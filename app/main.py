"""主入口 —— 单篇/批量处理 CLI"""

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_config
from app.logger import get_logger
from app.models.db import init_db
from app.services.importer import import_file, import_url, import_paste, import_batch
from app.services.pipeline import process_document, process_batch
from app.services.query import get_stats

logger = get_logger(__name__)


def cmd_init(args):
    """初始化数据库"""
    init_db()
    print("✅ 数据库初始化完成")


def cmd_import_file(args):
    """导入单个文件"""
    init_db()
    doc_id = import_file(args.path, source=args.source or "")
    print(f"✅ 导入成功: {doc_id}")
    if args.process:
        result = process_document(doc_id)
        _print_result(result)


def cmd_import_url(args):
    """从 URL 导入"""
    init_db()
    doc_id = import_url(args.url, source=args.source or "")
    print(f"✅ 导入成功: {doc_id}")
    if args.process:
        result = process_document(doc_id)
        _print_result(result)


def cmd_import_batch(args):
    """批量导入目录"""
    init_db()
    doc_ids = import_batch(args.directory, pattern=args.pattern or "*.txt")
    print(f"✅ 批量导入 {len(doc_ids)} 篇文档")
    if args.process:
        results = process_batch(doc_ids)
        for r in results:
            _print_result(r)


def cmd_process(args):
    """处理已导入的文档"""
    init_db()
    if args.document_id:
        result = process_document(args.document_id)
        _print_result(result)
    elif args.all:
        from app.models.db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id FROM source_document WHERE status IN ('待处理', 'imported', 'failed', 'extracting', 'too_short') OR status IS NULL"
            ).fetchall()
        finally:
            conn.close()
        doc_ids = [r["id"] for r in rows]
        if not doc_ids:
            print("⚠️ 没有待处理的文档")
            return
        print(f"📄 发现 {len(doc_ids)} 篇待处理文档")
        results = process_batch(doc_ids)
        for r in results:
            _print_result(r)
    else:
        print("❌ 请指定 --document-id 或 --all")


