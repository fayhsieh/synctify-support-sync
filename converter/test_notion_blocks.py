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


def test_last_updated_uses_notion_date_not_sync_date():
    """Last updated 是寫作者標記的「內容實質更新日」，不可被同步當天覆蓋。"""
    blocks = [
        blk("paragraph", {"rich_text": [rt("Last updated: June 23, 2026", italic=True)]}),
        head(2, "Overview"), para("Body."),
    ]
    _md, ws, _ = to_elementor(blocks)
    first = ws[0]["settings"]["editor"]
    assert "June 23, 2026" in first
    assert "July 24, 2026" not in first          # to_elementor 傳的 sync_date


def test_last_updated_falls_back_to_sync_date_when_absent():
    _md, ws, _ = to_elementor([head(2, "Overview"), para("Body.")])
    assert "July 24, 2026" in ws[0]["settings"]["editor"]


# ---------- SEO Meta 擷取 ----------
# fixture 依 5-6 New Order Frozen Period 的真實頁面結構建構（2026-08-02 由 Notion 讀出）：
# `**SEO Meta**` 段落後接兩個 quote，quote 內是「粗體標籤＋軟換行＋內容」，
# 兩者同屬一個 rich_text 陣列，故純文字為 "Title\n實際標題"。

def _seo_quote(label, value):
    return blk("quote", {"rich_text": [rt(label + "\n", bold=True), rt(value)]})


SEO_TITLE = "New Order Frozen Period - Synctify Support Center"
SEO_DESC = ("Learn how to use New Order Frozen Period to automatically hold new "
            "orders before fulfillment, review changes or cancellations.")


def test_seo_meta_is_extracted_not_just_dropped():
    blocks = [
        head(2, "Overview"), para("Body."),
        blk("divider", {}),
        para("SEO Meta"),
        _seo_quote("Title", SEO_TITLE),
        _seo_quote("Meta description", SEO_DESC),
    ]
    md, report = nb.blocks_to_markdown(blocks)
    assert report["seo"] == {"title": SEO_TITLE, "description": SEO_DESC}
    # 仍然不可流入正文
    assert SEO_TITLE not in md
    assert SEO_DESC not in md
    assert "SEO Meta" not in md


def test_seo_labels_tolerate_case_and_colon():
    blocks = [
        head(2, "Overview"), para("Body."), para("**SEO Meta**"),
        _seo_quote("SEO Title:", "T"), _seo_quote("meta description：", "D"),
    ]
    _md, report = nb.blocks_to_markdown(blocks)
    assert report["seo"] == {"title": "T", "description": "D"}


def test_seo_capture_stops_at_next_heading():
    """SEO Meta 後若還有正常章節，該章節要照常輸出，且不被誤當 SEO 欄位。"""
    blocks = [
        head(2, "Overview"), para("Body."), para("SEO Meta"),
        _seo_quote("Title", SEO_TITLE),
        head(2, "Appendix"), para("Tail content."),
    ]
    md, report = nb.blocks_to_markdown(blocks)
    assert report["seo"] == {"title": SEO_TITLE}
    assert "## Appendix" in md and "Tail content." in md


def test_review_notes_section_still_fully_dropped():
    """只有 SEO Meta 段改成擷取；內部審核筆記仍必須完全消失（CLAUDE.md）。"""
    blocks = [
        head(2, "Overview"), para("Body."),
        para("Content Review Notes"),
        _seo_quote("Title", "SHOULD NOT LEAK"),
    ]
    md, report = nb.blocks_to_markdown(blocks)
    assert report["seo"] == {}
    assert "SHOULD NOT LEAK" not in md


def test_seo_accepts_literal_br_separator():
    """寫作者手打 <br> 而非 Shift+Enter 時也要解析得出來。"""
    blocks = [
        head(2, "Overview"), para("Body."), para("SEO Meta"),
        blk("quote", {"rich_text": [rt("Title<br>", bold=True), rt(SEO_TITLE)]}),
    ]
    _md, report = nb.blocks_to_markdown(blocks)
    assert report["seo"] == {"title": SEO_TITLE}


# ---------- 版本標記 ----------
# fixture 依 5-5 Shipment Routing 母列的真實結構（2026-08-11 讀出）：
# Overview 有 `- Current Version: v3 (May 2026)`，Version History 的標題是
# `### **vN – Month Year**`，現行版本結尾多一個 ` (Current)`。破折號是 en dash。

def _vh(text, bold=True):
    return blk("heading_3", {"rich_text": [rt(text, **({"bold": True} if bold else {}))]})


