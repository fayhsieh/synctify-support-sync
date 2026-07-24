"""
數字清單巢狀內容測試（mapping-rules §一 #7）。

結構對齊實站範本 7899（Upload Sales Order）：整段連續編號＝同一個 docly_list_item
widget；編號項下的巢狀子內容渲染成內嵌 <p style="padding-left: 40px;">，而**非**
<ul><li>（否則主題 CSS counter 會把 <li> 算進圓圈編號，子項變成連續數字）。

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


def test_nested_stays_one_widget_and_uses_indented_p():
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
    # 整段維持在單一 widget（編號 1-4 連續，靠同一 widget）
    assert len(olists) == 1
    items = olists[0]["settings"]["ul_icon_list"]
    assert len(items) == 4

    # 巢狀子內容用內嵌縮排 <p>，絕不可出現 <ul>/<li>（否則會被編號）
    joined = "".join(it["text"] for it in items)
    assert "<ul>" not in joined and "<li>" not in joined

    # 第 2 步含兩個縮排段落（option A/B）
    assert items[1]["text"].count('<p style="padding-left: 40px;">') == 2
    assert "option A" in items[1]["text"] and "option B" in items[1]["text"]

    # 第 3 步含一個縮排段落（接續說明）
    assert items[2]["text"].count('<p style="padding-left: 40px;">') == 1
    assert "continuation note" in items[2]["text"]


def test_plain_numbered_list_unchanged():
    md = "## S\n1. one\n2. two\n3. three"
    items = _olists(md)[0]["settings"]["ul_icon_list"]
    assert [i["text"] for i in items] == ["<p>one</p>", "<p>two</p>", "<p>three</p>"]


def test_separate_lists_split_by_heading():
    md = "## A\n1. a1\n2. a2\n## B\n1. b1\n2. b2"
    olists = _olists(md)
    assert len(olists) == 2
    assert len(olists[0]["settings"]["ul_icon_list"]) == 2
    assert len(olists[1]["settings"]["ul_icon_list"]) == 2
