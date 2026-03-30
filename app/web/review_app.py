"""Flask 极简审核 Web 界面"""

import json
import os
import concurrent.futures
import threading
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.query import (
    query_facts, get_fact_detail, get_stats, export_csv,
    get_documents, get_document, get_document_chunks,
    get_document_evidences, get_document_tasks, get_doc_stats,
    get_passed_facts_stats, cascade_delete_document, update_document_meta,
    get_graph_data, get_entity_list, get_entity_timeline, get_entity_overview,
    get_entity_hierarchy, get_entity_detail,
)
from app.web.api_tasks import api_tasks_bp

logger = get_logger(__name__)

# ── 后台处理线程池（LLM 调用为 I/O 密集，可安全并行；
#    SQLite WAL 模式支持并发读写）──
_max_workers = get_config().get("pipeline", {}).get("max_concurrent_docs", 4)
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=_max_workers, thread_name_prefix="doc-proc")

# ── 暂停控制：Event set = 运行中，clear = 暂停 ──
_pause_event = threading.Event()
_pause_event.set()  # 默认运行


def _process_one_doc(doc_id: str) -> None:
    """处理单个文档，带超时保护。在线程池中执行。"""
    from app.services.pipeline import process_document

    # 如果已暂停，等待恢复（每 2 秒检查一次）
    while not _pause_event.wait(timeout=2):
        pass

    try:
        process_document(doc_id)
    except Exception as e:
        logger.error("后台处理文档失败 [%s]: %s", doc_id[:8], e, exc_info=True)
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE source_document SET status='失败', error_message=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(e)[:500], doc_id),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

# 事实类型中文名映射
FACT_TYPE_NAMES = {
    "FINANCIAL_METRIC": "财务指标",
    "SALES_VOLUME": "销售量",
    "MARKET_SHARE": "市场份额",
    "CAPACITY": "产能",
    "COMPETITIVE_RANKING": "竞争排名",
    "INVESTMENT": "投资",
    "PRICE_CHANGE": "价格变动",
    "EXPANSION": "扩建",
    "COOPERATION": "合作",
}

# qualifier 字段值的中文映射（LLM 可能输出英文 key）
QUALIFIER_VALUE_ZH = {
    # cooperation_type
    "strategic_cooperation": "战略合作",
    "strategic_coop": "战略合作",
    "joint_venture": "合资合作",
    "jv": "合资合作",
    "research_cooperation": "科研合作",
    "research_coop": "科研合作",
    "research_partnership": "科研合作",
    "technical_cooperation": "技术合作",
    "tech_cooperation": "技术合作",
    "tech_coop": "技术合作",
    "supply_partnership": "供应合作",
    "supply_agreement": "供应合作",
    "co_development": "联合开发",
    "joint_development": "联合开发",
    "equity_investment": "股权投资",
    "equity_cooperation": "股权合作",
    "equity": "股权合作",
    "distribution": "销售合作",
    "sales_coop": "销售合作",
    "licensing": "许可授权",
    "franchise": "特许经营",
    "investment": "投资合作",
    "strategic_investment": "战略投资",
    "partnership": "合作共建",
    # phase（阶段）
    "planned": "规划中",
    "under_construction": "在建",
    "construction": "在建",
    "completed": "竣工",
    "completion": "竣工",
    "commissioned": "投产",
    "operation": "运营中",
    "phase_1": "一期",
    "phase1": "一期",
    "first_phase": "一期",
    "phase_2": "二期",
    "phase2": "二期",
    "second_phase": "二期",
    "phase_3": "三期",
    "phase3": "三期",
    # report_scope（统计口径）
    "consolidated": "合并口径",
    "parent_only": "母公司口径",
    "standalone": "独立口径",
    "group": "集团口径",
    # price_type
    "factory": "出厂价",
    "retail": "零售价",
    "wholesale": "批发价",
    "market": "市场价",
}


def _build_fact_summary(fact: dict) -> str:
    """为事实生成摘要文本，同时在 fact 上设置 context_tag 字段。
    - summary: 主谓宾+数值，不含限定词括号
    - context_tag: 最关键的一个限定词（≤12字），用于在模板中单独渲染为标签
    """
    # 解析 qualifiers
    quals = {}
    qj = fact.get("qualifier_json")
    if qj and isinstance(qj, str):
        try:
            quals = json.loads(qj)
        except (json.JSONDecodeError, TypeError):
            pass
    elif isinstance(qj, dict):
        quals = qj

    parts = []
    ft = fact.get("fact_type") or ""
    pred = fact.get("predicate") or ""
    obj = fact.get("object_text") or ""

    if pred:
        parts.append(pred)
    if obj:
        parts.append("→ " + obj)

    # 数值：优先使用 value_text（已格式化），否则用 value_num
    # 竞争排名特殊处理：谓词已含"第"时用整数避免"排名第 第9位"冗余
    vn = fact.get("value_num")
    vt = fact.get("value_text") or ""
    if ft == "COMPETITIVE_RANKING" and vn is not None and pred.endswith("第"):
        parts.append(str(int(vn)) if vn == int(vn) else str(vn))
    elif vt:
        parts.append(vt)
    elif vn is not None:
        val_str = str(fact["value_num"])
        if fact.get("unit"):
            val_str += " " + fact["unit"]
        if fact.get("currency"):
            val_str += f"({fact['currency']})"
        parts.append(val_str)

    # 提取 context_tag（不超过 12 个字）并设置到 fact 上
    _MAX = 12
    ctx = ""
    if ft == "COMPETITIVE_RANKING":
        rn = quals.get("ranking_name") or ""
        seg = quals.get("segment") or ""
        raw = rn or seg
        ctx = raw[:_MAX] + ("…" if len(raw) > _MAX else "")
    elif ft == "CAPACITY":
        raw = quals.get("product_type") or ""
        ctx = raw[:_MAX]
    elif ft == "COOPERATION":
        ct = quals.get("cooperation_type") or ""
        ctx = QUALIFIER_VALUE_ZH.get(ct, ct)[:_MAX]
    elif ft == "EXPANSION":
        raw = quals.get("project_name") or quals.get("scope") or ""
        ctx = raw[:_MAX] + ("…" if len(raw) > _MAX else "")
    elif ft == "FINANCIAL_METRIC":
        raw = quals.get("metric_name") or quals.get("segment") or ""
        ctx = raw[:_MAX]
    elif ft == "SALES_VOLUME":
        raw = quals.get("product_type") or quals.get("product_name") or ""
        ctx = raw[:_MAX]
    elif ft == "INVESTMENT":
        raw = quals.get("project_name") or quals.get("purpose") or ""
        ctx = raw[:_MAX] + ("…" if len(raw) > _MAX else "")
    elif ft == "MARKET_SHARE":
        raw = quals.get("market_scope") or quals.get("segment") or ""
        ctx = raw[:_MAX]
    elif ft == "PRICE_CHANGE":
        pt = quals.get("product_name") or quals.get("product_type") or ""
        prt = QUALIFIER_VALUE_ZH.get(quals.get("price_type") or "", "")
        ctx = (pt + ("·" + prt if prt else ""))[:_MAX]

    fact["context_tag"] = ctx
    return " ".join(parts) if parts else "—"