def _ov(text):
    return blk("bulleted_list_item", {"rich_text": [rt(text)]})


def _mother_blocks(current="v3"):
    return [
        blk("heading_2", {"rich_text": [rt("Overview")]}),
        _ov(f"Current Version: {current} (May 2026)"),
        _ov("Status: Active"),
        blk("heading_2", {"rich_text": [rt("Version History")]}),
        _vh("v1 – May 2026"),
        _vh("v2 – May 2026"),
        _vh("v3 – May 2026 (Current)"),
    ]


ROWS = [
    {"id": "r1", "title": "5-5 Shipment Routing - v1", "version": "v1 (Initial Version)"},
    {"id": "r2", "title": "5-5 Shipment Routing - v2", "version": "v2"},
    {"id": "r3", "title": "5-5 Shipment Routing - v3 (Current)", "version": "v3"},
]


def test_version_marks_move_current_to_v2():
    plan = nb.plan_version_marks(ROWS, _mother_blocks("v3"), "v2")
    assert {r["id"]: r["title"] for r in plan["row_renames"]} == {
        "r2": "5-5 Shipment Routing - v2 (Current)",
        "r3": "5-5 Shipment Routing - v3",
    }
    texts = {u["id"]: u["rich_text"][0]["text"]["content"] for u in plan["block_updates"]}
    # Overview 沿用 Version History 裡該版本的日期，不自行編造
    assert any(t == "Current Version: v2 (May 2026)" for t in texts.values())
    assert "v2 – May 2026 (Current)" in texts.values()
    assert "v3 – May 2026" in texts.values()


def test_version_marks_preserve_bold_on_headings():
    plan = nb.plan_version_marks(ROWS, _mother_blocks("v3"), "v1")
    heads = [u for u in plan["block_updates"] if u["type"] == "heading_3"]
    assert heads, "應該有標題被改動"
    for u in heads:
        assert u["rich_text"][0].get("annotations", {}).get("bold") is True


def test_version_marks_emit_nothing_when_already_correct():
    """已經是正確狀態時不可送出任何 API 改動，避免刷 Notion 的編輯紀錄。"""
    plan = nb.plan_version_marks(ROWS, _mother_blocks("v3"), "v3")
    assert plan["row_renames"] == []
    assert plan["block_updates"] == []


def test_version_marks_accept_long_version_label():
    """Version 屬性的 v1 標籤是 `v1 (Initial Version)`，要能對得上。"""
    plan = nb.plan_version_marks(ROWS, _mother_blocks("v3"), "v1 (Initial Version)")
    assert {r["id"]: r["title"] for r in plan["row_renames"]} == {
        "r1": "5-5 Shipment Routing - v1 (Current)",
        "r3": "5-5 Shipment Routing - v3",
    }


def test_version_marks_ignore_unrelated_blocks():
    blocks = _mother_blocks("v3") + [
        blk("paragraph", {"rich_text": [rt("This document tracks the version history.")]}),
        _vh("Not a version heading"),
    ]
    plan = nb.plan_version_marks(ROWS, blocks, "v3")
    assert plan["block_updates"] == []


def test_vertical_ellipsis_icon_becomes_dots_vertical():
    """⋮ (More Actions) 是 U+22EE，不是 emoji，但一樣要轉成 custom_icon。"""
    blocks = [head(2, "Overview"),
              blk("paragraph", {"rich_text": [
                  rt("In the "), rt("Action", code=True), rt(" column, click "),
                  rt("⋮ (More Actions)", code=True), rt(" to open the menu.")]})]
    _md, ws, _ = to_elementor(blocks)
    body = "".join(w["settings"].get("editor", "") for w in ws)
    assert '[custom_icon class="dots-vertical"] (More Actions)' in body
    # 一般 inline code 仍走 [direction]，兩種語意不可混淆
    assert "[direction]Action[/direction]" in body


# ---------- 圖片佔位鷹架 ----------
# 結構取自 5-1 Manage Sales Orders v2「Find and review sales orders」（2026-08-11 讀出）：
# 步驟底下巢狀一個 callout，首行 **Image Placeholder**，內含檔名與 caption/alt。

def _placeholder_callout(label_on_self=True):
    head_rt = [rt("Image Placeholder", bold=True)] if label_on_self else []
    kids = [] if label_on_self else [blk("paragraph", {"rich_text": [rt("Image Placeholder", bold=True)]})]
    kids += [
        blk("paragraph", {"rich_text": [rt("Filename", bold=True)]}),
        blk("paragraph", {"rich_text": [rt("manage-sales-orders-views.png", code=True)]}),
        blk("paragraph", {"rich_text": [rt("caption: Choose the view. [alt: Sales Orders tabs.]")]}),
    ]
    return blk("callout", {"icon": {"emoji": "📷"}, "color": "gray_background",
                           "rich_text": head_rt}, children=kids)


