"""
數字清單巢狀內容測試（mapping-rules §一 #7）。

鎖住：一段連續編號清單（即使中間夾巢狀 bullet 或 tab 縮排的接續說明）
維持在「同一個」docly_list_item widget，編號連續不重來；巢狀 bullet 收成該步驟
內嵌 <ul>，接續說明收成該步驟追加的 <p>。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_olist_nesting.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402


def _widgets(els):
    out = []
    for e in els:
        if e.get("elType") == "widget":
            out.append(e)
        out += _widgets(e.get("elements", []))
    return out


def _olists(md):
    tpl, _f, _r = n2e.convert(md, "T", "t", sync_date="July 22, 2026")
    return [w for w in _widgets(tpl["content"]) if w["widgetType"] == "docly_list_item"]


def test_nested_numbered_list_stays_one_widget():
    md = "\n".join([
        "## Steps",
        "1. First step",
        "2. Second step with options:",
        "\t- option A",
        "\t- option B",
        "3. Third step",
        "\tA continuation note for step 3.",
        "4. Fourth step",
    ])
    olists = _olists(md)
    # 整段維持在單一 widget（不因巢狀而破碎）
    assert len(olists) == 1
    items = olists[0]["settings"]["ul_icon_list"]
    assert len(items) == 4  # 編號 1-4 連續

    # 第 2 步含巢狀 bullet（內嵌 <ul>）
    assert "<ul>" in items[1]["text"]
    assert "<li>option A</li>" in items[1]["text"]
    assert "<li>option B</li>" in items[1]["text"]

    # 第 3 步含接續說明（追加 <p>），且不是 bullet
    assert items[2]["text"].count("<p>") == 2
    assert "<ul>" not in items[2]["text"]
    assert "continuation note" in items[2]["text"]


def test_plain_numbered_list_unchanged():
    md = "## S\n1. one\n2. two\n3. three"
    olists = _olists(md)
    assert len(olists) == 1
    items = olists[0]["settings"]["ul_icon_list"]
    assert [i["text"] for i in items] == ["<p>one</p>", "<p>two</p>", "<p>three</p>"]


def test_two_separate_numbered_lists_split_by_heading():
    # 被標題隔開的兩段編號清單 → 兩個 widget（各自從 1 起算）
    md = "## A\n1. a1\n2. a2\n## B\n1. b1\n2. b2"
    olists = _olists(md)
    assert len(olists) == 2
    assert len(olists[0]["settings"]["ul_icon_list"]) == 2
    assert len(olists[1]["settings"]["ul_icon_list"]) == 2