def create_app() -> Flask:
    cfg = get_config()
    web_cfg = cfg.get("web", {})

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = web_cfg.get("secret_key", "dev-secret-change-me")

    # 全局模板上下文：注入事实类型中文名映射
    ENTITY_TYPE_ZH = {
        'COMPANY': '企业', 'GROUP': '群体/排名', 'PROJECT': '项目',
        'REGION': '地区', 'PRODUCT': '产品', 'PERSON': '人物',
        'ORG': '机构', 'OTHER': '其他', 'UNKNOWN': '未知', 'COUNTRY': '国家/地区',
    }

    @app.context_processor
    def inject_globals():
        return dict(
            FACT_TYPE_NAMES=FACT_TYPE_NAMES,
            ENTITY_TYPE_ZH=ENTITY_TYPE_ZH,
            QUALIFIER_VALUE_ZH=QUALIFIER_VALUE_ZH,
        )

    @app.route("/")
    def index():
        stats = get_stats()
        entity_overview = get_entity_overview(top_n=10)
        return render_template("index.html", stats=stats, entity_overview=entity_overview)

    @app.route("/documents")
    def documents_list():
        docs = get_documents(limit=200)
        # 汇总统计
        summary = {
            "total": len(docs),
            "processed": sum(1 for d in docs if d["status"] == "已完成"),
            "failed": sum(1 for d in docs if d["status"] == "失败"),
            "processing": sum(1 for d in docs if d["status"] in ("处理中", "清洗中", "抽取中", "审核中", "链接中")),
            "total_facts": sum(d.get("fact_count") or 0 for d in docs),
            "total_evidences": sum(d.get("evidence_count") or 0 for d in docs),
            "total_cost": sum(d.get("cost") or 0 for d in docs),
            "tasks_failed": sum(d.get("fail_tasks") or 0 for d in docs),
        }
        # 按状态分组排序：failed 在前，processing 次之，processed 最后
        status_order = {"失败": 0, "处理中": 1, "清洗中": 1, "抽取中": 1, "审核中": 1, "链接中": 1, "已完成": 2, "内容为空": 3, "清洗后为空": 3}
        docs.sort(key=lambda d: (status_order.get(d["status"], 9), d.get("crawl_time") or ""), reverse=False)
        # failed 和 processing 排最前，processed 按时间倒序
        failed_docs = [d for d in docs if d["status"] == "失败"]
        processing_docs = [d for d in docs if d["status"] in ("处理中", "清洗中", "抽取中", "审核中", "链接中")]
        ok_docs = [d for d in docs if d["status"] not in ("失败", "处理中", "清洗中", "抽取中", "审核中", "链接中")]
        ok_docs.sort(key=lambda d: d.get("crawl_time") or "", reverse=True)
        sorted_docs = failed_docs + processing_docs + ok_docs
        return render_template("documents.html", documents=sorted_docs, summary=summary)

    @app.route("/documents/<doc_id>")
    def document_detail(doc_id):
        doc = get_document(doc_id)
        if not doc:
            return "Document not found", 404
        chunks = get_document_chunks(doc_id)
        evidences = get_document_evidences(doc_id)
        facts = query_facts(document_id=doc_id, limit=500)
        tasks = get_document_tasks(doc_id)
        doc_stats = get_doc_stats(doc_id)

        for f in facts:
            if f.get("qualifier_json"):
                try:
                    f["qualifiers_display"] = json.loads(f["qualifier_json"])
                except (json.JSONDecodeError, TypeError):
                    f["qualifiers_display"] = {}
            else:
                f["qualifiers_display"] = {}

        return render_template(
            "document.html",
            doc=doc,
            chunks=chunks,
            evidences=evidences,
            facts=facts,
            tasks=tasks,
            stats=doc_stats,
        )

    @app.route("/passed")
    def passed_list():
        """已通过结果展示页"""
        fact_type = request.args.get("fact_type", "")
        doc_id = request.args.get("doc_id", "")
        pass_type = request.args.get("pass_type", "")  # '' | '自动通过' | '人工通过'
        subject_q = request.args.get("subject_q", "").strip()
        page = int(request.args.get("page", 1))
        per_page = 30

        # 查询状态范围
        if pass_type in ("自动通过", "人工通过"):
            status_filter = pass_type
        else:
            status_filter = ""  # 由 query_facts 双状态处理

        # 若无单一状态筛选，分两次查询合并
        if not pass_type:
            facts_auto = query_facts(
                subject=subject_q,
                fact_type=fact_type, review_status="自动通过",
                document_id=doc_id, limit=per_page * 10,
            )
            facts_human = query_facts(
                subject=subject_q,
                fact_type=fact_type, review_status="人工通过",
                document_id=doc_id, limit=per_page * 10,
            )
            all_facts = facts_auto + facts_human
            all_facts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        else:
            all_facts = query_facts(
                subject=subject_q,
                fact_type=fact_type, review_status=status_filter,
                document_id=doc_id, limit=per_page * 10,
            )

        total_count = len(all_facts)
        start = (page - 1) * per_page
        facts = all_facts[start: start + per_page]
        has_more = (start + per_page) < total_count

        # 解析 qualifier_json
        for f in facts:
            if f.get("qualifier_json"):
                try:
                    f["qualifiers_display"] = json.loads(f["qualifier_json"])
                except (json.JSONDecodeError, TypeError):
                    f["qualifiers_display"] = {}
            else:
                f["qualifiers_display"] = {}

        stats_data = get_passed_facts_stats(fact_type=fact_type, document_id=doc_id, pass_type=pass_type)
        all_docs = get_documents(limit=200)
        valid_types = cfg.get("fact_types", [])

        return render_template(
            "passed.html",
            facts=facts,
            fact_types=valid_types,
            all_docs=all_docs,
            current_type=fact_type,
            current_doc=doc_id,
            current_pass_type=pass_type,
            current_subject_q=subject_q,
            page=page,
            has_more=has_more,
            total_count=stats_data["total"],
            auto_pass_count=stats_data["auto_pass"],
            human_pass_count=stats_data["human_pass"],
            type_dist=stats_data["type_dist"],
        )

    @app.route("/review")
    def review_list():
        """审核列表页"""
        fact_type = request.args.get("fact_type", "")
        status = request.args.get("status", "待人工审核")
        page = int(request.args.get("page", 1))
        per_page = 20

        facts = query_facts(
            fact_type=fact_type,
            review_status=status,
            limit=per_page + 1,
            offset=(page - 1) * per_page,
        )

        has_more = len(facts) > per_page
        if has_more:
            facts = facts[:per_page]

        # 解析 qualifier_json 为 dict 用于展示
        for f in facts:
            if f.get("qualifier_json"):
                try:
                    f["qualifiers_display"] = json.loads(f["qualifier_json"])
                except (json.JSONDecodeError, TypeError):
                    f["qualifiers_display"] = {}
            else:
                f["qualifiers_display"] = {}

        valid_types = cfg.get("fact_types", [])
        statuses = [
            "待人工审核", "自动通过", "待处理",
            "已拒绝", "人工通过", "人工拒绝", "不确定",
        ]

        return render_template(
            "review.html",
            facts=facts,
            fact_types=valid_types,
            statuses=statuses,
            current_type=fact_type,
            current_status=status,
            page=page,
            has_more=has_more,
        )

    @app.route("/review/<fact_id>", methods=["GET"])
    def review_detail(fact_id):
        """审核详情 API"""
        detail = get_fact_detail(fact_id)
        if not detail:
            return jsonify({"error": "not found"}), 404
        return jsonify(detail)

    @app.route("/review/<fact_id>/action", methods=["POST"])
    def review_action(fact_id):
        """执行审核操作"""
        action = request.form.get("action", "")
        note = request.form.get("note", "")

        if action not in ("人工通过", "人工拒绝"):
            return jsonify({"error": "invalid action"}), 400

        conn = get_connection()
        try:
            # 获取旧状态
            old = conn.execute(
                "SELECT review_status FROM fact_atom WHERE id=?", (fact_id,)
            ).fetchone()
            old_status = old["review_status"] if old else "UNKNOWN"

            # 更新 fact_atom
            conn.execute(
                """UPDATE fact_atom SET
                review_status=?, review_note=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (action, note, fact_id),
            )

            # 写入 review_log
            import uuid
            conn.execute(
                """INSERT INTO review_log
                (id, target_type, target_id, old_status, new_status,
                 reviewer, review_action, review_note)
                VALUES (?, 'fact_atom', ?, ?, ?, '人工', ?, ?)""",
                (str(uuid.uuid4()), fact_id, old_status, action, action.lower(), note),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("人工审核 [%s]: %s → %s", fact_id[:8], old_status, action)
        return redirect(url_for("review_list"))

    @app.route("/review/<fact_id>/edit", methods=["POST"])
    def review_edit(fact_id):
        """人工编辑事实原子字段"""
        data = request.get_json(silent=True) or {}
        action = data.get("action", "save_only")  # save_only | save_and_pass | save_and_reject

        allowed_fields = [
            "subject_text", "predicate", "object_text",
            "value_num", "value_text", "unit", "currency",
            "time_expr", "location_text", "qualifier_json", "review_note",
        ]

        update_vals = {}
        for field in allowed_fields:
            if field in data:
                val = data[field]
                if isinstance(val, str) and val.strip() == "":
                    val = None
                update_vals[field] = val

        if action == "save_and_pass":
            new_status = "人工通过"
        elif action == "save_and_reject":
            new_status = "人工拒绝"
        else:
            new_status = "待人工审核"

        conn = get_connection()
        try:
            old = conn.execute(
                "SELECT review_status FROM fact_atom WHERE id=?", (fact_id,)
            ).fetchone()
            if not old:
                return jsonify({"error": "not found"}), 404
            old_status = old["review_status"]

            update_vals["review_status"] = new_status
            set_clause = (
                ", ".join(f"{k}=?" for k in update_vals)
                + ", updated_at=CURRENT_TIMESTAMP"
            )
            values = list(update_vals.values()) + [fact_id]
            conn.execute(f"UPDATE fact_atom SET {set_clause} WHERE id=?", values)

            import uuid
            conn.execute(
                """INSERT INTO review_log
                (id, target_type, target_id, old_status, new_status,
                 reviewer, review_action, review_note)
                VALUES (?, 'fact_atom', ?, ?, ?, '人工', '人工编辑', ?)""",
                (
                    str(uuid.uuid4()), fact_id, old_status, new_status,
                    update_vals.get("review_note") or "",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        logger.info("人工编辑 [%s]: %s → %s", fact_id[:8], old_status, new_status)
        return jsonify({"ok": True, "new_status": new_status})

    @app.route("/stats")
    def stats_page():
        """统计概览 API"""
        data = get_stats()
        return jsonify(data)

    @app.route("/export")
    def export_page():
        """导出 CSV"""
        fact_type = request.args.get("fact_type", "")
        status = request.args.get("status", "")

        facts = query_facts(
            fact_type=fact_type,
            review_status=status,
            limit=10000,
        )

        csv_text = export_csv(facts)

        from flask import Response
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=facts_export.csv"},
        )

    # ─────────────────────── 导入任务 ───────────────────────

    @app.route("/import", methods=["GET"])
    def import_page():
        """导入任务入口页"""
        return render_template("import.html")

    @app.route("/import/paste", methods=["POST"])
    def import_paste_action():
        """粘贴文本导入"""
        from app.services.importer import import_paste
        title = request.form.get("title", "").strip()
        text = request.form.get("text", "").strip()
        author = request.form.get("author", "").strip() or None
        publish_time = request.form.get("publish_time", "").strip() or None
        auto_process = request.form.get("auto_process") == "1"

        if not title or not text:
            return jsonify({"error": "标题和正文不能为空"}), 400

        doc_id = import_paste(text=text, title=title, author=author, publish_time=publish_time)

        if auto_process:
            _start_background_process([doc_id])

        return jsonify({"success": True, "document_id": doc_id, "processing": auto_process})

    @app.route("/import/file", methods=["POST"])
    def import_file_action():
        """文件上传导入（支持多文件）"""
        from app.services.importer import import_paste
        files = request.files.getlist("files")
        author = request.form.get("author", "").strip() or None
        publish_time = request.form.get("publish_time", "").strip() or None
        auto_process = request.form.get("auto_process") == "1"

        if not files or all(f.filename == "" for f in files):
            return jsonify({"error": "请选择至少一个文件"}), 400

        doc_ids = []
        errors = []
        supported = (".txt", ".md")

        for f in files:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in supported:
                errors.append(f"{f.filename}: 不支持的文件类型 {ext}")
                continue

            try:
                raw_text = f.read().decode("utf-8")
                title = os.path.splitext(f.filename)[0]
                doc_id = import_paste(text=raw_text, title=title, author=author, publish_time=publish_time)
                doc_ids.append(doc_id)
            except Exception as e:
                errors.append(f"{f.filename}: {e}")

        if auto_process and doc_ids:
            _start_background_process(doc_ids)

        return jsonify({
            "success": len(doc_ids) > 0,
            "imported": len(doc_ids),
            "errors": errors,
            "document_ids": doc_ids,
            "processing": auto_process and len(doc_ids) > 0,
        })

    @app.route("/import/url", methods=["POST"])
    def import_url_action():
        """URL 抓取导入"""
        from app.services.importer import import_url
        url = request.form.get("url", "").strip()
        title = request.form.get("title", "").strip() or None
        author = request.form.get("author", "").strip() or None
        publish_time = request.form.get("publish_time", "").strip() or None
        auto_process = request.form.get("auto_process") == "1"

        if not url:
            return jsonify({"error": "URL 不能为空"}), 400

        try:
            doc_id = import_url(url=url, title=title, author=author, publish_time=publish_time)
        except Exception as e:
            return jsonify({"error": f"抓取失败: {e}"}), 400

        if auto_process:
            _start_background_process([doc_id])

        return jsonify({"success": True, "document_id": doc_id, "processing": auto_process})

    # ─────────────────────── 文章管理 ───────────────────────

    @app.route("/manage")
    def manage_page():
        """文章管理页"""
        docs = get_documents(limit=500)
        return render_template("manage.html", documents=docs)

    @app.route("/manage/<doc_id>/edit", methods=["POST"])
    def manage_edit(doc_id):
        """修改文档元信息"""
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip() or None
        source_name = request.form.get("source_name", "").strip() or None
        publish_time = request.form.get("publish_time", "").strip() or None

        if not title:
            return jsonify({"error": "标题不能为空"}), 400

        ok = update_document_meta(doc_id, title, author, source_name, publish_time)
        if not ok:
            return jsonify({"error": "文档不存在"}), 404
        return jsonify({"success": True})

    @app.route("/manage/delete", methods=["POST"])
    def manage_delete():
        """级联删除文档（支持批量）"""
        data = request.get_json(silent=True)
        if not data or not data.get("doc_ids"):
            return jsonify({"error": "未指定文档 ID"}), 400

        doc_ids = data["doc_ids"]
        if not isinstance(doc_ids, list):
            return jsonify({"error": "doc_ids 必须为数组"}), 400

        results = []
        for doc_id in doc_ids:
            try:
                stats = cascade_delete_document(doc_id)
                results.append({"doc_id": doc_id, "deleted": stats})
            except Exception as e:
                logger.error("删除文档失败 [%s]: %s", doc_id[:8], e)
                results.append({"doc_id": doc_id, "error": str(e)})

        return jsonify({"success": True, "results": results})

    @app.route("/manage/<doc_id>/reprocess", methods=["POST"])
    def manage_reprocess(doc_id):
        """重新处理文档：先删除旧的处理结果，再重新运行三 Agent 链路"""
        doc = get_document(doc_id)
        if not doc:
            return jsonify({"error": "文档不存在"}), 404

        # 删除旧的处理数据（保留 source_document）
        from app.services.query import clear_document_results
        clear_document_results(doc_id)
        conn = get_connection()
        try:
            conn.execute("UPDATE source_document SET status='待处理' WHERE id=?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

        _start_background_process([doc_id])
        return jsonify({"success": True, "processing": True})

    # ─────────────────────── 实体合并 API ───────────────────────

    @app.route("/api/entity/merge-suggestions")
    def api_entity_merge_suggestions():
        """返回 AI 推荐的实体合并建议（基于文本相似度启发式，向后兼容）"""
        from app.services.entity_merger import get_merge_suggestions
        suggestions = get_merge_suggestions()
        return jsonify({"suggestions": suggestions})

    @app.route("/api/entity/merge", methods=["POST"])
    def api_entity_merge():
        """执行实体合并：secondary → primary（secondary 变为 primary 的 alias）"""
        data = request.get_json(silent=True) or {}
        primary_id = data.get("primary_id", "").strip()
        secondary_id = data.get("secondary_id", "").strip()
        if not primary_id or not secondary_id:
            return jsonify({"error": "primary_id 和 secondary_id 均为必填"}), 400
        if primary_id == secondary_id:
            return jsonify({"error": "不能合并同一实体"}), 400
        from app.services.entity_merger import merge_entities
        try:
            result = merge_entities(primary_id, secondary_id)
            return jsonify({"success": True, "result": result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("实体合并失败 %s→%s: %s", secondary_id[:8], primary_id[:8], e)
            return jsonify({"error": "合并失败，请查看日志"}), 500

    @app.route("/api/entity/detail")
    def api_entity_detail_batch():
        """批量获取实体详情（供合并建议面板使用）"""
        ids = request.args.get("ids", "")
        if not ids:
            return jsonify([])
        id_list = [i.strip() for i in ids.split(",") if i.strip()]
        conn = get_connection()
        try:
            placeholders = ",".join(["?"] * len(id_list))
            rows = conn.execute(
                f"""SELECT e.id, e.canonical_name, e.entity_type,
                           COUNT(f.id) AS fact_count
                    FROM entity e
                    LEFT JOIN fact_atom f ON f.subject_entity_id = e.id
                      AND f.review_status IN ('自动通过','人工通过')
                    WHERE e.id IN ({placeholders})
                    GROUP BY e.id""",
                id_list,
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    # ─────────────── 规则+LLM 合并任务 API ───────────────

    @app.route("/api/entity/merge-tasks")
    def api_merge_task_list():
        """返回合并任务列表，支持 ?status=pending|all|rejected|executed"""
        from app.services.entity_merger import get_pending_merge_tasks, get_merge_task_stats
        status = request.args.get("status", "待处理")
        tasks = get_pending_merge_tasks(status=status)
        stats = get_merge_task_stats()
        return jsonify({"tasks": tasks, "stats": stats})

    @app.route("/api/entity/merge-tasks/generate", methods=["POST"])
    def api_merge_task_generate():
        """触发规则+LLM 分析，生成合并任务；max_llm 参数控制本次 LLM 调用上限"""
        from app.services.entity_merger import generate_merge_tasks
        data = request.get_json(silent=True) or {}
        max_llm = min(int(data.get("max_llm", 20)), 50)   # 安全上限 50
        try:
            result = generate_merge_tasks(max_llm_calls=max_llm)
            return jsonify({"success": True, **result})
        except Exception as e:
            logger.error("生成合并任务失败: %s", e)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/entity/merge-task/<task_id>/approve", methods=["POST"])
    def api_merge_task_approve(task_id: str):
        """批准合并任务，执行合并"""
        from app.services.entity_merger import approve_task
        try:
            result = approve_task(task_id)
            return jsonify({"success": True, "result": result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("执行合并任务失败 %s: %s", task_id[:8], e)
            return jsonify({"error": "合并失败，请查看日志"}), 500

    @app.route("/api/entity/merge-task/<task_id>/reject", methods=["POST"])
    def api_merge_task_reject(task_id: str):
        """拒绝合并任务"""
        from app.services.entity_merger import reject_task
        try:
            reject_task(task_id)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/entity/merge-task/<task_id>/swap-approve", methods=["POST"])
    def api_merge_task_swap_approve(task_id: str):
        """交换主从后执行合并"""
        from app.services.entity_merger import swap_and_approve_task
        try:
            result = swap_and_approve_task(task_id)
            return jsonify({"success": True, "result": result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("交换合并任务失败 %s: %s", task_id[:8], e)
            return jsonify({"error": "合并失败，请查看日志"}), 500

    # ─────────────── 实体关联分析 API ───────────────

    @app.route("/api/entity/analyze", methods=["POST"])
    def api_entity_analyze():
        """分析单个实体，生成关联建议（写入 entity_relation_suggestion 表）"""
        from app.services.entity_analyzer import analyze_entity
        from app.services.llm_client import LLMClient
        data = request.get_json(silent=True) or {}
        entity_id = str(data.get("entity_id", "")).strip()
        if not entity_id:
            return jsonify({"error": "entity_id 不能为空"}), 400
        use_llm = bool(data.get("use_llm", True))
        try:
            llm = LLMClient(cfg) if use_llm else None
            result = analyze_entity(entity_id, llm_client=llm)
            return jsonify({"success": True, **result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("实体分析失败 %s: %s", entity_id, e)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/entity/analyze-with-search", methods=["POST"])
    def api_entity_analyze_with_search():
        """分析单个实体 + 网络搜索（置信度达标时自动入库）"""
        from app.services.entity_analyzer import analyze_entity
        from app.services.llm_client import LLMClient
        data = request.get_json(silent=True) or {}
        entity_id = str(data.get("entity_id", "")).strip()
        if not entity_id:
            return jsonify({"error": "entity_id 不能为空"}), 400
        use_llm = bool(data.get("use_llm", True))
        try:
            llm = LLMClient(cfg) if use_llm else None
            result = analyze_entity(entity_id, llm_client=llm, use_web_search=True)
            return jsonify({"success": True, **result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("实体网络搜索分析失败 %s: %s", entity_id, e)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/entity/search-cache")
    def api_entity_search_cache():
        """返回网络搜索缓存统计 + 最近 20 条记录"""
        from app.services.web_searcher import get_cache_stats
        from app.models.db import get_connection as _gc
        conn = _gc()
        try:
            stats = get_cache_stats(conn)
            rows = conn.execute(
                """SELECT entity_name, query, search_source, summary_text,
                          SUBSTR(created_at, 1, 16) AS created_at
                   FROM entity_search_cache
                   ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
            entries = [dict(r) for r in rows]
        finally:
            conn.close()
        return jsonify({"stats": stats, "recent": entries})

    @app.route("/api/entity/analyze-batch", methods=["POST"])
    def api_entity_analyze_batch():
        """批量分析多个实体（按事实数量降序取 top N）"""
        from app.services.entity_analyzer import analyze_entity
        from app.services.llm_client import LLMClient
        from app.models.db import get_connection as _gc
        data = request.get_json(silent=True) or {}
        limit = min(int(data.get("limit", 10)), 30)   # 安全上限 30
        use_llm = bool(data.get("use_llm", False))     # 批量默认不调 LLM，避免费用过高
        conn = _gc()
        try:
            rows = conn.execute(
                """SELECT e.id
                   FROM entity e
                   LEFT JOIN fact_atom f ON f.subject_entity_id = e.id
                     AND f.review_status IN ('自动通过','人工通过')
                   GROUP BY e.id
                   ORDER BY COUNT(f.id) DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            entity_ids = [r["id"] for r in rows]
        finally:
            conn.close()

        llm = LLMClient(cfg) if use_llm else None
        results = []
        for eid in entity_ids:
            try:
                r = analyze_entity(eid, llm_client=llm)
                results.append(r)
            except Exception as exc:
                logger.warning("批量分析跳过 %s: %s", eid, exc)
        return jsonify({"success": True, "analyzed": len(results), "results": results})

    @app.route("/api/entity/relation-suggestions")
    def api_relation_suggestions():
        """查询实体关联建议列表"""
        from app.services.entity_analyzer import get_suggestions
        entity_id = request.args.get("entity_id", "") or None
        status = request.args.get("status", "待处理")
        limit = min(int(request.args.get("limit", 100)), 500)
        suggestions = get_suggestions(entity_id=entity_id, status=status, limit=limit)
        return jsonify({"suggestions": suggestions})

    @app.route("/api/entity/relation-suggestion/<suggestion_id>/confirm", methods=["POST"])
    def api_relation_suggestion_confirm(suggestion_id: str):
        """确认建议：建立关系 / 别名 / 发起合并任务"""
        from app.services.entity_analyzer import confirm_suggestion
        try:
            result = confirm_suggestion(suggestion_id)
            return jsonify({"success": True, **result})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error("确认建议失败 %s: %s", suggestion_id, e)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/entity/relation-suggestion/<suggestion_id>/reject", methods=["POST"])
    def api_relation_suggestion_reject(suggestion_id: str):
        """拒绝建议"""
        from app.services.entity_analyzer import reject_suggestion
        try:
            reject_suggestion(suggestion_id)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ─────────────────────── 知识图谱 & 时间轴 ───────────────────────

    # ─────────── 主体知识库管理 API ───────────

    @app.route("/api/entity/knowledge-base")
    def api_knowledge_base():
        """返回所有实体、别名、关系（主体管理 Tab 使用）"""
        conn = get_connection()
        try:
            entities = conn.execute(
                """SELECT e.id, e.canonical_name, e.entity_type,
                          COUNT(DISTINCT f_linked.id) AS linked_fact_count,
                          COUNT(DISTINCT f_text.id)   AS text_fact_count
                   FROM entity e
                   LEFT JOIN fact_atom f_linked
                     ON f_linked.subject_entity_id = e.id
                     AND f_linked.review_status IN ('自动通过','人工通过')
                   LEFT JOIN entity_alias ea ON ea.entity_id = e.id
                   LEFT JOIN fact_atom f_text
                     ON f_text.subject_entity_id IS NULL
                     AND f_text.review_status IN ('自动通过','人工通过')
                     AND (f_text.subject_text = e.canonical_name
                          OR f_text.subject_text = ea.alias_name)
                   GROUP BY e.id
                   ORDER BY e.canonical_name"""
            ).fetchall()
            aliases = conn.execute(
                "SELECT id, entity_id, alias_name FROM entity_alias"
            ).fetchall()
            relations = conn.execute(
                """SELECT r.id, r.from_entity_id, ef.canonical_name AS from_name,
                          r.to_entity_id, et.canonical_name AS to_name,
                          r.relation_type, r.detail_json, r.source
                   FROM entity_relation r
                   JOIN entity ef ON r.from_entity_id = ef.id
                   JOIN entity et ON r.to_entity_id = et.id
                   ORDER BY r.created_at DESC"""
            ).fetchall()
            return jsonify({
                "entities": [dict(e) for e in entities],
                "aliases": [dict(a) for a in aliases],
                "relations": [dict(r) for r in relations],
            })
        finally:
            conn.close()

    @app.route("/api/entity/add", methods=["POST"])
    def api_entity_add():
        """手动添加实体"""
        from app.services.entity_linker import add_entity
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        entity_type = data.get("entity_type", "COMPANY").strip()
        if not name:
            return jsonify({"error": "实体名称不能为空"}), 400
        eid = add_entity(name, entity_type)
        return jsonify({"success": True, "entity_id": eid})

    @app.route("/api/entity/<entity_id>/alias", methods=["POST"])
    def api_entity_add_alias(entity_id):
        """为实体添加别名"""
        from app.services.entity_linker import add_alias
        data = request.get_json(silent=True) or {}
        alias = data.get("alias", "").strip()
        if not alias:
            return jsonify({"error": "别名不能为空"}), 400
        try:
            add_alias(entity_id, alias)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/entity/relation/add", methods=["POST"])
    def api_entity_relation_add():
        """添加实体关系"""
        from app.services.entity_linker import add_entity_relation
        data = request.get_json(silent=True) or {}
        from_id = data.get("from_entity_id", "").strip()
        to_id = data.get("to_entity_id", "").strip()
        rel_type = data.get("relation_type", "").strip()
        detail_json = json.dumps(data.get("details", {}), ensure_ascii=False)
        if not from_id or not to_id or not rel_type:
            return jsonify({"error": "from_entity_id, to_entity_id, relation_type 均为必填"}), 400
        try:
            rel_id = add_entity_relation(from_id, to_id, rel_type, detail_json)
            return jsonify({"success": True, "relation_id": rel_id})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/entity/relation/<rel_id>/remove", methods=["POST"])
    def api_entity_relation_remove(rel_id):
        """删除实体关系"""
        from app.services.entity_linker import remove_entity_relation
        ok = remove_entity_relation(rel_id)
        if not ok:
            return jsonify({"error": "关系不存在"}), 404
        return jsonify({"success": True})

    @app.route("/api/entity/candidate-relations")
    def api_candidate_relations():
        """从已通过事实反向提取候选关系"""
        from app.services.entity_linker import get_candidate_relations_from_facts
        candidates = get_candidate_relations_from_facts()
        return jsonify({"candidates": candidates})

    @app.route("/api/entity/profile")
    def api_entity_profile():
        """获取实体档案"""
        from app.services.entity_profiler import get_entity_profile
        entity_id = request.args.get("entity_id", "").strip()
        if not entity_id:
            return jsonify({"error": "缺少 entity_id"}), 400
        profile = get_entity_profile(entity_id)
        if not profile:
            return jsonify({"error": "实体不存在"}), 404
        return jsonify({"profile": profile})

    @app.route("/api/entity/profile/build", methods=["POST"])
    def api_entity_profile_build():
        """构建或重建实体档案（纯聚合，无 LLM）"""
        from app.services.entity_profiler import build_entity_profile
        data = request.get_json(silent=True) or {}
        entity_id = data.get("entity_id", "").strip()
        if not entity_id:
            return jsonify({"error": "缺少 entity_id"}), 400
        profile = build_entity_profile(entity_id)
        if not profile:
            return jsonify({"error": "实体不存在或构建失败"}), 404
        return jsonify({"success": True, "profile": profile})

    @app.route("/api/entity/profile/enrich", methods=["POST"])
    def api_entity_profile_enrich():
        """Web 搜索 + LLM 丰富实体档案"""
        from app.services.entity_profiler import enrich_entity_profile
        data = request.get_json(silent=True) or {}
        entity_id = data.get("entity_id", "").strip()
        if not entity_id:
            return jsonify({"error": "缺少 entity_id"}), 400
        result = enrich_entity_profile(entity_id)
        if not result:
            return jsonify({"error": "实体不存在或丰富失败"}), 404
        return jsonify({"success": True, "result": result})

    @app.route("/api/entity/profile/build-all", methods=["POST"])
    def api_entity_profile_build_all():
        """批量构建所有实体档案"""
        from app.services.entity_profiler import build_all_profiles
        data = request.get_json(silent=True) or {}
        min_facts = int(data.get("min_facts", 1))
        stats = build_all_profiles(min_facts=min_facts)
        return jsonify({"success": True, "stats": stats})

    @app.route("/api/entity/ai-suggest-relations", methods=["POST"])
    def api_ai_suggest_relations():
        """调用 AI 分析所有实体，给出关系建议"""
        from app.services.entity_linker import ai_suggest_relations
        data = request.get_json(silent=True) or {}
        hint = str(data.get("hint", "")).strip()
        try:
            suggestions = ai_suggest_relations(hint=hint)
            return jsonify({"suggestions": suggestions})
        except Exception as exc:
            return jsonify({"error": str(exc), "suggestions": []}), 500

    @app.route("/graph")
    def graph_page():
        """知识图谱可视化页面"""
        valid_types = cfg.get("fact_types", [])
        return render_template("graph.html", fact_types=valid_types)

    @app.route("/hierarchy")
    def hierarchy_page():
        """实体层级视图页面"""
        return render_template("hierarchy.html")

    @app.route("/api/graph")
    def api_graph():
        """图谱数据 API"""
        fact_type = request.args.get("fact_type", "")
        doc_id = request.args.get("doc_id", "")
        data = get_graph_data(fact_type=fact_type, doc_id=doc_id)
        return jsonify(data)

    @app.route("/api/entities")
    def api_entities():
        """实体列表搜索 API"""
        search = request.args.get("search", "")
        entity_type = request.args.get("entity_type", "")
        entities = get_entity_list(search=search, entity_type=entity_type)
        return jsonify({"entities": entities})

    @app.route("/api/entity/hierarchy")
    def api_entity_hierarchy():
        """实体层级树数据 API"""
        data = get_entity_hierarchy()
        return jsonify(data)

    @app.route("/api/dedup/search")
    def api_dedup_search():
        """
        手动去重搜索：返回已通过事实中所有匹配关键词的 subject_text / object_text，
        以及标准化实体表中匹配的实体。
        已绑定实体的 subject_text 不再作为独立条目输出（避免与实体重复）。
        """
        kw = request.args.get("q", "").strip()
        if not kw:
            return jsonify({"items": []})
        conn = get_connection()
        try:
            pattern = f"%{kw}%"
            # 标准化实体
            ent_rows = conn.execute(
                """SELECT e.id AS entity_id, e.canonical_name AS text_val,
                          e.entity_type,
                          COUNT(DISTINCT f.id) AS cnt
                   FROM entity e
                   LEFT JOIN fact_atom f ON f.subject_entity_id = e.id
                     AND f.review_status IN ('自动通过','人工通过')
                   WHERE e.canonical_name LIKE ?
                   GROUP BY e.id ORDER BY cnt DESC LIMIT 20""",
                (pattern,),
            ).fetchall()
            # 也搜索别名表
            alias_rows = conn.execute(
                """SELECT ea.entity_id, e.canonical_name AS text_val,
                          e.entity_type,
                          COUNT(DISTINCT f.id) AS cnt
                   FROM entity_alias ea
                   JOIN entity e ON ea.entity_id = e.id
                   LEFT JOIN fact_atom f ON f.subject_entity_id = e.id
                     AND f.review_status IN ('自动通过','人工通过')
                   WHERE ea.alias_name LIKE ?
                   GROUP BY ea.entity_id ORDER BY cnt DESC LIMIT 10""",
                (pattern,),
            ).fetchall()

            seen = set()  # 实体 ID 集合
            seen_text = set()  # 文本集合（同名不同类型的实体只显示一条）
            items = []
            for r in ent_rows:
                if r["text_val"] in seen_text:
                    # 同名已存在，称位合并迮安提示而非重复输出
                    continue
                seen.add(r["entity_id"])
                seen_text.add(r["text_val"])
                items.append({"text": r["text_val"], "fact_count": r["cnt"],
                               "entity_id": r["entity_id"], "entity_type": r["entity_type"],
                               "source": "entity"})
            for r in alias_rows:
                if r["entity_id"] not in seen:
                    seen.add(r["entity_id"])
                    seen_text.add(r["text_val"])
                    items.append({"text": r["text_val"], "fact_count": r["cnt"],
                                   "entity_id": r["entity_id"], "entity_type": r["entity_type"],
                                   "source": "entity"})

            # 从事实的文本字段搜集候选（只返回未绑定实体的文本）
            subj_rows = conn.execute(
                """SELECT subject_text AS text_val, COUNT(*) AS cnt
                   FROM fact_atom
                   WHERE review_status IN ('自动通过','人工通过')
                     AND subject_text LIKE ? AND subject_text IS NOT NULL
                     AND subject_entity_id IS NULL
                   GROUP BY subject_text ORDER BY cnt DESC LIMIT 50""",
                (pattern,),
            ).fetchall()
            obj_rows = conn.execute(
                """SELECT object_text AS text_val, COUNT(*) AS cnt
                   FROM fact_atom
                   WHERE review_status IN ('自动通过','人工通过')
                     AND object_text LIKE ? AND object_text IS NOT NULL
                     AND object_entity_id IS NULL
                   GROUP BY object_text ORDER BY cnt DESC LIMIT 30""",
                (pattern,),
            ).fetchall()

            for r in subj_rows:
                key = r["text_val"]
                if key not in seen_text:
                    seen_text.add(key)
                    items.append({"text": r["text_val"], "fact_count": r["cnt"],
                                  "entity_id": None, "entity_type": None,
                                  "source": "subject_text"})
            for r in obj_rows:
                key = r["text_val"]
                if key not in seen_text:
                    seen_text.add(key)
                    items.append({"text": r["text_val"], "fact_count": r["cnt"],
                                  "entity_id": None, "entity_type": None,
                                  "source": "object_text"})
            return jsonify({"items": items})
        finally:
            conn.close()

    @app.route("/api/dedup/batch-rename", methods=["POST"])
    def api_dedup_batch_rename():
        """
        批量将多个文本归一化到同一个标准实体名称。
        业务逻辑委托给 entity_merger.dedup_batch_rename()。

        请求体 JSON:
          {
            "texts": ["EntityA", "EntityA Ltd"],
            "canonical_name": "EntityA Co.",
            "entity_type": "COMPANY",
            "entity_id": "optional-existing-entity-uuid"
          }
        """
        from app.services.entity_merger import dedup_batch_rename

        data = request.get_json(silent=True) or {}
        texts = [t.strip() for t in (data.get("texts") or []) if t.strip()]
        canonical = data.get("canonical_name", "").strip()
        entity_type = data.get("entity_type", "COMPANY").strip()
        existing_eid = data.get("entity_id", "").strip()

        if not texts:
            return jsonify({"error": "texts 不能为空"}), 400
        if not canonical:
            return jsonify({"error": "canonical_name 不能为空"}), 400

        try:
            result = dedup_batch_rename(
                texts=texts,
                canonical_name=canonical,
                entity_type=entity_type,
                existing_entity_id=existing_eid,
            )
            return jsonify({"success": True, **result})
        except Exception as exc:
            logger.error("batch_rename failed: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/dedup/clusters")
    def api_dedup_clusters():
        """返回自动检测到的相似名称聚类，供"实体质量概览"使用。
        只返回成员数 >= 2 的簇，按最大事实数降序排列。
        """
        conn = get_connection()
        try:
            # 从已通过事实取高频主体 (min_count >= 1)
            subj_rows = conn.execute(
                """SELECT subject_text AS text_val, COUNT(*) AS cnt
                   FROM fact_atom
                   WHERE review_status IN ('自动通过','人工通过')
                     AND subject_text IS NOT NULL AND subject_text != ''
                   GROUP BY subject_text
                   HAVING cnt >= 1
                   ORDER BY cnt DESC LIMIT 300"""
            ).fetchall()
            # 知识库实体
            ent_rows = conn.execute(
                """SELECT e.id AS entity_id, e.canonical_name AS text_val,
                          e.entity_type,
                          COUNT(DISTINCT f.id) AS cnt
                   FROM entity e
                   LEFT JOIN fact_atom f ON f.subject_entity_id = e.id
                     AND f.review_status IN ('自动通过','人工通过')
                   GROUP BY e.id"""
            ).fetchall()
        finally:
            conn.close()

        seen = {}  # text -> item
        for r in ent_rows:
            seen[r["text_val"]] = {
                "text": r["text_val"],
                "fact_count": r["cnt"] or 0,
                "entity_id": r["entity_id"],
                "entity_type": r["entity_type"],
                "source": "entity",
            }
        for r in subj_rows:
            if r["text_val"] not in seen:
                seen[r["text_val"]] = {
                    "text": r["text_val"],
                    "fact_count": r["cnt"],
                    "entity_id": None,
                    "entity_type": None,
                    "source": "subject_text",
                }
        items = list(seen.values())

        # ── 并查集聚类（与前端 _clusterDedup 逻辑一致） ──
        GEO_SET = {
            "香港", "青岛", "上海", "北京", "天津", "广州", "深圳", "南京", "成都",
            "宁波", "武汉", "苏州", "张家港", "常州", "无锡", "重庆", "济南", "杭州", "厦门",
        }

        def _norm(s):
            import re
            return re.sub(r"[（(）)\s]", "", s).lower()

        def _geo_in_bracket(s):
            import re
            m = re.search(r"[（(]([^）)]+)[）)]", s)
            if not m:
                return None
            c = m.group(1).strip()
            return c if c in GEO_SET else None

        def _sim(a, b):
            na, nb = _norm(a), _norm(b)
            if not na or not nb:
                return 0.0
            if na == nb:
                return 1.0
            shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
            if longer.startswith(shorter) or longer.endswith(shorter) or shorter in longer:
                score_con = 0.5 + len(shorter) / len(longer) * 0.45
            else:
                score_con = 0.0
            # LCS ratio
            if len(na) <= 30 and len(nb) <= 30:
                m_len, n_len = len(na), len(nb)
                dp = [[0] * (n_len + 1) for _ in range(m_len + 1)]
                for i in range(1, m_len + 1):
                    for j in range(1, n_len + 1):
                        dp[i][j] = dp[i-1][j-1] + 1 if na[i-1] == nb[j-1] else max(dp[i-1][j], dp[i][j-1])
                score_lcs = dp[m_len][n_len] / max(m_len, n_len)
            else:
                score_lcs = 0.0
            return max(score_con, score_lcs)

        threshold = 0.68
        n = len(items)
        par = list(range(n))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        scores = {}
        for i in range(n):
            for j in range(i + 1, n):
                a, b = items[i]["text"], items[j]["text"]
                ga, gb = _geo_in_bracket(a), _geo_in_bracket(b)
                if ga and gb and ga != gb:
                    continue  # 地区不同，跳过
                s = _sim(a, b)
                scores[(i, j)] = s
                if s >= threshold:
                    pi, pj = find(i), find(j)
                    if pi != pj:
                        par[pj] = pi

        # 组装簇
        from collections import defaultdict
        groups = defaultdict(list)
        for i in range(n):
            groups[find(i)].append(i)

        clusters = []
        for root, members in groups.items():
            if len(members) < 2:
                continue
            # 排序：已标准化实体排前，再按事实数降序
            members.sort(key=lambda i: (
                0 if items[i]["source"] == "entity" else 1,
                -(items[i]["fact_count"] or 0),
            ))
            pivot = members[0]
            cluster_items = []
            for i in members:
                sims = scores.get((min(pivot, i), max(pivot, i)), 0.0) if i != pivot else 1.0
                cluster_items.append({
                    "text": items[i]["text"],
                    "fact_count": items[i]["fact_count"],
                    "entity_id": items[i]["entity_id"],
                    "entity_type": items[i]["entity_type"],
                    "source": items[i]["source"],
                    "sim_to_pivot": round(sims, 3),
                    "is_pivot": i == pivot,
                })
            max_fc = max(it["fact_count"] for it in cluster_items)
            clusters.append({
                "pivot_text": items[pivot]["text"],
                "max_fact_count": max_fc,
                "members": cluster_items,
                "has_geo_warn": any(
                    _geo_in_bracket(cluster_items[a]["text"]) != _geo_in_bracket(cluster_items[b]["text"])
                    and _geo_in_bracket(cluster_items[a]["text"]) is not None
                    and _geo_in_bracket(cluster_items[b]["text"]) is not None
                    for a in range(len(cluster_items))
                    for b in range(a + 1, len(cluster_items))
                ),
            })

        clusters.sort(key=lambda c: -c["max_fact_count"])
        return jsonify({"clusters": clusters, "total_items": len(items)})

    @app.route("/entity/<entity_id>")
    def entity_timeline_page(entity_id):
        """实体详情页面（含基础信息、关系、时间轴）"""
        tab = request.args.get("tab", "overview")
        initial_type = request.args.get("fact_type", "")
        active_types = [t for t in initial_type.split(",") if t] if initial_type else []

        detail = get_entity_detail(entity_id)

        # 始终加载全部事实，由前端分类显示
        data = get_entity_timeline(entity_id=entity_id, fact_type="")
        if not detail and not data["facts"]:
            return "Entity not found", 404

        if not detail:
            detail = {
                "id": entity_id,
                "canonical_name": data["entity_info"].get("name", "未知"),
                "entity_type": data["entity_info"].get("entity_type", ""),
                "normalized_name": "",
                "aliases": [],
                "relations_from": [],
                "relations_to": [],
                "fact_type_stats": [],
                "total_fact_count": data["total_count"],
                "source_doc_count": 0,
                "time_earliest": None,
                "time_latest": None,
            }

        # 为每条事实生成摘要，解析 qualifier，统计分类计数
        from collections import defaultdict
        type_counts = defaultdict(int)
        for f in data["facts"]:
            type_counts[f["fact_type"]] += 1
            qd = {}
            if f.get("qualifier_json"):
                try:
                    qd = json.loads(f["qualifier_json"])
                except (json.JSONDecodeError, TypeError):
                    qd = {}
            f["qualifiers_display"] = qd
            f["summary"] = _build_fact_summary(f)

        valid_types = cfg.get("fact_types", [])

        REL_TYPE_ZH = {
            "子公司": "子公司", "股东": "股东", "合资": "合资",
            "品牌": "品牌归属", "合作方": "合作方", "投资": "投资/持有",
        }

        # 合并关系列表，附加方向标记，供模板按 fact_type 分组
        all_relations = []
        for r in detail.get("relations_from", []):
            all_relations.append({**r, "_dir": "from"})
        for r in detail.get("relations_to", []):
            all_relations.append({**r, "_dir": "to"})

        return render_template(
            "entity_timeline.html",
            entity=data["entity_info"],
            detail=detail,
            facts=data["facts"],
            available_types=data["available_types"],
            total_count=data["total_count"],
            active_types=active_types,
            current_tab=tab,
            fact_types=valid_types,
            type_counts=dict(type_counts),
            REL_TYPE_ZH=REL_TYPE_ZH,
            all_relations=all_relations,
        )

    @app.route("/api/entity/<entity_id>/detail")
    def api_entity_detail(entity_id):
        """实体完整详情 API"""
        detail = get_entity_detail(entity_id)
        if not detail:
            return jsonify({"error": "not found"}), 404
        return jsonify(detail)

    @app.route("/api/entity/<entity_id>/update", methods=["POST"])
    def api_entity_update(entity_id):
        """更新实体基本信息（名称、类型）"""
        data = request.get_json(silent=True) or {}
        conn = get_connection()
        try:
            ent = conn.execute("SELECT id FROM entity WHERE id=?", (entity_id,)).fetchone()
            if not ent:
                return jsonify({"error": "entity not found"}), 404

            updates = []
            params = []
            if "canonical_name" in data and data["canonical_name"].strip():
                new_name = data["canonical_name"].strip()
                updates.append("canonical_name=?")
                params.append(new_name)
                # 同步更新 normalized_name
                import re
                normalized = re.sub(r"[（(）)\s]", "", new_name).lower()
                updates.append("normalized_name=?")
                params.append(normalized)
                # 同步更新 fact_atom.subject_text
                conn.execute(
                    "UPDATE fact_atom SET subject_text=? WHERE subject_entity_id=?",
                    (new_name, entity_id),
                )
                conn.execute(
                    "UPDATE fact_atom SET object_text=? WHERE object_entity_id=?",
                    (new_name, entity_id),
                )
            if "entity_type" in data and data["entity_type"]:
                updates.append("entity_type=?")
                params.append(data["entity_type"])

            if updates:
                params.append(entity_id)
                conn.execute(
                    f"UPDATE entity SET {', '.join(updates)} WHERE id=?",
                    params,
                )
                conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/entity/<entity_id>/alias/add", methods=["POST"])
    def api_entity_alias_add(entity_id):
        """添加实体别名"""
        data = request.get_json(silent=True) or {}
        alias_name = (data.get("alias_name") or data.get("alias") or "").strip()
        if not alias_name:
            return jsonify({"error": "alias_name required"}), 400

        conn = get_connection()
        try:
            ent = conn.execute("SELECT id FROM entity WHERE id=?", (entity_id,)).fetchone()
            if not ent:
                return jsonify({"error": "entity not found"}), 404

            # 检查别名是否已存在
            existing = conn.execute(
                "SELECT id FROM entity_alias WHERE alias_name=?", (alias_name,)
            ).fetchone()
            if existing:
                return jsonify({"error": f"别名 '{alias_name}' 已被使用"}), 409

            import uuid
            conn.execute(
                "INSERT INTO entity_alias (id, entity_id, alias_name) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), entity_id, alias_name),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/entity/<entity_id>/alias/<alias_id>/delete", methods=["POST"])
    def api_entity_alias_delete(entity_id, alias_id):
        """删除实体别名"""
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM entity_alias WHERE id=? AND entity_id=?",
                (alias_id, entity_id),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/entity/<entity_id>/timeline")
    def api_entity_timeline(entity_id):
        """实体时间轴数据 API"""
        fact_type = request.args.get("fact_type", "")
        data = get_entity_timeline(entity_id=entity_id, fact_type=fact_type)
        return jsonify(data)

    @app.route("/fact/<fact_id>")
    def fact_detail_page(fact_id):
        """事实详情页面"""
        fact = get_fact_detail(fact_id)
        if not fact:
            return "Fact not found", 404
        qd = {}
        if fact.get("qualifier_json"):
            try:
                qd = json.loads(fact["qualifier_json"])
            except (json.JSONDecodeError, TypeError):
                qd = {}
        fact["qualifiers_display"] = qd
        fact["summary"] = _build_fact_summary(fact)

        QUAL_KEY_ZH = {
            'metric_name': '指标', 'segment': '细分领域', 'yoy': '同比',
            'qoq': '环比', 'report_scope': '报告范围', 'product_type': '产品类型',
            'project_name': '项目名称', 'investment_type': '投资类型',
            'market_scope': '市场范围', 'ranking_name': '排名名称',
            'ranking_scope': '排名范围', 'cooperation_type': '合作类型',
            'product_name': '产品名称', 'price_type': '价格类型',
            'stage': '阶段', 'scope': '范围', 'purpose': '目的',
            'phase': '期次', 'duration': '期限', 'reason': '原因',
            'rank': '排名', 'ranking_year': '排名年份',
        }
        return render_template(
            "fact_detail.html",
            fact=fact,
            QUAL_KEY_ZH=QUAL_KEY_ZH,
        )

    @app.route("/fact/<fact_id>/delete", methods=["POST"])
    def fact_delete(fact_id):
        """删除事实原子"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM fact_atom WHERE id=?", (fact_id,)
            ).fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            conn.execute("DELETE FROM fact_atom WHERE id=?", (fact_id,))
            import uuid
            conn.execute(
                """INSERT INTO review_log
                (id, target_type, target_id, old_status, new_status,
                 reviewer, review_action, review_note)
                VALUES (?, 'fact_atom', ?, 'DELETED', 'DELETED', '人工', 'delete', '')""",
                (str(uuid.uuid4()), fact_id),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("人工删除事实 [%s]", fact_id[:8])
        return jsonify({"ok": True})

    @app.route("/entity/search")
    def entity_search_page():
        """按名称搜索实体的时间轴页面"""
        subject = request.args.get("subject", "")
        initial_type = request.args.get("fact_type", "")
        active_types = [t for t in initial_type.split(",") if t] if initial_type else []
        tab = request.args.get("tab", "timeline")
        if not subject:
            return redirect(url_for("graph_page"))
        data = get_entity_timeline(subject_text=subject, fact_type="")

        entity_info = data.get("entity_info", {})
        eid = entity_info.get("id")
        detail = get_entity_detail(eid) if eid else None
        if not detail:
            detail = {
                "id": eid or "",
                "canonical_name": entity_info.get("name", subject),
                "entity_type": entity_info.get("entity_type", ""),
                "normalized_name": "",
                "aliases": [],
                "relations_from": [],
                "relations_to": [],
                "fact_type_stats": [],
                "total_fact_count": data["total_count"],
                "source_doc_count": 0,
                "time_earliest": None,
                "time_latest": None,
            }

        from collections import defaultdict
        type_counts = defaultdict(int)
        for f in data["facts"]:
            type_counts[f["fact_type"]] += 1
            qd = {}
            if f.get("qualifier_json"):
                try:
                    qd = json.loads(f["qualifier_json"])
                except (json.JSONDecodeError, TypeError):
                    qd = {}
            f["qualifiers_display"] = qd
            f["summary"] = _build_fact_summary(f)

        valid_types = cfg.get("fact_types", [])

        REL_TYPE_ZH = {
            "子公司": "子公司", "股东": "股东", "合资": "合资",
            "品牌": "品牌归属", "合作方": "合作方", "投资": "投资/持有",
        }

        return render_template(
            "entity_timeline.html",
            entity=data["entity_info"],
            detail=detail,
            facts=data["facts"],
            available_types=data["available_types"],
            total_count=data["total_count"],
            active_types=active_types,
            current_tab=tab,
            fact_types=valid_types,
            type_counts=dict(type_counts),
            REL_TYPE_ZH=REL_TYPE_ZH,
        )

    def _start_background_process(doc_ids: list[str]):
        """将文档提交到后台线程池并行处理。

        ThreadPoolExecutor(max_workers=N) 允许 N 篇文档并行处理，
        每个文档独立提交，一个失败不影响后续文档。
        """
        def _on_done(future: concurrent.futures.Future, doc_id: str):
            exc = future.exception()
            if exc:
                logger.error("文档处理异常 [%s]: %s", doc_id[:8], exc)

        for doc_id in doc_ids:
            future = _executor.submit(_process_one_doc, doc_id)
            future.add_done_callback(lambda f, did=doc_id: _on_done(f, did))

    # ─────────────── 批量重评估 API ───────────────

    @app.route("/api/review/re-evaluate", methods=["POST"])
    def api_review_re_evaluate():
        """用当前 config 批量重评估待人工审核 事实，无需重新调用 LLM。
        对 confidence_score >= 0.65 且符合新配置条件的事实晋升为自动通过。
        """
        from app.services.reviewer import batch_re_evaluate_pending
        try:
            result = batch_re_evaluate_pending()
            return jsonify({"success": True, **result})
        except Exception as e:
            logger.error("批量重评估失败: %s", e, exc_info=True)
            return jsonify({"error": str(e)}), 500

    # ─────────────── 暂停/继续 API ───────────────

    @app.route("/api/tasks/pause", methods=["POST"])
    def api_pause_processing():
        """暂停后台处理"""
        _pause_event.clear()
        logger.info("后台处理已暂停")
        return jsonify({"ok": True, "paused": True})

    @app.route("/api/tasks/resume", methods=["POST"])
    def api_resume_processing():
        """继续后台处理"""
        _pause_event.set()
        logger.info("后台处理已恢复")
        return jsonify({"ok": True, "paused": False})

    @app.route("/api/tasks/pause-status", methods=["GET"])
    def api_pause_status():
        """获取当前暂停状态"""
        return jsonify({"paused": not _pause_event.is_set()})

    # 注册任务状态 API 蓝图
    app.register_blueprint(api_tasks_bp)

    return app


def run_app():
    """启动 Web 审核服务"""
    cfg = get_config()
    web_cfg = cfg.get("web", {})
    app = create_app()
    app.run(
        host=web_cfg.get("host", "0.0.0.0"),
        port=web_cfg.get("port", 5000),
        debug=web_cfg.get("debug", True),
    )


if __name__ == "__main__":
    run_app()
