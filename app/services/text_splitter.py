"""文本切分模块：短文直通 + 段落组块"""

import re

from app.config import get_config
from app.logger import get_logger

logger = get_logger(__name__)

# 中文字符计数（排除标点和空白）
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# 段落分割
PARA_SPLIT_RE = re.compile(r"\n\s*\n")

# 章节标题识别
SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"[一二三四五六七八九十]+[、.．]"        # 中文序号
    r"|#{1,4}\s"                              # Markdown 标题
    r"|\d+[.、．]\d*\s*"                      # 数字序号
    r"|第[一二三四五六七八九十\d]+[章节部分]"  # 第X章/节
    r")",
    re.MULTILINE,
)

# 句子断点
SENTENCE_BREAK_RE = re.compile(r"([。！？；;!?])")


def count_chinese_chars(text: str) -> int:
    """统计中文字符数"""
    return len(CJK_RE.findall(text))


def count_effective_chars(text: str) -> int:
    """统计有效字符数（中文字 + 英文单词近似）"""
    cjk = count_chinese_chars(text)
    # 英文单词近似：非中文非空白字符数 / 5
    non_cjk = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\s]", "", text)
    return cjk + len(non_cjk) // 5


def split_text(
    cleaned_text: str,
    doc_id: str = "",
    cfg: dict | None = None,
) -> list[dict]:
    """
    切分文本为 chunk 列表。

    返回:
        [{"chunk_index": 0, "chunk_text": "...", "char_start": 0, "char_end": 100}, ...]
    """
    if cfg is None:
        cfg = get_config().get("splitter", {})

    # 空输入直接返回空列表
    if not cleaned_text or not cleaned_text.strip():
        return []

    threshold = cfg.get("short_text_threshold", 1200)
    chunk_ideal = cfg.get("chunk_ideal", 700)
    chunk_max = cfg.get("chunk_max", 1200)
    overlap_size = cfg.get("overlap", 80)

    char_count = count_effective_chars(cleaned_text)

    # 策略 A：短文直通
    if char_count <= threshold:
        logger.info("[%s] 短文直通: %d 字", doc_id, char_count)
        return [
            {
                "chunk_index": 0,
                "chunk_text": cleaned_text,
                "char_start": 0,
                "char_end": len(cleaned_text),
            }
        ]

    # 策略 B：段落组块
    paragraphs = _split_to_paragraphs(cleaned_text)
    chunks = _group_paragraphs(
        paragraphs, chunk_ideal, chunk_max, overlap_size
    )

    logger.info(
        "[%s] 段落组块: %d 字 → %d 块", doc_id, char_count, len(chunks)
    )
    return chunks


def _split_to_paragraphs(text: str) -> list[dict]:
    """将文本按段落分割，保留位置信息"""
    paragraphs = []
    parts = PARA_SPLIT_RE.split(text)
    pos = 0

    for part in parts:
        # 找到在原文中的实际位置
        idx = text.find(part, pos)
        if idx == -1:
            idx = pos
        stripped = part.strip()
        if stripped:
            paragraphs.append(
                {
                    "text": stripped,
                    "char_start": idx,
                    "char_end": idx + len(part),
                    "effective_chars": count_effective_chars(stripped),
                }
            )
        pos = idx + len(part)

    return paragraphs


def _group_paragraphs(
    paragraphs: list[dict],
    chunk_ideal: int,
    chunk_max: int,
    overlap_size: int,
) -> list[dict]:
    """将段落组装为 chunk，保留 overlap"""
    if not paragraphs:
        return []

    chunks = []
    current_paras: list[dict] = []
    current_chars = 0
    chunk_index = 0

    for para in paragraphs:
        para_chars = para["effective_chars"]

        # 单段超大：强制切出当前缓冲，然后单独成块
        if para_chars > chunk_max:
            if current_paras:
                chunks.append(_make_chunk(current_paras, chunk_index))
                chunk_index += 1
                current_paras = []
                current_chars = 0
            # 对超大段落做句级切分
            sub_chunks = _split_long_paragraph(
                para, chunk_ideal, chunk_max, chunk_index
            )
            chunks.extend(sub_chunks)
            chunk_index += len(sub_chunks)
            continue

        # 累加当前段落
        if current_chars + para_chars > chunk_max and current_paras:
            chunks.append(_make_chunk(current_paras, chunk_index))
            chunk_index += 1
            # overlap：保留最后一段
            if overlap_size > 0 and current_paras:
                last = current_paras[-1]
                if last["effective_chars"] <= overlap_size:
                    current_paras = [last]
                    current_chars = last["effective_chars"]
                else:
                    current_paras = []
                    current_chars = 0
            else:
                current_paras = []
                current_chars = 0

        current_paras.append(para)
        current_chars += para_chars

    # 剩余
    if current_paras:
        chunks.append(_make_chunk(current_paras, chunk_index))

    return chunks


def _make_chunk(paras: list[dict], index: int) -> dict:
    """从段落列表生成一个 chunk"""
    text = "\n\n".join(p["text"] for p in paras)
    return {
        "chunk_index": index,
        "chunk_text": text,
        "char_start": paras[0]["char_start"],
        "char_end": paras[-1]["char_end"],
    }


def _split_long_paragraph(
    para: dict,
    chunk_ideal: int,
    chunk_max: int,
    start_index: int,
) -> list[dict]:
    """对超长段落按句子断点切分"""
    text = para["text"]
    sentences = SENTENCE_BREAK_RE.split(text)

    # 重组：把标点归到前一句
    merged = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        if i + 1 < len(sentences) and SENTENCE_BREAK_RE.match(sentences[i + 1]):
            s += sentences[i + 1]
            i += 2
        else:
            i += 1
        if s.strip():
            merged.append(s.strip())

    chunks = []
    current_text = ""
    current_chars = 0
    idx = start_index

    for sent in merged:
        sent_chars = count_effective_chars(sent)
        if current_chars + sent_chars > chunk_max and current_text:
            chunks.append(
                {
                    "chunk_index": idx,
                    "chunk_text": current_text,
                    "char_start": para["char_start"],
                    "char_end": para["char_end"],
                }
            )
            idx += 1
            current_text = ""
            current_chars = 0

        current_text = (current_text + " " + sent).strip() if current_text else sent
        current_chars += sent_chars

    if current_text:
        chunks.append(
            {
                "chunk_index": idx,
                "chunk_text": current_text,
                "char_start": para["char_start"],
                "char_end": para["char_end"],
            }
        )

    return chunks
