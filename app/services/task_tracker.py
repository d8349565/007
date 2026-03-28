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
            WHERE status IN ('处理中', '清洗中', '抽取中', '审核中', '链接中', '失败', '已完成')
            ORDER BY
                CASE status
                    WHEN '失败' THEN 0
                    WHEN '处理中' THEN 1
                    WHEN '清洗中' THEN 2
                    WHEN '抽取中' THEN 3
                    WHEN '审核中' THEN 4
                    WHEN '链接中' THEN 5
                    WHEN '已完成' THEN 6
                    ELSE 7
                END,
                updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        tasks = [dict(row) for row in rows]

        # 计算汇总
        total = len(tasks)
        done = sum(1 for t in tasks if t['status'] == '已完成')
        failed = sum(1 for t in tasks if t['status'] == '失败')
        running = sum(1 for t in tasks if t['status'] in ('处理中', '清洗中', '抽取中', '审核中', '链接中'))
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


def clear_done_tasks() -> int:
    """将已完成/失败的任务状态重置为 ACTIVE，从任务面板隐藏但保留数据"""
    conn = get_connection()
    try:
        cursor = conn.execute("""
            UPDATE source_document
            SET status = '待处理', updated_at = datetime('now')
            WHERE status IN ('已完成', '失败', '内容为空', '清洗后为空')
        """)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
