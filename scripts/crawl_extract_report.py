"""从 juton_urls.txt 爬取资讯 → 运行提取管道 → 生成静态 HTML 报告"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import get_connection
from app.services.importer import import_url
from app.services.pipeline import process_document
from app.logger import get_logger

logger = get_logger(__name__)

# 输入/输出路径
URLS_FILE = Path(__file__).resolve().parent.parent / "_archive" / "juton_urls.txt"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / f"extraction_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"


def load_urls(path: Path) -> list[str]:
    """从文本文件加载 URL 列表（每行一个），跳过空行和注释"""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def crawl_and_extract(urls: list[str]) -> list[dict]:
    """逐个 URL 导入并处理，返回每篇的处理统计"""
    results = []
    for i, url in enumerate(urls, 1):
        print(f"\n{'='*60}")
        print(f"[{i}/{len(urls)}] 处理: {url[:80]}...")
        print(f"{'='*60}")

        record = {"url": url, "doc_id": None, "stats": None, "error": None}

        # 1) 导入
        try:
            doc_id = import_url(url)
            record["doc_id"] = doc_id
            print(f"  导入成功 → doc_id={doc_id[:8]}")
        except Exception as e:
            record["error"] = f"导入失败: {e}"
            logger.error("导入失败 [%s]: %s", url[:60], e)
            results.append(record)
            continue

        # 2) 提取管道
        try:
            stats = process_document(doc_id)
            record["stats"] = stats
            print(f"  抽取完成 → facts={stats['facts']}, pass={stats['passed']}, "
                  f"reject={stats['rejected']}, uncertain={stats['uncertain']}, dup={stats['duplicates']}")
        except Exception as e:
            record["error"] = f"处理失败: {e}"
            logger.error("处理失败 [%s]: %s", doc_id[:8], e)

        results.append(record)
        # 简单限速，避免太快触发反爬
        time.sleep(1)

    return results


def query_all_facts(doc_ids: list[str]) -> list[dict]:
    """查询所有文档中提取出的事实原子"""
    if not doc_ids:
        return []

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(doc_ids))
        rows = conn.execute(
            f"""SELECT fa.*, sd.title AS doc_title, sd.url AS doc_url,
                       es.evidence_text
                FROM fact_atom fa
                JOIN source_document sd ON sd.id = fa.document_id
                LEFT JOIN evidence_span es ON es.id = fa.evidence_span_id
                WHERE fa.document_id IN ({placeholders})
                ORDER BY fa.document_id, fa.fact_type, fa.created_at""",
            doc_ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_documents(doc_ids: list[str]) -> list[dict]:
    """查询文档基本信息"""
    if not doc_ids:
        return []

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(doc_ids))
        rows = conn.execute(
            f"""SELECT id, title, url, source_name, publish_time, status,
                       crawl_time, LENGTH(raw_text) as text_length
                FROM source_document
                WHERE id IN ({placeholders})""",
            doc_ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_task_stats(doc_ids: list[str]) -> list[dict]:
    """查询 LLM 调用统计"""
    if not doc_ids:
        return []

    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(doc_ids))
        rows = conn.execute(
            f"""SELECT document_id, task_type, status,
                       input_tokens, output_tokens, model_name,
                       started_at, finished_at
                FROM extraction_task
                WHERE document_id IN ({placeholders})
                ORDER BY document_id, started_at""",
            doc_ids,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def generate_html_report(
    process_results: list[dict],
    facts: list[dict],
    documents: list[dict],
    task_stats: list[dict],
) -> str:
    """生成静态 HTML 报告"""

    # 按审核状态统计
    status_counts = {}
    type_counts = {}
    for f in facts:
        st = f.get("review_status", "未知")
        status_counts[st] = status_counts.get(st, 0) + 1
        ft = f.get("fact_type", "未知")
        type_counts[ft] = type_counts.get(ft, 0) + 1

    # LLM token 统计
    total_input_tokens = sum(t.get("input_tokens", 0) for t in task_stats)
    total_output_tokens = sum(t.get("output_tokens", 0) for t in task_stats)

    # 状态颜色映射
    status_colors = {
        "自动通过": "#28a745",
        "人工通过": "#20c997",
        "待人工审核": "#ffc107",
        "已拒绝": "#dc3545",
        "重复": "#6c757d",
        "待处理": "#17a2b8",
        "不确定": "#fd7e14",
    }

    # 构建 fact 行的 HTML
    fact_rows = []
    for f in facts:
        review_status = f.get("review_status", "未知")
        color = status_colors.get(review_status, "#6c757d")
        qualifiers = f.get("qualifier_json", "{}")
        if isinstance(qualifiers, str):
            try:
                qualifiers = json.loads(qualifiers)
            except Exception:
                qualifiers = {}
        qualifier_str = "; ".join(f"{k}: {v}" for k, v in qualifiers.items()) if qualifiers else ""

        fact_rows.append(f"""<tr>
            <td title="{_esc(f.get('doc_title', ''))}">{_esc(_truncate(f.get('doc_title', ''), 20))}</td>
            <td><span class="badge" style="background:{_type_color(f.get('fact_type', ''))}">{_esc(f.get('fact_type', ''))}</span></td>
            <td>{_esc(f.get('subject_text', ''))}</td>
            <td>{_esc(f.get('predicate', ''))}</td>
            <td>{_esc(f.get('object_text', '') or '')}</td>
            <td class="num">{_format_num(f.get('value_num'))}</td>
            <td>{_esc(f.get('unit', '') or '')}</td>
            <td>{_esc(f.get('time_expr', '') or '')}</td>
            <td>{_esc(f.get('location_text', '') or '')}</td>
            <td>{_esc(qualifier_str)}</td>
            <td><span class="status-badge" style="background:{color}">{_esc(review_status)}</span></td>
            <td class="num">{f.get('confidence_score', 0):.2f}</td>
            <td class="evidence" title="{_esc(f.get('evidence_text', ''))}">{_esc(_truncate(f.get('evidence_text', ''), 60))}</td>
            <td>{_esc(f.get('review_note', '') or '')}</td>
        </tr>""")

    # 处理结果摘要行
    summary_rows = []
    for r in process_results:
        s = r.get("stats") or {}
        summary_rows.append(f"""<tr>
            <td title="{_esc(r.get('url', ''))}">{_esc(_truncate(r.get('url', ''), 60))}</td>
            <td>{_esc(r.get('doc_id', '')[:8] if r.get('doc_id') else 'N/A')}</td>
            <td class="num">{s.get('evidences', 0)}</td>
            <td class="num">{s.get('facts', 0)}</td>
            <td class="num" style="color:#28a745">{s.get('passed', 0)}</td>
            <td class="num" style="color:#dc3545">{s.get('rejected', 0)}</td>
            <td class="num" style="color:#ffc107">{s.get('uncertain', 0)}</td>
            <td class="num">{s.get('duplicates', 0)}</td>
            <td style="color:{'#dc3545' if r.get('error') else '#28a745'}">{_esc(r.get('error') or '✓ 成功')}</td>
        </tr>""")

    # 文档信息行
    doc_rows = []
    for d in documents:
        doc_rows.append(f"""<tr>
            <td>{_esc(d.get('title', ''))}</td>
            <td><a href="{_esc(d.get('url', ''))}" target="_blank">链接</a></td>
            <td>{_esc(d.get('publish_time', '') or '')}</td>
            <td class="num">{d.get('text_length', 0):,}</td>
            <td>{_esc(d.get('status', ''))}</td>
            <td>{_esc(d.get('crawl_time', ''))}</td>
        </tr>""")

    # LLM 任务行
    task_rows = []
    for t in task_stats:
        task_rows.append(f"""<tr>
            <td>{_esc(t.get('document_id', '')[:8])}</td>
            <td>{_esc(t.get('task_type', ''))}</td>
            <td>{_esc(t.get('status', ''))}</td>
            <td class="num">{t.get('input_tokens', 0):,}</td>
            <td class="num">{t.get('output_tokens', 0):,}</td>
            <td>{_esc(t.get('model_name', '') or '')}</td>
        </tr>""")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>资讯提取报告 — {datetime.now().strftime('%Y-%m-%d %H:%M')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background: #f5f7fa; color: #333; padding: 20px; }}
        h1 {{ text-align: center; margin-bottom: 8px; color: #1a1a2e; }}
        .subtitle {{ text-align: center; color: #666; margin-bottom: 24px; font-size: 14px; }}
        .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }}
        .card {{ background: #fff; border-radius: 8px; padding: 16px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .card .num {{ font-size: 28px; font-weight: 700; color: #1a73e8; }}
        .card .label {{ font-size: 12px; color: #888; margin-top: 4px; }}
        section {{ background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        h2 {{ margin-bottom: 12px; color: #1a1a2e; font-size: 18px; border-bottom: 2px solid #1a73e8; padding-bottom: 6px; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th {{ background: #f0f4f8; color: #333; font-weight: 600; text-align: left; padding: 8px 10px; position: sticky; top: 0; }}
        td {{ padding: 6px 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
        tr:hover {{ background: #f9fbfd; }}
        .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
        .badge {{ color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
        .status-badge {{ color: #fff; padding: 2px 8px; border-radius: 10px; font-size: 11px; white-space: nowrap; }}
        .evidence {{ max-width: 300px; font-size: 12px; color: #666; }}
        a {{ color: #1a73e8; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .filter-bar {{ margin-bottom: 12px; }}
        .filter-bar select, .filter-bar input {{
            padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; margin-right: 8px;
        }}
        .stats-inline {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }}
        .stats-inline .tag {{ padding: 4px 12px; border-radius: 12px; font-size: 12px; color: #fff; }}
    </style>
</head>
<body>
    <h1>资讯颗粒化提取报告</h1>
    <p class="subtitle">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 共 {len(process_results)} 篇资讯 | {len(facts)} 条事实原子</p>

    <!-- 汇总卡片 -->
    <div class="summary-cards">
        <div class="card"><div class="num">{len(process_results)}</div><div class="label">资讯文档</div></div>
        <div class="card"><div class="num">{len(facts)}</div><div class="label">事实原子</div></div>
        <div class="card"><div class="num" style="color:#28a745">{status_counts.get('自动通过', 0) + status_counts.get('人工通过', 0)}</div><div class="label">通过</div></div>
        <div class="card"><div class="num" style="color:#ffc107">{status_counts.get('待人工审核', 0)}</div><div class="label">待人工审核</div></div>
        <div class="card"><div class="num" style="color:#dc3545">{status_counts.get('已拒绝', 0)}</div><div class="label">拒绝</div></div>
        <div class="card"><div class="num" style="color:#6c757d">{status_counts.get('重复', 0)}</div><div class="label">重复</div></div>
        <div class="card"><div class="num">{total_input_tokens + total_output_tokens:,}</div><div class="label">总 Token 消耗</div></div>
    </div>

    <!-- 按事实类型统计 -->
    <div class="stats-inline">
        {"".join(f'<span class="tag" style="background:{_type_color(t)}">{t}: {c}</span>' for t, c in sorted(type_counts.items(), key=lambda x: -x[1]))}
    </div>

    <!-- 处理摘要 -->
    <section>
        <h2>处理摘要</h2>
        <table>
            <thead><tr>
                <th>URL</th><th>Doc ID</th><th>证据</th><th>事实</th>
                <th>通过</th><th>拒绝</th><th>不确定</th><th>重复</th><th>状态</th>
            </tr></thead>
            <tbody>{"".join(summary_rows)}</tbody>
        </table>
    </section>

    <!-- 文档信息 -->
    <section>
        <h2>文档信息</h2>
        <table>
            <thead><tr>
                <th>标题</th><th>链接</th><th>发布时间</th><th>字符数</th><th>状态</th><th>爬取时间</th>
            </tr></thead>
            <tbody>{"".join(doc_rows)}</tbody>
        </table>
    </section>

    <!-- 事实原子明细 -->
    <section>
        <h2>事实原子明细 ({len(facts)} 条)</h2>
        <div class="filter-bar">
            <select id="filterType" onchange="filterTable()">
                <option value="">全部类型</option>
                {"".join(f'<option value="{t}">{t} ({c})</option>' for t, c in sorted(type_counts.items()))}
            </select>
            <select id="filterStatus" onchange="filterTable()">
                <option value="">全部状态</option>
                {"".join(f'<option value="{s}">{s} ({c})</option>' for s, c in sorted(status_counts.items()))}
            </select>
            <input type="text" id="filterText" placeholder="搜索主体/谓词/客体..." oninput="filterTable()">
        </div>
        <div style="overflow-x:auto;">
        <table id="factTable">
            <thead><tr>
                <th>文档</th><th>类型</th><th>主体</th><th>谓词</th><th>客体</th>
                <th>数值</th><th>单位</th><th>时间</th><th>地点</th><th>限定词</th>
                <th>审核状态</th><th>置信度</th><th>证据原文</th><th>审核备注</th>
            </tr></thead>
            <tbody>{"".join(fact_rows)}</tbody>
        </table>
        </div>
    </section>

    <!-- LLM 调用统计 -->
    <section>
        <h2>LLM 调用统计 (输入 {total_input_tokens:,} + 输出 {total_output_tokens:,} = {total_input_tokens + total_output_tokens:,} tokens)</h2>
        <table>
            <thead><tr>
                <th>文档</th><th>任务类型</th><th>状态</th><th>输入 Token</th><th>输出 Token</th><th>模型</th>
            </tr></thead>
            <tbody>{"".join(task_rows)}</tbody>
        </table>
    </section>

    <script>
    function filterTable() {{
        const typeVal = document.getElementById('filterType').value.toLowerCase();
        const statusVal = document.getElementById('filterStatus').value;
        const textVal = document.getElementById('filterText').value.toLowerCase();
        const rows = document.querySelectorAll('#factTable tbody tr');
        rows.forEach(row => {{
            const cells = row.querySelectorAll('td');
            const type = cells[1]?.textContent?.toLowerCase() || '';
            const status = cells[10]?.textContent || '';
            const subject = cells[2]?.textContent?.toLowerCase() || '';
            const predicate = cells[3]?.textContent?.toLowerCase() || '';
            const object = cells[4]?.textContent?.toLowerCase() || '';
            const matchType = !typeVal || type.includes(typeVal);
            const matchStatus = !statusVal || status === statusVal;
            const matchText = !textVal || subject.includes(textVal) || predicate.includes(textVal) || object.includes(textVal);
            row.style.display = (matchType && matchStatus && matchText) ? '' : 'none';
        }});
    }}
    </script>
</body>
</html>"""
    return html


