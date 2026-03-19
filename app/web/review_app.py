"""Flask 极简审核 Web 界面"""

import json
import os
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
)

logger = get_logger(__name__)

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
        return dict(FACT_TYPE_NAMES=FACT_TYPE_NAMES, ENTITY_TYPE_ZH=ENTITY_TYPE_ZH)

    @app.route("/")
    def index():
        stats = get_stats()
        entity_overview = get_entity_overview(top_n=10)
        return render_template("index.html", stats=stats, entity_overview=entity_overview)

    @app.route("/documents")
    def documents_list():
        docs = get_documents(limit=200)
        return render_template("documents.html", documents=docs)

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
        pass_type = request.args.get("pass_type", "")  # '' | 'AUTO_PASS' | 'HUMAN_PASS'
        page = int(request.args.get("page", 1))
        per_page = 30

        # 查询状态范围
        if pass_type in ("AUTO_PASS", "HUMAN_PASS"):
            status_filter = pass_type
        else:
            status_filter = ""  # 由 query_facts 双状态处理

        # 若无单一状态筛选，分两次查询合并
        if not pass_type:
            facts_auto = query_facts(
                fact_type=fact_type, review_status="AUTO_PASS",
                document_id=doc_id, limit=per_page * 10,
            )
            facts_human = query_facts(
                fact_type=fact_type, review_status="HUMAN_PASS",
                document_id=doc_id, limit=per_page * 10,
            )
            all_facts = facts_auto + facts_human
            all_facts.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        else:
            all_facts = query_facts(
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
        status = request.args.get("status", "HUMAN_REVIEW_REQUIRED")
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
            "HUMAN_REVIEW_REQUIRED", "AUTO_PASS", "PENDING",
            "REJECTED", "HUMAN_PASS", "HUMAN_REJECTED",
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

        if action not in ("HUMAN_PASS", "HUMAN_REJECTED"):
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
                VALUES (?, 'fact_atom', ?, ?, ?, 'human', ?, ?)""",
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
            new_status = "HUMAN_PASS"
        elif action == "save_and_reject":
            new_status = "HUMAN_REJECTED"
        else:
            new_status = "HUMAN_REVIEW_REQUIRED"

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
                VALUES (?, 'fact_atom', ?, ?, ?, 'human', 'human_edit', ?)""",
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
            conn.execute("UPDATE source_document SET status='ACTIVE' WHERE id=?", (doc_id,))
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
                      AND f.review_status IN ('AUTO_PASS','HUMAN_PASS')
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
        status = request.args.get("status", "pending")
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

    # ─────────────────────── 知识图谱 & 时间轴 ───────────────────────

    @app.route("/graph")
    def graph_page():
        """知识图谱可视化页面"""
        valid_types = cfg.get("fact_types", [])
        return render_template("graph.html", fact_types=valid_types)

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

    @app.route("/entity/<entity_id>")
    def entity_timeline_page(entity_id):
        """实体时间轴页面"""
        fact_type = request.args.get("fact_type", "")
        data = get_entity_timeline(entity_id=entity_id, fact_type=fact_type)
        if not data["facts"] and not data["entity_info"].get("name"):
            return "Entity not found", 404
        valid_types = cfg.get("fact_types", [])
        return render_template(
            "entity_timeline.html",
            entity=data["entity_info"],
            facts=data["facts"],
            available_types=data["available_types"],
            total_count=data["total_count"],
            current_type=fact_type,
            fact_types=valid_types,
        )

    @app.route("/api/entity/<entity_id>/timeline")
    def api_entity_timeline(entity_id):
        """实体时间轴数据 API"""
        fact_type = request.args.get("fact_type", "")
        data = get_entity_timeline(entity_id=entity_id, fact_type=fact_type)
        return jsonify(data)

    @app.route("/entity/search")
    def entity_search_page():
        """按名称搜索实体的时间轴页面"""
        subject = request.args.get("subject", "")
        fact_type = request.args.get("fact_type", "")
        if not subject:
            return redirect(url_for("graph_page"))
        data = get_entity_timeline(subject_text=subject, fact_type=fact_type)
        valid_types = cfg.get("fact_types", [])
        return render_template(
            "entity_timeline.html",
            entity=data["entity_info"],
            facts=data["facts"],
            available_types=data["available_types"],
            total_count=data["total_count"],
            current_type=fact_type,
            fact_types=valid_types,
        )

    def _start_background_process(doc_ids: list[str]):
        """在后台线程中处理文档（避免阻塞 HTTP 请求）"""
        def _run():
            from app.services.pipeline import process_document
            for doc_id in doc_ids:
                try:
                    process_document(doc_id)
                except Exception as e:
                    logger.error("后台处理文档失败 [%s]: %s", doc_id[:8], e)
        t = threading.Thread(target=_run, daemon=True)
        t.start()

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
