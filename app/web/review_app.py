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
    @app.context_processor
    def inject_fact_type_names():
        return dict(FACT_TYPE_NAMES=FACT_TYPE_NAMES)

    @app.route("/")
    def index():
        stats = get_stats()
        return render_template("index.html", stats=stats)

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
        conn = get_connection()
        try:
            fact_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM fact_atom WHERE document_id=?", (doc_id,)
            ).fetchall()]
            if fact_ids:
                ph = ",".join(["?"] * len(fact_ids))
                conn.execute(f"DELETE FROM review_log WHERE target_id IN ({ph})", fact_ids)
            conn.execute("DELETE FROM fact_atom WHERE document_id=?", (doc_id,))
            conn.execute("DELETE FROM evidence_span WHERE document_id=?", (doc_id,))
            conn.execute("DELETE FROM extraction_task WHERE document_id=?", (doc_id,))
            conn.execute("DELETE FROM document_chunk WHERE document_id=?", (doc_id,))
            conn.execute("UPDATE source_document SET status='ACTIVE' WHERE id=?", (doc_id,))
            conn.commit()
        finally:
            conn.close()

        _start_background_process([doc_id])
        return jsonify({"success": True, "processing": True})

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
