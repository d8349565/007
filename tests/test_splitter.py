"""text_splitter 模块单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.text_splitter import split_text, count_effective_chars


def test_short_text_passthrough():
    """短文本不应被切分"""
    text = "这是一段短文本，总共不超过一百个字符。"
    chunks = split_text(text)
    assert len(chunks) == 1
    assert chunks[0]["chunk_text"] == text


def test_count_effective_chars_chinese():
    text = "你好世界"
    assert count_effective_chars(text) == 4


def test_count_effective_chars_english():
    text = "hello world"
    # "hello" + " " + "world" ≈ depends on implementation
    count = count_effective_chars(text)
    assert count > 0


def test_count_effective_chars_mixed():
    text = "中文English混合123"
    count = count_effective_chars(text)
    assert count > 0


def test_split_long_text():
    """长文本应被切分为多个 chunk"""
    paragraphs = []
    for i in range(20):
        paragraphs.append(f"这是第{i+1}个段落。" * 10)  # 每段约 80-100 字
    text = "\n\n".join(paragraphs)

    chunks = split_text(text)
    assert len(chunks) > 1
    # 每个 chunk 都不应为空
    for c in chunks:
        assert len(c["chunk_text"].strip()) > 0


def test_empty_input():
    chunks = split_text("")
    assert chunks == []

    chunks = split_text("   \n\n   ")
    assert chunks == []


def test_single_paragraph():
    text = "只有一个段落的文本，内容丰富但整体不长。" * 5
    chunks = split_text(text)
    assert len(chunks) >= 1


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"  ✅ {name}")
            except AssertionError as e:
                print(f"  ❌ {name}: {e}")
            except Exception as e:
                print(f"  ⚠️ {name}: {e}")
    print("测试完成")
