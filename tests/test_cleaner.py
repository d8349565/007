"""cleaner 模块单元测试"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.cleaner import clean_text


def test_strip_html_tags():
    html = "<p>这是一段<b>测试</b>文本</p>"
    result = clean_text(html)
    assert "<p>" not in result
    assert "<b>" not in result
    assert "测试" in result
    assert "文本" in result


def test_remove_extra_whitespace():
    text = "这是   测试    文本\n\n\n\n段落二"
    result = clean_text(text)
    # 不应有连续多个空行
    assert "\n\n\n" not in result


def test_remove_ad_patterns():
    text = "正文内容。\n\n免责声明：本文不构成投资建议。\n\n更多内容"
    result = clean_text(text)
    assert "免责声明" not in result


def test_preserve_chinese_text():
    text = "2024年第一季度，宁德时代实现营收889.37亿元，同比增长26.34%。"
    result = clean_text(text)
    assert "889.37" in result
    assert "26.34%" in result


def test_empty_input():
    assert clean_text("") == ""
    assert clean_text("   \n\n  ") == ""


def test_html_table_basic():
    html = "<table><tr><td>指标</td><td>数值</td></tr><tr><td>营收</td><td>100亿</td></tr></table>"
    result = clean_text(html)
    assert "营收" in result
    assert "100亿" in result


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
