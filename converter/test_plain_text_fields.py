"""純文字欄位不可帶行內 markdown（圖說／alt／FAQ 題目）。

2026-08-25 正式站 5601 實際踩到：Notion 圖說上的粗體同步後在站上顯示成字面的
`**粗體**`。原因是圖說走了 rich_text()，那個函式會把 annotation 轉成行內
markdown——但圖說最終進的是 WP 媒體庫的 Caption（post_excerpt）與 Alt text
（post meta），那兩個欄位不解析 markdown。

同一類問題還有 FAQ 題目：它最終是 WP 的文章標題，也是純文字欄位。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_plain_text_fields.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402
import notion_blocks as nb  # noqa: E402


def img(caption_items):
    return [{"id": "i", "type": "image",
             "image": {"external": {"url": "https://x/a.png"}, "caption": caption_items}}]


def rt(text, **ann):
    return {"plain_text": text, "annotations": ann}


# ── 圖說 ────────────────────────────────────────────────

def test_粗體圖說不會變成字面的星號():
    """實際踩到的案例。"""
    md, _ = nb.blocks_to_markdown(img([rt("庫存頁面的"), rt("篩選列", bold=True)]))
    assert "![庫存頁面的篩選列]" in md
    assert "**" not in md


def test_行內code圖說不會帶反引號():
    md, _ = nb.blocks_to_markdown(img([rt("點擊 "), rt("Save", code=True), rt(" 按鈕")]))
    assert "![點擊 Save 按鈕]" in md
    assert "`" not in md


def caption_of(md):
    """從 `![圖說](url)` 取出圖說本身——斷言要看的是圖說內容，
    不是整行（整行必然含有 markdown 圖片語法的 `![` 與 `](`）。"""
    import re as _re
    m = _re.search(r"!\[(.*?)\]\(", md, _re.S)
    return m.group(1) if m else ""


def test_斜體與連結也一併去掉():
    md, _ = nb.blocks_to_markdown(img([
        rt("見 "), rt("說明", italic=True),
        {"plain_text": "文件", "annotations": {}, "href": "https://example.com"}]))
    cap = caption_of(md)
    assert cap == "見 說明文件", cap
    for mark in ("*", "[", "`"):
        assert mark not in cap


def test_alt標記在純文字下仍然work():
    md, _ = nb.blocks_to_markdown(img([
        rt("篩選列", bold=True), rt(" [alt: 頁面上方的篩選與搜尋列]")]))
    assert '![篩選列](https://x/a.png "頁面上方的篩選與搜尋列")' in md


def test_plain_text不動純文字():
    assert nb.plain_text([rt("已經是純文字")]) == "已經是純文字"
    assert nb.plain_text([]) == ""
    assert nb.plain_text(None) == ""


# ── strip_inline_md ────────────────────────────────────

def test_脫掉成對標記():
    cases = [
        ("**粗體**", "粗體"),
        ("*斜體*", "斜體"),
        ("`code`", "code"),
        ("[連結文字](https://x)", "連結文字"),
        ("混合 **粗** 與 `碼` 與 *斜*", "混合 粗 與 碼 與 斜"),
    ]
    for src, want in cases:
        assert n2e.strip_inline_md(src) == want, src


def test_單獨的星號與反引號原樣保留():
    """否則會把「2*3」這種內容改壞。"""
    for src in ("What is 2*3?", "價格 * 數量", "反引號 ` 單獨出現"):
        assert n2e.strip_inline_md(src) == src, src


def test_空值不炸():
    assert n2e.strip_inline_md("") == ""
    assert n2e.strip_inline_md(None) == ""


# ── FAQ 題目 ────────────────────────────────────────────

FAQ_MD = """## Overview

Some text.

## FAQ

#### What is **New Order Frozen Period**?

It holds new orders.

#### How do I use `Release Order`?

Click it.
"""


def test_faq題目去掉行內標記():
    """FAQ 題目最終是 WP 的文章標題（純文字），不能帶 markdown。"""
    _, faq_items, _ = n2e.convert(FAQ_MD, "T", "t", sync_date="July 29, 2026")
    qs = [q["question"] for sec in faq_items for q in sec["items"]] \
        if faq_items and isinstance(faq_items[0], dict) and "items" in faq_items[0] \
        else [q["question"] for q in faq_items]
    assert "What is New Order Frozen Period?" in qs, qs
    assert "How do I use Release Order?" in qs, qs
    assert not any("**" in q or "`" in q for q in qs), qs
