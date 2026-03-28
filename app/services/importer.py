"""文档导入模块：支持 txt/md/URL + 批量导入 + 去重覆盖"""

import hashlib
import re
import uuid
from pathlib import Path
from typing import Generator

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from app.config import get_config
from app.logger import get_logger
from app.models.db import get_connection

logger = get_logger(__name__)


def generate_content_hash(text: str) -> str:
    """生成内容指纹用于去重"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_file(
    file_path: str,
    title: str | None = None,
    author: str | None = None,
    publish_time: str | None = None,
    source: str | None = None,
) -> str:
    """
    导入单个文件。

    返回: document_id
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    cfg = get_config().get("importer", {})
    supported = cfg.get("supported_extensions", [".txt", ".md"])
    if path.suffix.lower() not in supported:
        raise ValueError(f"不支持的文件类型: {path.suffix}（支持: {supported}）")

    raw_text = path.read_text(encoding="utf-8")
    if not title:
        title = path.stem

    return _upsert_document(
        source_type="文件",
        source_name=source or str(path.name),
        title=title,
        author=author,
        url=None,
        publish_time=publish_time,
        raw_text=raw_text,
    )


def import_url(
    url: str,
    title: str | None = None,
    author: str | None = None,
    publish_time: str | None = None,
    source: str | None = None,
) -> str:
    """
    从 URL 抓取正文并导入。

    返回: document_id
    """
    cfg = get_config().get("importer", {})
    timeout = cfg.get("url_timeout", 30)

    logger.info("抓取 URL: %s", url)
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": "Mozilla/5.0 (compatible; FactExtractor/1.0)"
    })
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "lxml")

    # 移除 script/style/nav 等
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    # 提取正文
    # 优先尝试 article / main 标签
    body = soup.find("article") or soup.find("main") or soup.find("body")
    raw_text = _extract_article_text(body if body else soup)

    if not title:
        title = _extract_title(soup, raw_text, url)

    return _upsert_document(
        source_type="网址",
        source_name=source or url[:200],
        title=title,
        author=author,
        url=url,
        publish_time=publish_time,
        raw_text=raw_text,
    )


def import_paste(
    text: str,
    title: str,
    author: str | None = None,
    publish_time: str | None = None,
) -> str:
    """
    导入手工粘贴文本。

    返回: document_id
    """
    return _upsert_document(
        source_type="粘贴",
        source_name=None,
        title=title,
        author=author,
        url=None,
        publish_time=publish_time,
        raw_text=text,
    )


