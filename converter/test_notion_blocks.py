"""
Notion blocks → Markdown 測試。

重點是**端到端**：block JSON → markdown → notion2elementor → Elementor JSON，
直接斷言最終 widget 結構，確保這一層產出的 markdown 真的能被轉換器正確解析
（避免「數字清單斷編號」「表格沒被辨識」這類格式細節出錯）。

⚠️ 這裡的 block fixture 是依 Notion API schema 手工建構的合成資料，
尚未用真實 API 回傳驗證過（測試站閘門未放行前拿不到）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_notion_blocks.py -v
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402
import notion_blocks as nb  # noqa: E402

_ids = itertools.count(1)


# ---------- fixture helpers ----------

def rt(text, **ann):
    t = {"plain_text": text, "annotations": {k: True for k in ann if k != "href"}}
    if "href" in ann:
        t["href"] = ann["href"]
    return t


def blk(btype, payload=None, children=None, parent=None):
    b = {"id": f"blk-{next(_ids)}", "type": btype, btype: payload or {}}
    b["has_children"] = bool(children)
    if children is not None:
        b["children"] = children
    if parent:
        b["parent"] = {"type": "block_id", "block_id": parent}
    return b


def para(text):
    return blk("paragraph", {"rich_text": [rt(text)]})


def head(level, text):
    return blk(f"heading_{level}", {"rich_text": [rt(text)]})


def num(text, children=None):
    return blk("numbered_list_item", {"rich_text": [rt(text)]}, children)


def bullet(text, children=None):
    return blk("bulleted_list_item", {"rich_text": [rt(text)]}, children)


def image(url, caption):
    return blk("image", {"external": {"url": url}, "caption": [rt(caption)]})


def table(rows, ):
    trs = [blk("table_row", {"cells": [[rt(c)] for c in row]}) for row in rows]
    return blk("table", {"table_width": len(rows[0])}, trs)


def widgets(els):
    out = []
    for e in els:
        if e.get("elType") == "widget":
            out.append(e)
        out += widgets(e.get("elements", []))
    return out


def to_elementor(blocks):
    md, report = nb.blocks_to_markdown(blocks)
    tpl, faqs, rep = n2e.convert(md, "T", "t", sync_date="July 24, 2026")
    return md, widgets(tpl["content"]), report


# ---------- 測試 ----------

def test_heading_levels_map_one_to_one():
    """Notion heading_N → N 個井字號，夾在轉換器認得的 [2,4]。

    依 2026-08-02 真實 block 資料確認：主章節為 heading_2、FAQ 問題為 heading_4。
    """
    md, ws, _ = to_elementor([
        head(2, "Overview"), para("Body text."),
        head(3, "Sub section"),
        head(4, "FAQ question"),
    ])
    assert md.startswith("## Overview")
    assert "### Sub section" in md
    assert "#### FAQ question" in md
    heads = [w for w in ws if w["widgetType"] == "heading"]
    assert [h["settings"]["title"] for h in heads] == ["Overview", "Sub section", "FAQ question"]
    assert "header_size" not in heads[0]["settings"]          # ## → 預設 h2
    assert heads[1]["settings"]["header_size"] == "h3"
    assert heads[2]["settings"]["header_size"] == "h4"


def test_heading_1_and_deep_headings_are_clamped():
    """heading_1 併入 ##（否則 `#` 會被當段落靜默漏掉）；heading_5/6 併入 ####。"""
    md, _ws, _ = to_elementor([head(1, "Top"), head(5, "Deep"), head(6, "Deeper")])
    assert "## Top" in md and "# Top" not in md.replace("## Top", "")
    assert "#### Deep" in md
    assert "#### Deeper" in md
    assert "#####" not in md


def test_simplify_output_shape_still_parses():
    """n8n Notion 節點若開了 Simplify Output 會用 `text` 而非 `rich_text`——兩者都要能讀。"""
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"text": [{"plain_text": "Overview"}]}},
        {"id": "p", "type": "paragraph",
         "paragraph": {"text": [{"text": {"content": "Body via text.content"}}]}},
    ]
    md, _report = nb.blocks_to_markdown(blocks)
    assert "## Overview" in md
    assert "Body via text.content" in md


def test_numbered_list_contiguous_single_widget():
    """連續編號之間不可有空行，否則會被切成多個 widget、編號重來。"""
    blocks = [head(1, "Steps")] + [num(f"Step {i}") for i in range(1, 6)]
    md, ws, _ = to_elementor(blocks)
    assert "\n\n1." not in md  # 編號項之間沒有空行
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    assert len(olists) == 1
    assert len(olists[0]["settings"]["ul_icon_list"]) == 5


