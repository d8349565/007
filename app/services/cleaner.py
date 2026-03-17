"""文本清洗模块：去噪、去广告、去HTML标签、基础表格保留"""

import re

from app.config import get_config
from app.logger import get_logger

logger = get_logger(__name__)

# 广告/导航/免责声明常见关键词
AD_PATTERNS = [
    r"(点击|扫码|关注|订阅|加入)(我们的)?[^。]{0,20}(公众号|微信|群|社群)",
    r"(免责声明|版权声明|法律声明|声明)[：:]",
    r"(转载|来源)[：:].*?(网|报|号|社|台)",
    r"(联系我们|联系方式|电话|邮箱|地址)[：:]",
    r"(上一篇|下一篇|相关阅读|推荐阅读|热门文章)",
    r"(首页|导航|登录|注册|返回顶部)",
    r"(©|版权所有|All [Rr]ights [Rr]eserved)",
    r"(广告|推广|赞助|商务合作)",
]

# HTML 标签
HTML_TAG_RE = re.compile(r"<[^>]+>")

# 多余空白
MULTI_BLANK_LINE_RE = re.compile(r"\n{3,}")
MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# 表格标记（保留 Markdown 表格）
MD_TABLE_RE = re.compile(r"^\|.*\|$", re.MULTILINE)


def clean_text(raw_text: str, cfg: dict | None = None) -> str:
    """对原始文本执行全流程清洗"""
    if cfg is None:
        cfg = get_config().get("cleaner", {})

    text = raw_text

    # 1. 去除 HTML 标签（保留文本内容）
    if cfg.get("strip_html_tags", True):
        text = _strip_html(text)

    # 2. 去除广告/导航/免责
    if cfg.get("remove_ads", True):
        text = _remove_ad_lines(text)

    # 3. 规范化空白
    if cfg.get("normalize_whitespace", True):
        text = _normalize_whitespace(text)

    logger.debug("清洗完成: %d → %d 字符", len(raw_text), len(text))
    return text.strip()


def _strip_html(text: str) -> str:
    """去除 HTML 标签，保留内容"""
    # 先处理 <br> → 换行
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # 处理 <p> → 换行
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # 处理 <table> 简单提取
    text = _extract_simple_tables(text)
    # 去除其余标签
    text = HTML_TAG_RE.sub("", text)
    # 解码常见 HTML 实体
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&quot;", '"')
    return text


def _extract_simple_tables(text: str) -> str:
    """将简单 HTML 表格转换为文本表示"""
    # 提取 <table>...</table> 中的文本
    table_pattern = re.compile(
        r"<table[^>]*>(.*?)</table>", re.DOTALL | re.IGNORECASE
    )

    def _table_to_text(match: re.Match) -> str:
        table_html = match.group(1)
        rows = re.findall(
            r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE
        )
        result_lines = []
        for row in rows:
            cells = re.findall(
                r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE
            )
            # 去除 cell 内标签
            clean_cells = [HTML_TAG_RE.sub("", c).strip() for c in cells]
            if any(clean_cells):
                result_lines.append(" | ".join(clean_cells))
        return "\n".join(result_lines)

    return table_pattern.sub(_table_to_text, text)


def _remove_ad_lines(text: str) -> str:
    """按行移除广告/导航/免责声明"""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        is_ad = False
        for pattern in AD_PATTERNS:
            if re.search(pattern, stripped):
                is_ad = True
                logger.debug("移除广告行: %s", stripped[:50])
                break
        if not is_ad:
            cleaned.append(line)
    return "\n".join(cleaned)


def _normalize_whitespace(text: str) -> str:
    """规范化空白：合并多余空行和空格"""
    text = MULTI_SPACE_RE.sub(" ", text)
    text = MULTI_BLANK_LINE_RE.sub("\n\n", text)
    return text