def import_batch(
    folder_path: str,
    author: str | None = None,
    pattern: str = "*.txt",
) -> list[str]:
    """
    批量导入文件夹中的所有支持文件。

    返回: document_id 列表
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        raise NotADirectoryError(f"不是目录: {folder_path}")

    cfg = get_config().get("importer", {})
    supported = cfg.get("supported_extensions", [".txt", ".md"])
    show_progress = cfg.get("batch_progress", True)

    files = [
        f for f in sorted(folder.iterdir())
        if f.is_file() and f.suffix.lower() in supported
        and f.match(pattern)
    ]

    if not files:
        logger.warning("目录中没有可导入的文件: %s", folder_path)
        return []

    logger.info("批量导入: 发现 %d 个文件", len(files))
    doc_ids = []

    iterator = tqdm(files, desc="导入文档") if show_progress else files
    for f in iterator:
        try:
            doc_id = import_file(str(f), author=author)
            doc_ids.append(doc_id)
        except Exception as e:
            logger.error("导入失败 [%s]: %s", f.name, e)

    logger.info("批量导入完成: %d/%d 成功", len(doc_ids), len(files))
    return doc_ids


def _extract_article_text(root) -> str:
    """
    从 HTML 根节点提取正文文本。

    自定义遍历策略：
    - 块级元素（p, div, section, li 等）：输出前后换行，保持段落感
    - 行内元素（span, a, em, strong 等）：直接拼接字符不断开
    - 逐字拆分的 span（Sohu 等平台的特殊结构）：无缝拼接

    这样搜狐等逐字包 span 的页面不会变成"每字一行"的破碎文本。
    """
    BLOCK_TAGS = {"p", "div", "section", "article", "li", "blockquote",
                  "h1", "h2", "h3", "h4", "h5", "h6",
                  "tr", "th", "td", "br"}
    # 不递归进入的标签
    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside",
                 "iframe", "noscript", "svg", "canvas"}

    parts = []
    _walk_text(root, parts, BLOCK_TAGS, SKIP_TAGS, inside_block=False)
    text = "".join(parts)

    # 后处理：合并连续空白、移除孤立单字符行（逐字拆分的残留）
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 如果一行只有一个字符且两边有空行，很可能是逐字拆分的残留；跳过
        if len(line) == 1 and ord(line) < 0x4E00:  # ASCII/数字单字
            continue
        cleaned_lines.append(line)
    # 段落之间保留空行
    return "\n\n".join(cleaned_lines)


def _walk_text(node, parts: list, block_tags: set, skip_tags: set, inside_block: bool) -> None:
    """递归遍历 DOM 节点，收集文本"""
    if not hasattr(node, "name") or node.name is None:
        # 文本节点
        text = str(node)
        if inside_block:
            parts.append(text)
        else:
            # 不在块内时做基础清理
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                parts.append(text)
        return

    name = node.name.lower()

    if name in skip_tags:
        return

    is_block = name in block_tags

    # 进入块级标签：先加换行
    if is_block:
        # 避免连续加多个换行
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    # 递归处理子节点
    for child in node.children:
        _walk_text(child, parts, block_tags, skip_tags, inside_block=is_block or inside_block)

    # 离开块级标签：加换行
    if is_block:
        if parts and not parts[-1].endswith("\n"):
            parts.append("\n")


def _extract_title(soup: BeautifulSoup, raw_text: str, fallback: str) -> str:
    """
    多策略提取文章标题。

    优先级：
    1. og:title meta 标签（微信公众号等平台通用）
    2. h1 标签
    3. <title> 标签
    4. 正文第一个非空行
    5. fallback（通常是 URL）
    """
    # 策略 1: og:title（微信/知乎/头条等平台标准）
    og = soup.find("meta", property="og:title")
    if og and og.get("content", "").strip():
        return og["content"].strip()

    # 策略 2: h1 标签
    h1 = soup.find("h1")
    if h1 and h1.get_text().strip():
        return h1.get_text().strip()

    # 策略 3: <title> 标签
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text().strip():
        return title_tag.get_text().strip()

    # 策略 4: 正文第一个非空有意义行（至少 4 个字符，排除日期/作者行）
    for line in raw_text.split("\n"):
        line = line.strip()
        if len(line) >= 4 and not line.startswith("http"):
            return line[:200]

    # 策略 5: fallback
    logger.warning("无法提取标题，使用 fallback: %s", fallback[:50])
    return fallback


def _upsert_document(
    source_type: str,
    source_name: str | None,
    title: str,
    author: str | None,
    url: str | None,
    publish_time: str | None,
    raw_text: str,
) -> str:
    """插入或更新文档（基于 content_hash 去重）"""
    content_hash = generate_content_hash(raw_text)

    conn = get_connection()
    try:
        # URL 类型优先按 URL 去重（URL 是唯一标识，content_hash 可能因动态内容而不同）
        if url:
            row = conn.execute(
                "SELECT id FROM source_document WHERE url = ?",
                (url,),
            ).fetchone()
            if row:
                doc_id = row["id"]
                conn.execute(
                    """UPDATE source_document SET
                        source_type=?, source_name=?, title=?, author=?,
                        url=?, publish_time=?, raw_text=?,
                        crawl_time=CURRENT_TIMESTAMP, status='待处理'
                    WHERE id=?""",
                    (source_type, source_name, title, author,
                     url, publish_time, raw_text, doc_id),
                )
                conn.commit()
                logger.info("文档 URL 已存在，更新: %s [%s]", title, doc_id[:8])
                return doc_id
        else:
            # 非 URL 类型按 content_hash 去重
            row = conn.execute(
                "SELECT id FROM source_document WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()

        if row:
            doc_id = row["id"]
            conn.execute(
                """UPDATE source_document SET
                    source_type=?, source_name=?, title=?, author=?,
                    url=?, publish_time=?, raw_text=?,
                    crawl_time=CURRENT_TIMESTAMP, status='待处理'
                WHERE id=?""",
                (source_type, source_name, title, author,
                 url, publish_time, raw_text, doc_id),
            )
            conn.commit()
            logger.info("文档已更新: %s [%s]", title, doc_id[:8])
            return doc_id

        # 新插入
        doc_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO source_document
            (id, source_type, source_name, title, author, url,
             publish_time, raw_text, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, source_type, source_name, title, author,
             url, publish_time, raw_text, content_hash),
        )
        conn.commit()
        logger.info("文档已导入: %s [%s]", title, doc_id[:8])
        return doc_id
    finally:
        conn.close()