def test_numbered_list_with_nested_bullets():
    blocks = [
        head(1, "Steps"),
        num("First"),
        num("Pick one:", children=[bullet("Option A"), bullet("Option B")]),
        num("Last"),
    ]
    md, ws, _ = to_elementor(blocks)
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    assert len(olists) == 1
    items = olists[0]["settings"]["ul_icon_list"]
    assert len(items) == 3                      # 巢狀 bullet 不佔編號
    assert items[1]["text"].count('padding-left: 40px;') == 2
    assert "<li>" not in "".join(i["text"] for i in items)


def test_numbered_list_with_nested_image_becomes_caption():
    blocks = [
        head(1, "Steps"),
        num("Click the menu"),
        num("Select it", children=[image("https://x/img.png", "The menu")]),
        num("Done"),
    ]
    md, ws, _ = to_elementor(blocks)
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    items = olists[0]["settings"]["ul_icon_list"]
    assert len(items) == 3
    assert "[caption" in items[1]["text"]
    assert 'class="size-large"' in items[1]["text"]
    assert not [w for w in ws if w["widgetType"] == "image"]   # 未產生獨立 image widget


def test_table_becomes_html_table():
    blocks = [head(1, "Ref"), table([["Column", "Description"],
                                     ["Partner Code", "The code sent."],
                                     ["Source", "System or Custom."]])]
    md, ws, _ = to_elementor(blocks)
    assert "| Column | Description |" in md
    assert "| --- | --- |" in md
    tables = [w for w in ws if w["widgetType"] == "text-editor"
              and "<table>" in w["settings"].get("editor", "")]
    assert len(tables) == 1
    assert "<th>Column</th>" in tables[0]["settings"]["editor"]
    assert "<td>Partner Code</td>" in tables[0]["settings"]["editor"]


def test_callout_emoji_maps_to_alert_type():
    blocks = [
        head(1, "S"),
        blk("callout", {"rich_text": [rt("Heads up.")], "icon": {"emoji": "⚠️"},
                        "color": "red_background"}),
    ]
    _md, ws, _ = to_elementor(blocks)
    alerts = [w for w in ws if w["widgetType"] == "docly_alerts_box"]
    assert len(alerts) == 1
    assert alerts[0]["settings"]["alert_type"] == "danger"


def test_inline_code_and_link_and_bold():
    blocks = [head(1, "S"), blk("paragraph", {"rich_text": [
        rt("Go to "), rt("Orders > Exception Orders", code=True), rt(" and click "),
        rt("Save", bold=True), rt(" or see "), rt("docs", href="https://e.com"),
    ]})]
    _md, ws, _ = to_elementor(blocks)
    # 略過轉換器自動產生的 "Last updated" widget，找含實際內文的那個
    ed = next(w["settings"]["editor"] for w in ws
              if w["widgetType"] == "text-editor" and "Go to" in w["settings"].get("editor", ""))
    assert "[direction]Orders &gt; Exception Orders[/direction]" in ed   # 路徑用 &gt;
    assert "<strong>Save</strong>" in ed
    assert '<a href="https://e.com" target="_blank" rel="noopener">docs</a>' in ed


def test_seo_meta_and_version_history_sections_excluded():
    blocks = [
        head(1, "Overview"), para("Real content."),
        blk("paragraph", {"rich_text": [rt("SEO Meta", bold=True)]}),
        para("Title: something"),
        head(1, "Version History"), para("v2 - changed things"),
    ]
    md, _ws, report = to_elementor(blocks)
    assert "Real content." in md
    assert "SEO Meta" not in md
    assert "Title: something" not in md
    assert "Version History" not in md
    assert "v2 - changed things" not in md
    assert len(report["skipped_sections"]) == 2


def test_toggle_excluded_as_internal_note():
    blocks = [head(1, "S"), para("Visible."),
              blk("toggle", {"rich_text": [rt("Content Review Notes")]},
                  [para("internal only")])]
    md, _ws, report = to_elementor(blocks)
    assert "Visible." in md
    assert "internal only" not in md
    assert report["excluded_toggles"] == 1


def test_flat_list_is_rebuilt_into_tree():
    """n8n 的『Also Fetch Nested Blocks』回傳扁平清單，需用 parent.block_id 重建。"""
    parent = num("Pick one:")
    child_a = bullet("Option A")
    child_b = bullet("Option B")
    for c in (child_a, child_b):
        c["parent"] = {"type": "block_id", "block_id": parent["id"]}
        c.pop("children", None)
    parent.pop("children", None)
    flat = [head(1, "Steps"), parent, child_a, child_b, num("Next")]

    md, ws, _ = to_elementor(flat)
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    assert len(olists) == 1
    items = olists[0]["settings"]["ul_icon_list"]
    assert len(items) == 2                                   # 兩個編號項
    assert items[0]["text"].count("padding-left: 40px;") == 2  # 子項掛回第一項