def _sales_orders_blocks(label_on_self=True):
    return [
        head(2, "Find and review sales orders"),
        num("Go to Sales Orders."),
        num("Open the appropriate order view.", children=[
            blk("bulleted_list_item", {"rich_text": [rt("All - every sales order.")]}),
            _placeholder_callout(label_on_self),
        ]),
        num("Click Filter to open the filter panel."),
        num("Act on the orders you need."),
    ]


def test_image_placeholder_never_reaches_output():
    md, report = nb.blocks_to_markdown(_sales_orders_blocks())
    assert report["excluded_placeholders"] == 1
    for leak in ("Image Placeholder", "manage-sales-orders-views.png", "[alt:"):
        assert leak not in md, f"鷹架洩漏：{leak}"


def test_image_placeholder_label_on_first_child_also_excluded():
    _md, report = nb.blocks_to_markdown(_sales_orders_blocks(label_on_self=False))
    assert report["excluded_placeholders"] == 1


def test_numbering_stays_continuous_after_placeholder_removed():
    """佔位 callout 是編號斷掉的元兇；剔除後 4 個步驟要回到同一個 widget。"""
    _md, ws, _ = to_elementor(_sales_orders_blocks())
    lists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    assert len(lists) == 1, f"編號被切成 {len(lists)} 段"
    assert len(lists[0]["settings"]["ul_icon_list"]) == 4


def test_normal_callout_still_renders():
    """只剔除佔位鷹架，一般 callout 不受影響。"""
    blocks = [head(2, "Overview"),
              blk("callout", {"icon": {"emoji": "⚠️"}, "color": "yellow_background",
                              "rich_text": [rt("Important", bold=True)]},
                  children=[blk("paragraph", {"rich_text": [rt("Keep your token safe.")]})])]
    _md, ws, report = to_elementor(blocks)
    assert report["excluded_placeholders"] == 0
    assert any(w["widgetType"] == "docly_alerts_box" for w in ws)


# ---------- Notion 內部連結 → WP 永久連結 ----------

HUB_ROWS = [
    {"id": "3272f2ed-e27d-808d-b5d4-d7f4a6796142",      # 母列：7-1 Reports Center
     "properties": {"WP Post ID": {"rich_text": [{"plain_text": "6118"}]},
                    "Doc name": {"title": [{"plain_text": "7-1 Reports Center"}]},
                    "Parent item": {"relation": []}}},
    {"id": "3282f2ed-e27d-804a-966b-ed0721b0cc08",      # 其版本子列（自己沒有 Post ID）
     "properties": {"WP Post ID": {"rich_text": []},
                    "Doc name": {"title": [{"plain_text": "7-1 Reports Center - v1 (Current)"}]},
                    "Parent item": {"relation": [
                        {"id": "3272f2ed-e27d-808d-b5d4-d7f4a6796142"}]}}},
    {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",      # 尚未同步：查不到網址
     "properties": {"WP Post ID": {"rich_text": []},
                    "Doc name": {"title": [{"plain_text": "9-9 Draft Doc"}]},
                    "Parent item": {"relation": []}}},
]
WP_DOCS = [{"id": 6118,
            "link": "https://support.synctify.io/docs/synctify-documentation/reports/reports-center/",
            "title": {"rendered": "Reports Center"}}]


def test_link_map_covers_mother_and_version_rows():
    m = nb.build_link_map(HUB_ROWS, WP_DOCS)
    url = "https://support.synctify.io/docs/synctify-documentation/reports/reports-center/"
    assert m["3272f2ede27d808db5d4d7f4a6796142"]["url"] == url
    assert m["3272f2ede27d808db5d4d7f4a6796142"]["title"] == "Reports Center"
    assert m["3272f2ede27d808db5d4d7f4a6796142"]["doc_name"] == "7-1 Reports Center"
    # 連到版本子列也要解析得出來——WP Post ID 只記在母列
    assert m["3282f2ede27d804a966bed0721b0cc08"]["url"] == url
    assert "aaaaaaaabbbbccccddddeeeeeeeeeeee" not in m


