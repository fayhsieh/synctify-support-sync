"""Notion 協作標記剔除測試（strip_notion_artifacts）。

漏掉任何一種標記的後果是它會出現在**公開頁面**上。2026-08-14 掃描測試站 36 篇
已發佈文章，7 篇帶著 notion-enable-hover 或 notionvc 註解（人工上稿的舊文，
Fay 決定不回頭改——那些的譯文已在正式站完成；新版本走流程時自然乾淨）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_notion_artifacts.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402

strip = n2e.strip_notion_artifacts


def test_剔除notionvc註解():
    src = "Enable it.<!-- notionvc: 6c1d0579-8e9e-4f2c-8512-d2db696cef78 -->"
    assert strip(src) == "Enable it."


def test_拆掉discussion錨點保留文字():
    src = '<span discussion-urls="https://notion.so/x">Click Submit</span> to save.'
    assert strip(src) == "Click Submit to save."


def test_拆掉notion_enable_hover保留文字():
    """實站漏出去的就是這一種。"""
    src = ('<em><span class="notion-enable-hover" data-token-index="0">'
           "Last updated: May 28, 2026</span></em>")
    assert strip(src) == "<em>Last updated: May 28, 2026</em>"


def test_巢狀span不會留下孤兒收尾標籤():
    """非貪婪比對會停在內層的 </span>，把外層那個留在原文裡。"""
    src = ('<span class="notion-enable-hover">Go to '
           '<span class="direction_step">Settings</span> now</span>')
    got = strip(src)
    assert got == 'Go to <span class="direction_step">Settings</span> now'
    assert got.count("<span") == got.count("</span>") == 1


def test_兩層都是留言標記時全部拆掉():
    src = ('<span discussion-urls="u1">outer '
           '<span class="notion-enable-hover">inner</span> tail</span>')
    assert strip(src) == "outer inner tail"


def test_同一段裡多個標記():
    src = ('<span class="notion-enable-hover">A</span> and '
           '<span class="notion-enable-hover">B</span>'
           "<!-- notionvc: abc -->")
    assert strip(src) == "A and B"


def test_殘缺的開始標籤不會無限迴圈():
    src = '<span class="notion-enable-hover">no closing tag'
    assert strip(src) == "no closing tag"


def test_乾淨的文字不受影響():
    for src in ("Plain text.",
                "Keep <strong>bold</strong> and <em>italic</em>.",
                '<span class="direction_step">Settings</span> stays.'):
        assert strip(src) == src


def test_不誤傷其他span():
    src = '<span class="direction_steps"><span class="direction_step">Save</span></span>'
    assert strip(src) == src
