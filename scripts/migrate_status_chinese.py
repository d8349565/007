"""状态值中英文迁移：将数据库中所有英文状态/类型值批量替换为中文。

迁移范围：
  - source_document.status / source_type
  - fact_atom.review_status
  - extraction_task.status / task_type
  - entity.entity_type
  - entity_merge_task.status / llm_verdict
  - entity_relation_suggestion.status / suggestion_type
  - entity_relation.relation_type / source
  - entity_search_cache.search_source
  - review_log.reviewer / review_action / old_status / new_status

默认 dry-run，加 --apply 实际执行。幂等安全。
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.logger import get_logger

logger = get_logger(__name__)

# ── 映射表 ──────────────────────────────────────────────────

# (表名, 列名, {英文值: 中文值})
MAPPINGS = [
    # --- source_document ---
    ("source_document", "status", {
        "ACTIVE": "待处理",
        "processing": "处理中",
        "cleaning": "清洗中",
        "extracting": "抽取中",
        "reviewing": "审核中",
        "linking": "链接中",
        "processed": "已完成",
        "failed": "失败",
        "empty_content": "内容为空",
        "empty_after_clean": "清洗后为空",
    }),
    ("source_document", "source_type", {
        "file": "文件",
        "url": "网址",
        "paste": "粘贴",
    }),

    # --- fact_atom ---
    ("fact_atom", "review_status", {
        "PENDING": "待处理",
        "AUTO_PASS": "自动通过",
        "HUMAN_REVIEW_REQUIRED": "待人工审核",
        "HUMAN_PASS": "人工通过",
        "REJECTED": "已拒绝",
        "HUMAN_REJECTED": "人工拒绝",
        "UNCERTAIN": "不确定",
        "DUPLICATE": "重复",
    }),

    # --- extraction_task ---
    ("extraction_task", "status", {
        "running": "运行中",
        "success": "成功",
        "failed": "失败",
    }),
    ("extraction_task", "task_type", {
        "evidence_finder": "证据发现",
        "fact_extractor": "事实抽取",
        "reviewer": "审核",
        "full_extractor": "全文抽取",
        "complementation": "上下文补全",
        "deduplicator": "去重",
        "entity_linker": "实体链接",
    }),

    # --- entity ---
    ("entity", "entity_type", {
        "UNKNOWN": "未知",
        "COMPANY": "企业",
        "GROUP": "集团",
        "PROJECT": "项目",
        "REGION": "地区",
        "COUNTRY": "国家",
    }),

    # --- entity_merge_task ---
    ("entity_merge_task", "status", {
        "pending": "待处理",
        "approved": "已批准",
        "rejected": "已拒绝",
        "executed": "已执行",
        "skipped": "已跳过",
    }),
    ("entity_merge_task", "llm_verdict", {
        "merge": "合并",
        "keep": "保留",
        "uncertain": "不确定",
    }),

    # --- entity_relation_suggestion ---
    ("entity_relation_suggestion", "status", {
        "pending": "待处理",
        "confirmed": "已确认",
        "rejected": "已拒绝",
    }),
    ("entity_relation_suggestion", "suggestion_type", {
        "relation": "关系",
        "merge": "合并",
        "alias": "别名",
        "skip": "跳过",
    }),

    # --- entity_relation ---
    ("entity_relation", "relation_type", {
        "SUBSIDIARY": "子公司",
        "SHAREHOLDER": "股东",
        "JV": "合资",
        "BRAND": "品牌",
        "PARTNER": "合作方",
        "INVESTS_IN": "投资",
    }),
    ("entity_relation", "source", {
        "manual": "手动",
        "auto": "自动提取",
    }),

    # --- entity_search_cache ---
    ("entity_search_cache", "search_source", {
        "duckduckgo": "搜索引擎",
        "llm_knowledge": "LLM知识",
        "combined": "综合",
    }),

    # --- review_log ---
    ("review_log", "reviewer", {
        "system_reviewer": "系统审核",
        "system": "系统",
        "auto_dedup": "自动去重",
    }),
    ("review_log", "review_action", {
        "AUTO_PASS": "自动通过",
        "HUMAN_PASS": "人工通过",
        "REJECT": "拒绝",
        "HUMAN_REJECTED": "人工拒绝",
        "MARK_DUPLICATE": "标记重复",
    }),
    ("review_log", "old_status", {
        "PENDING": "待处理",
        "AUTO_PASS": "自动通过",
        "HUMAN_REVIEW_REQUIRED": "待人工审核",
        "HUMAN_PASS": "人工通过",
        "REJECTED": "已拒绝",
        "HUMAN_REJECTED": "人工拒绝",
        "UNCERTAIN": "不确定",
        "DUPLICATE": "重复",
    }),
    ("review_log", "new_status", {
        "PENDING": "待处理",
        "AUTO_PASS": "自动通过",
        "HUMAN_REVIEW_REQUIRED": "待人工审核",
        "HUMAN_PASS": "人工通过",
        "REJECTED": "已拒绝",
        "HUMAN_REJECTED": "人工拒绝",
        "UNCERTAIN": "不确定",
        "DUPLICATE": "重复",
    }),
]


def migrate_statuses(dry_run: bool = True) -> int:
    """遍历所有映射，将英文值替换为中文值。"""
    conn = get_connection()
    total_updated = 0
    try:
        for table, column, mapping in MAPPINGS:
            for eng_val, chn_val in mapping.items():
                # 统计数量
                row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM {table} WHERE {column} = ?",
                    (eng_val,),
                ).fetchone()
                cnt = row["cnt"] if row else 0
                if cnt == 0:
                    continue

                logger.debug(
                    "%s.%s: '%s' → '%s' (%d 条)",
                    table, column, eng_val, chn_val, cnt,
                )
                if not dry_run:
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                        (chn_val, eng_val),
                    )
                total_updated += cnt

        if not dry_run:
            conn.commit()
            logger.info("迁移完成，共更新 %d 条记录", total_updated)
    finally:
        conn.close()
    return total_updated


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="将数据库中所有英文状态/类型值迁移为中文"
    )
    parser.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    args = parser.parse_args()

    dry_run = not args.apply
    count = migrate_statuses(dry_run=dry_run)

    mode = "[DRY-RUN]" if dry_run else "[APPLIED]"
    print(f"{mode} 影响 {count} 条记录")
    if dry_run and count > 0:
        print("加 --apply 参数实际执行迁移")


if __name__ == "__main__":
    main()