def _esc(text: str) -> str:
    """HTML 转义"""
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def _truncate(text: str, max_len: int) -> str:
    """截断长文本"""
    if not text:
        return ""
    return text[:max_len] + "…" if len(text) > max_len else text


def _format_num(val) -> str:
    """格式化数值"""
    if val is None:
        return ""
    if isinstance(val, float):
        if val == int(val):
            return f"{int(val):,}"
        return f"{val:,.2f}"
    return str(val)


def _type_color(fact_type: str) -> str:
    """事实类型颜色"""
    colors = {
        "FINANCIAL_METRIC": "#1a73e8",
        "SALES_VOLUME": "#e8710a",
        "CAPACITY": "#0d904f",
        "INVESTMENT": "#9c27b0",
        "EXPANSION": "#00acc1",
        "MARKET_SHARE": "#c62828",
        "COMPETITIVE_RANKING": "#5c6bc0",
        "COOPERATION": "#2e7d32",
        "PRICE_CHANGE": "#ef6c00",
    }
    return colors.get(fact_type, "#757575")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="爬取资讯 → 提取事实 → 生成 HTML 报告")
    parser.add_argument("--urls-file", type=str, default=str(URLS_FILE),
                        help="URL 列表文件路径")
    parser.add_argument("--output", type=str, default=None,
                        help="输出 HTML 文件路径")
    parser.add_argument("--skip-extract", action="store_true",
                        help="跳过提取步骤，仅从 DB 生成报告（需先完成提取）")
    parser.add_argument("--limit", type=int, default=0,
                        help="限制处理 URL 数量（0=全部）")
    args = parser.parse_args()

    urls_path = Path(args.urls_file)
    if not urls_path.exists():
        print(f"URL 文件不存在: {urls_path}")
        return

    output_path = Path(args.output) if args.output else OUTPUT_FILE

    urls = load_urls(urls_path)
    if args.limit > 0:
        urls = urls[:args.limit]

    print(f"加载 {len(urls)} 个 URL")
    print(f"输出文件: {output_path}")

    if args.skip_extract:
        # 从 DB 中查找这些 URL 对应的文档
        conn = get_connection()
        try:
            doc_ids = []
            for url in urls:
                row = conn.execute(
                    "SELECT id FROM source_document WHERE url=?", (url,)
                ).fetchone()
                if row:
                    doc_ids.append(row["id"])
            process_results = [{"url": u, "doc_id": d, "stats": {}, "error": None}
                               for u, d in zip(urls, doc_ids)]
        finally:
            conn.close()
    else:
        process_results = crawl_and_extract(urls)
        doc_ids = [r["doc_id"] for r in process_results if r.get("doc_id")]

    # 查询结果
    facts = query_all_facts(doc_ids)
    documents = query_documents(doc_ids)
    task_stats = query_task_stats(doc_ids)

    # 生成 HTML
    html = generate_html_report(process_results, facts, documents, task_stats)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"\n报告已生成: {output_path}")
    print(f"共 {len(facts)} 条事实原子")


if __name__ == "__main__":
    main()