def test_code_block_language_preserved():
    blocks = [head(1, "S"), blk("code", {"rich_text": [rt("GET /api\nHost: x")],
                                         "language": "http"})]
    _md, ws, _ = to_elementor(blocks)
    code = [w for w in ws if w["widgetType"] == "docly_code_syntax_highlighter"]
    assert len(code) == 1
    assert code[0]["settings"]["lng_type"] == "http"
    assert "GET /api" in code[0]["settings"]["source_code"]


def test_realistic_article_matches_handmade_shape():
    """
    模擬 Manage Exception Orders 的實際結構（章節＋表格＋數字清單＋步驟內嵌圖＋callout），
    斷言自動路徑產出的 widget 組成與手工整理的結果一致。
    """
    blocks = [
        head(1, "Overview"),
        para("The Exception Orders page helps you review sales orders."),
        table([["Tab", "What it shows", "What you can do"],
               ["Errors", "Unresolved error-type issues.", "Reinstate the order."],
               ["On-Hold", "Temporarily paused orders.", "Release the order."]]),

        head(1, "Review error orders"),
        para("Use the Errors tab."),
        num("Go to Orders"), num("Select the Errors tab"), num("Review the list"),
        image("https://x/errors-list.png", "Review exception orders."),

        head(1, "Reinstate an error order"),
        num("Go to Orders"),
        num("Find the order"),
        num("Select Reinstate Order", children=[image("https://x/actions.png", "Action menu.")]),
        num("Click Submit"),
        table([["Error type", "What to do"],
               ["SKU Not Found", "Select the correct SKU."]]),
        blk("callout", {"rich_text": [rt("Note", bold=True)], "icon": {"emoji": "ℹ️"},
                        "color": "blue_background"},
            [para("Missing Default Warehouse is resolved differently.")]),

        head(1, "Important notes"),
        bullet("Errors and On-Hold are different tabs."),
        bullet("Use Reinstate Order for error-type issues."),
    ]
    _md, ws, report = to_elementor(blocks)

    kinds = {}
    for w in ws:
        kinds[w["widgetType"]] = kinds.get(w["widgetType"], 0) + 1

    assert kinds["heading"] == 4
    # 兩個表格皆成 HTML table
    tables = [w for w in ws if w["widgetType"] == "text-editor"
              and "<table>" in w["settings"].get("editor", "")]
    assert len(tables) == 2
    # 兩段數字清單，各自單一 widget、編號連續
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    assert [len(o["settings"]["ul_icon_list"]) for o in olists] == [3, 4]
    # 步驟內嵌圖 → [caption]；獨立圖 → image widget（僅 errors-list 那張）
    assert "[caption" in olists[1]["settings"]["ul_icon_list"][2]["text"]
    assert len([w for w in ws if w["widgetType"] == "image"]) == 1
    # callout 正確分類
    alerts = [w for w in ws if w["widgetType"] == "docly_alerts_box"]
    assert alerts[0]["settings"]["alert_type"] == "info"
    assert alerts[0]["settings"]["alert_title"] == "Note"
    # 項目符號清單成獨立 <ul>
    assert any(w["widgetType"] == "text-editor"
               and w["settings"].get("editor", "").startswith("<ul>") for w in ws)
    assert not report["unsupported"]


def test_faq_questions_extracted_for_h3_and_h4():
    """FAQ 問題用 h3 或 h4 都要抽得到。

    Style Guide 寫 h3，但實際文章多用 h4。若只認 h3，h4 的問答會既不進
    faq_items 也不進頁面內容——整段靜默消失（2026-08-02 首次端到端測試發現）。
    """
    for level in (3, 4):
        blocks = [
            head(2, "Overview"), para("Intro."),
            head(2, "FAQ"),
            head(level, "Why is an order under Exception Orders?"),
            para("Because Synctify detected an issue."),
            head(level, "What is the difference?"),
            para("Errors need correction."),
        ]
        md, report = nb.blocks_to_markdown(blocks)
        tpl, faqs, _rep = n2e.convert(md, "T", "manage-exception-orders",
                                      sync_date="August 2, 2026")
        assert len(faqs) == 2, f"h{level} 的 FAQ 未被抽取"
        assert faqs[0]["question"] == "Why is an order under Exception Orders?"
        assert "Synctify detected an issue" in faqs[0]["answer_html"]
        assert faqs[1]["question"] == "What is the difference?"

        # 頁面上只留 h2 標題＋shortcode，問答本身不重複出現在頁面
        ws = widgets(tpl["content"])
        shortcodes = [w["settings"]["shortcode"] for w in ws
                      if w["widgetType"] == "shortcode"]
        assert any('[faq group="manage-exception-orders"' in s for s in shortcodes)


def test_unsupported_block_recorded_not_crash():
    blocks = [head(1, "S"), para("ok"), blk("equation", {"expression": "x^2"})]
    md, _ws, report = to_elementor(blocks)
    assert "ok" in md
    assert "equation" in report["unsupported"]