def test_notion_link_in_article_becomes_wp_permalink():
    m = nb.build_link_map(HUB_ROWS, WP_DOCS)
    blocks = [head(2, "Overview"),
              blk("paragraph", {"rich_text": [
                  {"plain_text": "For details see ",
                   "annotations": {}},
                  {"plain_text": "Reports Center", "annotations": {},
                   "href": "https://www.notion.so/Reports-Center-3272f2ede27d808db5d4d7f4a6796142"}]})]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map=m)
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert "support.synctify.io/docs/synctify-documentation/reports/reports-center/" in body
    assert "notion.so" not in body
    assert rep["unresolved_notion_links"] == []


def test_unresolvable_notion_link_is_reported_not_dropped():
    """換不掉時保留原連結並回報——靜默刪掉會讓寫作者不知道哪裡要修。"""
    blocks = [head(2, "Overview"),
              blk("paragraph", {"rich_text": [
                  {"plain_text": "See ", "annotations": {}},
                  {"plain_text": "Draft Doc", "annotations": {},
                   "href": "https://www.notion.so/Draft-aaaaaaaabbbbccccddddeeeeeeeeeeee"}]})]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="August 11, 2026",
                               link_map=nb.build_link_map(HUB_ROWS, WP_DOCS))
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert "aaaaaaaabbbbccccddddeeeeeeeeeeee" in body          # 連結還在
    assert len(rep["unresolved_notion_links"]) == 1


def test_external_links_untouched():
    blocks = [head(2, "Overview"),
              blk("paragraph", {"rich_text": [
                  {"plain_text": "Synctify", "annotations": {},
                   "href": "https://synctify.net/"}]})]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map={})
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert 'href="https://synctify.net/"' in body
    assert rep["unresolved_notion_links"] == []


def _mention_para(text, page_url):
    """Notion 的頁面提及：顯示文字就是被提及頁面的 Doc name。"""
    return blk("paragraph", {"rich_text": [
        {"plain_text": "For more information, see ", "annotations": {}},
        {"plain_text": text, "annotations": {}, "href": page_url},
        {"plain_text": ".", "annotations": {}}]})


def test_page_mention_uses_wp_title_not_notion_doc_name():
    """提及的文字是 Doc name（帶編號前綴），站上沒有編號，要換成 WP 標題。"""
    m = nb.build_link_map(HUB_ROWS, WP_DOCS)
    blocks = [head(2, "Export"),
              _mention_para("7-1 Reports Center",
                            "https://app.notion.com/p/3272f2ede27d808db5d4d7f4a6796142")]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map=m)
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert ">Reports Center</a>" in body
    assert "7-1" not in body
    assert "notion." not in body


def test_author_written_link_text_is_preserved():
    """作者自訂的連結文字不可被標題蓋掉——只有等於 Doc name 時才換。"""
    m = nb.build_link_map(HUB_ROWS, WP_DOCS)
    blocks = [head(2, "Export"),
              _mention_para("the reports guide",
                            "https://app.notion.com/p/3272f2ede27d808db5d4d7f4a6796142")]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, _r = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map=m)
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert ">the reports guide</a>" in body
    assert "reports-center/" in body


def test_page_mention_without_href_still_becomes_link():
    """mention 的 href 不保證存在；沒有它就產不出連結，也就無從換成 WP 網址。"""
    m = nb.build_link_map(HUB_ROWS, WP_DOCS)
    blocks = [head(2, "Export"),
              blk("paragraph", {"rich_text": [
                  {"plain_text": "See ", "annotations": {}},
                  {"plain_text": "7-1 Reports Center", "annotations": {},
                   "type": "mention",
                   "mention": {"type": "page",
                               "page": {"id": "3272f2ed-e27d-808d-b5d4-d7f4a6796142"}}},
                  {"plain_text": ".", "annotations": {}}]})]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, _r = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map=m)
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert "reports-center/" in body
    assert ">Reports Center</a>" in body


def test_user_mention_not_turned_into_link():
    """只有頁面提及要變連結；使用者提及不該產生網址。"""
    blocks = [head(2, "Overview"),
              blk("paragraph", {"rich_text": [
                  {"plain_text": "Owner: ", "annotations": {}},
                  {"plain_text": "Fay", "annotations": {}, "type": "mention",
                   "mention": {"type": "user", "user": {"id": "abc"}}}]})]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, _r = n2e.convert(md, "T", "t", sync_date="August 11, 2026", link_map={})
    body = "".join(w["settings"].get("editor", "")
                   for c in tpl["content"] for w in c["elements"])
    assert "<a href" not in body
    assert "Fay" in body