def cmd_reprocess(args):
    """重新处理已完成的文档（清除旧结果后重跑全流程）"""
    init_db()
    from app.models.db import get_connection
    if args.document_id:
        doc_ids = [args.document_id]
    elif args.failed:
        # 查找解析失败或结果为 0 的文档
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT DISTINCT s.id, s.title FROM source_document s
                   WHERE s.status = '已完成'
                     AND (
                       -- 无事实原子
                       NOT EXISTS (SELECT 1 FROM fact_atom f WHERE f.document_id = s.id)
                       -- 或存在解析失败的任务
                       OR EXISTS (SELECT 1 FROM extraction_task t
                                  WHERE t.document_id = s.id AND t.status = '解析失败')
                     )"""
            ).fetchall()
        finally:
            conn.close()
        doc_ids = [r["id"] for r in rows]
        if not doc_ids:
            print("⚠️ 没有需要重新处理的失败文档")
            return
        print(f"📄 发现 {len(doc_ids)} 篇需重新处理的文档:")
        for r in rows:
            print(f"  {r['id'][:8]} {(r['title'] or '')[:50]}")
    elif args.all:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id FROM source_document WHERE status = '已完成'"
            ).fetchall()
        finally:
            conn.close()
        doc_ids = [r["id"] for r in rows]
        if not doc_ids:
            print("⚠️ 没有已完成的文档")
            return
        print(f"📄 将重新处理 {len(doc_ids)} 篇文档")
    else:
        print("❌ 请指定 --document-id、--failed 或 --all")
        return

    results = process_batch(doc_ids)
    for r in results:
        _print_result(r)


def cmd_relink(args):
    """重新执行实体链接（补全空 entity_id）"""
    init_db()
    from app.services.entity_linker import batch_link_fact_atoms
    print("[relink] 开始重新执行实体链接...")
    stats = batch_link_fact_atoms()
    print(
        f"[relink] 实体链接完成: 处理 {stats['processed']} 条, "
        f"匹配 {stats['matched']} 条, 未匹配 {stats['unmatched']} 条, "
        f"新建实体 {stats['created']} 个"
    )


def cmd_stats(args):
    """查看统计"""
    init_db()
    stats = get_stats()
    print(f"\n📊 统计概览")
    print(f"  文档总数: {stats['document_count']}")
    print(f"  事实总数: {stats['fact_count']}")
    print(f"\n  fact_type 分布:")
    for item in stats.get("fact_type_distribution", []):
        print(f"    {item['fact_type']}: {item['cnt']}")
    print(f"\n  审核状态分布:")
    for item in stats.get("review_status_distribution", []):
        print(f"    {item['review_status']}: {item['cnt']}")
    print(f"\n  Token 使用:")
    for item in stats.get("token_usage", []):
        print(f"    {item['task_type']}: {item['calls']} 次调用, 输入 {item['total_input']} tokens, 输出 {item['total_output']} tokens")


def cmd_web(args):
    """启动审核 Web 界面"""
    init_db()
    from app.web.review_app import run_app
    print("启动审核 Web 界面...")
    run_app()


def cmd_profile(args):
    """构建或丰富实体档案"""
    init_db()
    from app.services.entity_profiler import (
        build_entity_profile, enrich_entity_profile, build_all_profiles,
    )

    if args.all:
        print(f"[profile] 批量构建实体档案 (min_facts={args.min_facts})...")
        stats = build_all_profiles(min_facts=args.min_facts)
        print(f"[profile] 完成: 总{stats['total']} 成功{stats['built']} 失败{stats['failed']}")
    elif args.entity_id:
        if args.enrich:
            print(f"[profile] 丰富实体档案: {args.entity_id[:8]}...")
            result = enrich_entity_profile(args.entity_id)
            if result:
                print(f"[profile] 完成: {result.get('canonical_name', '')}")
                if result.get("new_aliases"):
                    print(f"  新增别名: {result['new_aliases']}")
                if result.get("enriched_fields"):
                    print(f"  丰富字段: {result['enriched_fields']}")
            else:
                print("[profile] 失败或实体不存在")
        else:
            print(f"[profile] 构建实体档案: {args.entity_id[:8]}...")
            result = build_entity_profile(args.entity_id)
            if result:
                print(f"[profile] 完成: {result['canonical_name']}")
                print(f"  别名: {result['aliases']}")
                print(f"  关系: {len(result['relations'])} 条")
                print(f"  指标: {len(result['benchmarks'])} 条")
                print(f"  竞品: {len(result['competitors'])} 个")
                print(f"  事实: {result['fact_count']} 条")
            else:
                print("[profile] 失败或实体不存在")
    else:
        print("[profile] 请指定 --entity-id 或 --all")


def _print_result(result: dict):
    if "error" in result:
        print(f"  ❌ [{result['document_id'][:8]}] 错误: {result['error']}")
    else:
        print(
            f"  ✅ [{result['document_id'][:8]}] "
            f"evidences={result.get('evidences', 0)}, "
            f"facts={result.get('facts', 0)}, pass={result.get('passed', 0)}, "
            f"reject={result.get('rejected', 0)}, uncertain={result.get('uncertain', 0)}, "
            f"dup={result.get('duplicates', 0)}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="资讯颗粒化收集系统 — 事实原子抽取工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init
    sub_init = subparsers.add_parser("init", help="初始化数据库")
    sub_init.set_defaults(func=cmd_init)

    # import-file
    sub_file = subparsers.add_parser("import-file", help="导入单个文件")
    sub_file.add_argument("path", help="文件路径")
    sub_file.add_argument("--source", help="来源名称")
    sub_file.add_argument("--process", action="store_true", help="导入后立即处理")
    sub_file.set_defaults(func=cmd_import_file)

    # import-url
    sub_url = subparsers.add_parser("import-url", help="从 URL 导入")
    sub_url.add_argument("url", help="URL 地址")
    sub_url.add_argument("--source", help="来源名称")
    sub_url.add_argument("--process", action="store_true", help="导入后立即处理")
    sub_url.set_defaults(func=cmd_import_url)

    # import-batch
    sub_batch = subparsers.add_parser("import-batch", help="批量导入目录")
    sub_batch.add_argument("directory", help="目录路径")
    sub_batch.add_argument("--pattern", default="*.txt", help="文件匹配模式 (默认 *.txt)")
    sub_batch.add_argument("--source", help="来源名称")
    sub_batch.add_argument("--process", action="store_true", help="导入后立即处理")
    sub_batch.set_defaults(func=cmd_import_batch)

    # process
    sub_proc = subparsers.add_parser("process", help="处理已导入的文档")
    sub_proc.add_argument("--document-id", help="文档 ID")
    sub_proc.add_argument("--all", action="store_true", help="处理所有待处理文档")
    sub_proc.set_defaults(func=cmd_process)

    # reprocess
    sub_reproc = subparsers.add_parser("reprocess", help="重新处理已完成的文档")
    sub_reproc.add_argument("--document-id", help="指定文档 ID")
    sub_reproc.add_argument("--failed", action="store_true", help="重新处理解析失败或结果为空的文档")
    sub_reproc.add_argument("--all", action="store_true", help="重新处理所有已完成的文档")
    sub_reproc.set_defaults(func=cmd_reprocess)

    # relink
    sub_relink = subparsers.add_parser("relink", help="重新执行实体链接（补全空 entity_id）")
    sub_relink.set_defaults(func=cmd_relink)

    # stats
    sub_stats = subparsers.add_parser("stats", help="查看统计")
    sub_stats.set_defaults(func=cmd_stats)

    # web
    sub_web = subparsers.add_parser("web", help="启动审核 Web 界面")
    sub_web.set_defaults(func=cmd_web)

    # profile
    sub_profile = subparsers.add_parser("profile", help="构建实体档案")
    sub_profile.add_argument("--entity-id", help="指定实体 ID")
    sub_profile.add_argument("--all", action="store_true", help="构建所有有事实的实体档案")
    sub_profile.add_argument("--enrich", action="store_true", help="使用 Web 搜索 + LLM 丰富档案")
    sub_profile.add_argument("--min-facts", type=int, default=1, help="最少事实数（--all 时生效）")
    sub_profile.set_defaults(func=cmd_profile)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
