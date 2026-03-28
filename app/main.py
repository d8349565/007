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


def _print_result(result: dict):
    if "error" in result:
        print(f"  ❌ [{result['document_id'][:8]}] 错误: {result['error']}")
    else:
        print(
            f"  ✅ [{result['document_id'][:8]}] "
            f"chunks={result['chunks']}, evidences={result['evidences']}, "
            f"facts={result['facts']}, pass={result['passed']}, "
            f"reject={result['rejected']}, uncertain={result['uncertain']}"
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

    # relink
    sub_relink = subparsers.add_parser("relink", help="重新执行实体链接（补全空 entity_id）")
    sub_relink.set_defaults(func=cmd_relink)

    # stats
    sub_stats = subparsers.add_parser("stats", help="查看统计")
    sub_stats.set_defaults(func=cmd_stats)

    # web
    sub_web = subparsers.add_parser("web", help="启动审核 Web 界面")
    sub_web.set_defaults(func=cmd_web)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