def test_code_block_with_spaced_language_does_not_swallow_document():
    """語言標記帶空格（Notion 的 "plain text"）不可讓後續內容被吞進程式碼區塊。

    2026-08-02 實站發現：fence 正則 ^```(\\w*)\\s*$ 認不出 "```plain text"，
    導致結尾的 ``` 被當成開頭，整篇後半段（標題、段落全部）被吞成一個程式碼區塊。
    """
    blocks = [
        head(2, "Find orders on hold"),
        para("Orders held by this feature will show a reason such as:"),
        blk("code", {"rich_text": [rt("Order held by [Channel Name].")],
                     "language": "plain text"}),
        head(2, "Release an order before the frozen period ends"),
        para("If you review an order and decide it can proceed."),
        head(4, "Step 1: Go to the On-Hold tab"),
        para("From the navigation bar."),
    ]
    md, ws, report = to_elementor(blocks)

    # 語言正規化為站上慣例
    code = [w for w in ws if w["widgetType"] == "docly_code_syntax_highlighter"]
    assert len(code) == 1
    assert code[0]["settings"]["lng_type"] == "plaintext"
    assert "Order held by" in code[0]["settings"]["source_code"]

    # 關鍵：程式碼區塊之後的內容必須完整保留，不可被吞
    heads = [w["settings"]["title"] for w in ws if w["widgetType"] == "heading"]
    assert "Release an order before the frozen period ends" in heads
    assert "Step 1: Go to the On-Hold tab" in heads
    assert "##" not in code[0]["settings"]["source_code"]      # 標題沒被吞進去


def test_various_code_languages_parse():
    for lang, expect in [("json", "json"), ("http", "http"), ("", "markdown"),
                         ("plain text", "plaintext"), ("shell script", "shellscript")]:
        blocks = [head(2, "S"),
                  blk("code", {"rich_text": [rt("body")], "language": lang}),
                  head(2, "After")]
        _md, ws, _r = to_elementor(blocks)
        code = [w for w in ws if w["widgetType"] == "docly_code_syntax_highlighter"]
        assert code and code[0]["settings"]["lng_type"] == expect, f"{lang} → {expect}"
        heads = [w["settings"]["title"] for w in ws if w["widgetType"] == "heading"]
        assert "After" in heads, f"{lang}: 後續內容被吞掉了"


def test_caption_alt_marker_splits_into_two_fields():
    """Notion API 不給 alt text，寫作端以 `可見圖說 [alt: 描述]` 標記把兩段放進圖說。"""
    cap = "Review on-hold orders and their hold reasons from the On-Hold tab."
    alt = "Synctify OMS On-Hold tab showing on-hold orders, hold reasons, and actions."
    blocks = [
        head(2, "S"),
        blk("image", {"external": {"url": "https://x/a.png"},
                      "caption": [rt(f"{cap} [alt: {alt}]")]}),
    ]
    md, ws, _ = to_elementor(blocks)
    # markdown 用 title 欄位帶 alt：![可見圖說](url "alt")
    assert f'![{cap}](https://x/a.png "{alt}")' in md

    img = [w for w in ws if w["widgetType"] == "image"][0]
    assert img["settings"]["image"]["alt"] == alt          # widget 用 alt
    _tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="Aug 2, 2026")
    assert rep["images"][0]["alt"] == alt
    assert rep["images"][0]["caption"] == cap              # 上傳時分開送


def test_caption_without_marker_is_backward_compatible():
    """沒有標記的舊文章：alt 與 caption 同值，行為與先前一致。"""
    text = "A plain caption."
    blocks = [head(2, "S"),
              blk("image", {"external": {"url": "https://x/b.png"}, "caption": [rt(text)]})]
    md, ws, _ = to_elementor(blocks)
    assert f'![{text}](https://x/b.png)' in md      # 無多餘的 title 欄位
    _tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="Aug 2, 2026")
    assert rep["images"][0]["alt"] == text
    assert rep["images"][0]["caption"] == text


def test_nested_step_image_uses_alt_and_caption_separately():
    cap, alt = "The action menu.", "Screenshot of the row action menu with Reinstate Order."
    blocks = [
        head(2, "Steps"),
        num("Open the menu"),
        num("Select it", children=[
            blk("image", {"external": {"url": "https://x/c.png"},
                          "caption": [rt(f"{cap} [alt: {alt}]")]})]),
    ]
    _md, ws, _ = to_elementor(blocks)
    step = [w for w in ws if w["widgetType"] == "docly_list_item"][0]["settings"]["ul_icon_list"][1]["text"]
    assert f'alt="{alt}"' in step          # img 的 alt 用 alt text
    assert f"</a> {cap}[/caption]" in step  # 可見圖說用 caption
