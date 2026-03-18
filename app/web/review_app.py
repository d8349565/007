"""Flask 极简审核 Web 界面"""

import json
from flask import Flask, render_template, request, redirect, url_for, jsonify

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection
from app.services.query import (
    query_facts, get_fact_detail, get_stats, export_csv,
    get_documents, get_document, get_document_chunks,
    get_document_evidences, get_document_tasks, get_doc_stats,
    get_passed_facts_stats,
)

logger = get_logger(__name__)


def create_app() -> Flask:
    cfg = get_config()
    web_cfg = cfg.get("web", {})

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = web_cfg.get("secret_key", "dev-secret-change-me")

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
            limit=per_page,
            offset=(page - 1) * per_page,
        )

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
