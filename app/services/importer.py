"""文档导入模块：支持 txt/md/URL + 批量导入 + 去重覆盖"""

import hashlib
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
        source_type="file",
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
    raw_text = body.get_text(separator="\n") if body else soup.get_text(separator="\n")

    if not title:
        title = _extract_title(soup, raw_text, url)

    return _upsert_document(
        source_type="url",
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
        source_type="paste",
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
        # 检查是否已存在
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
                    crawl_time=CURRENT_TIMESTAMP, status='ACTIVE'
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
