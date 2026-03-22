"""任务状态跟踪 - 从 source_document 查询处理中/失败的任务"""

from app.models.db import get_connection


def get_processing_tasks(limit: int = 20) -> tuple[list[dict], dict]:
    """获取所有处理中/失败/刚完成的任务"""
    conn = get_connection()
    try:
        # 使用 COALESCE 兼容没有 error_message 字段的旧数据库
        rows = conn.execute("""
            SELECT
                id,
                title,
                status,
                updated_at,
                COALESCE(error_message, '') AS error_message,
                (SELECT COUNT(*) FROM fact_atom WHERE document_id = source_document.id) AS facts_count
            FROM source_document
            WHERE status IN ('processing', 'cleaning', 'extracting', 'reviewing', 'linking', 'failed', 'processed')
            ORDER BY
                CASE status
                    WHEN 'failed' THEN 0
                    WHEN 'processing' THEN 1
                    WHEN 'cleaning' THEN 2
                    WHEN 'extracting' THEN 3
                    WHEN 'reviewing' THEN 4
                    WHEN 'linking' THEN 5
                    WHEN 'processed' THEN 6
                    ELSE 7
                END,
                updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        tasks = [dict(row) for row in rows]

        # 计算汇总
        total = len(tasks)
        done = sum(1 for t in tasks if t['status'] == 'processed')
        failed = sum(1 for t in tasks if t['status'] == 'failed')
        running = sum(1 for t in tasks if t['status'] in ('processing', 'cleaning', 'extracting', 'reviewing', 'linking'))
        pending = 0  # 当前不追踪 pending 状态

        summary = {
            'total': total,
            'done': done,
            'failed': failed,
            'running': running,
            'pending': pending
        }

        return tasks, summary
    finally:
        conn.close()
