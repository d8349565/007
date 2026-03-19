"""pipeline 基础测试（不调用 LLM，仅测试导入/清洗/切分流程）"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.db import init_db, get_connection
from app.services.importer import import_file, import_paste
from app.services.cleaner import clean_text
from app.services.text_splitter import split_text


def test_import_and_read():
    """测试导入 + 数据库读取"""
    init_db()
    doc_id = import_paste(
        text="这是一篇测试文档，用于验证导入流程。\n\n第二段内容。",
        title="测试文档",
    )
    assert doc_id

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM source_document WHERE id=?", (doc_id,)
        ).fetchone()
        assert row is not None
        assert row["title"] == "测试文档"
        assert "测试文档" in row["raw_text"]
    finally:
        conn.close()


def test_import_dedup():
    """测试重复导入去重"""
    init_db()
    text = "完全相同的内容用于去重测试。"
    id1 = import_paste(text=text, title="去重测试1")
    id2 = import_paste(text=text, title="去重测试2")
    assert id1 == id2  # 相同 content_hash，返回同一 ID


def test_import_file_roundtrip():
    """测试文件导入"""
    init_db()
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8",
    ) as f:
        f.write("临时文件内容用于测试文件导入功能。")
        f.flush()
        temp_path = f.name

    try:
        doc_id = import_file(temp_path)
        assert doc_id
    finally:
        os.unlink(temp_path)


def test_clean_and_split_pipeline():
    """测试清洗 + 切分流程"""
    raw = """
    <p>2024年第一季度报告</p>
    
    <p>宁德时代发布2024年第一季度报告。报告期内公司实现营业收入889.37亿元，同比增长26.34%。
    净利润达到105.10亿元，创历史同期最高水平。</p>
    
    <p>公司表示将继续加大研发投入，推动技术创新和产业升级。</p>
    
    免责声明：本文不构成投资建议。
    """

    cleaned = clean_text(raw)
    assert "<p>" not in cleaned
    assert "889.37" in cleaned
    assert "免责声明" not in cleaned

    chunks = split_text(cleaned)
    assert len(chunks) >= 1
    assert all(len(c["chunk_text"].strip()) > 0 for c in chunks)


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
