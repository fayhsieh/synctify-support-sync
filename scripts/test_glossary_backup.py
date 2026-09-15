"""glossary_backup 的純函式測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest scripts/test_glossary_backup.py -v
"""
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import glossary_backup as gb  # noqa: E402


def rt(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text}] if text else []}


def live_row(pid, english, **fields):
    props = {"English": {"type": "title", "title": [{"plain_text": english}]}}
    for k, v in fields.items():
        props[k] = v
    return {"id": pid, "english": english, "props": props}


# ---- plain_value / to_property ----------------------------------------------

def test_plain_value_each_type():
    assert gb.plain_value({"type": "title", "title": [{"plain_text": "Add "}, {"plain_text": "Product"}]}) == "Add Product"
    assert gb.plain_value(rt("")) == ""
    assert gb.plain_value({"type": "number", "number": 3}) == 3
    assert gb.plain_value({"type": "checkbox", "checkbox": True}) is True
    assert gb.plain_value({"type": "select", "select": None}) is None
    assert gb.plain_value({"type": "select", "select": {"name": "一致"}}) == "一致"
    assert gb.plain_value({"type": "multi_select", "multi_select": [{"name": "order"}, {"name": "global"}]}) \
        == ["global", "order"]
    assert gb.plain_value({"type": "created_time", "created_time": "x"}) is None


def test_to_property_formats():
    assert gb.to_property("number", None) == {"number": None}
    assert gb.to_property("checkbox", 1) == {"checkbox": True}
    assert gb.to_property("select", "") == {"select": None}
    assert gb.to_property("select", "一致") == {"select": {"name": "一致"}}
    assert gb.to_property("multi_select", ["order"]) == {"multi_select": [{"name": "order"}]}
    assert gb.to_property("rich_text", "") == {"rich_text": []}


def test_to_property_splits_long_text():
    """Notion API 單段文字上限 2000 字，備註常常超過。"""
    segs = gb.to_property("rich_text", "字" * 4500)["rich_text"]
    assert [len(s["text"]["content"]) for s in segs] == [2000, 2000, 500]


# ---- flatten / snapshot -----------------------------------------------------

def test_flatten_sorts_by_english_and_keeps_supported_types():
    rows = [live_row("b", "products", 已確認={"type": "checkbox", "checkbox": True},
                     建立時間={"type": "created_time", "created_time": "x"}),
            live_row("a", "Add Product", 简体中文=rt("添加产品"))]
    flat = gb.flatten(rows)
    assert [r["English"] for r in flat] == ["Add Product", "products"]
    assert flat[0] == {"id": "a", "English": "Add Product", "简体中文": "添加产品"}
    assert "建立時間" not in flat[1]


def test_snapshot_writes_timestamped_file(tmp_path):
    now = datetime.datetime(2026, 9, 15, 17, 5, 9)
    path = gb.snapshot([live_row("a", "Link", 简体中文=rt("关联"))], reason="測試", directory=tmp_path, now=now)
    assert path.name == "glossary-20260915-170509.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["reason"] == "測試"
    assert data["taken_at"] == "2026-09-15T17:05:09"
    assert data["rows"] == [{"id": "a", "English": "Link", "简体中文": "关联"}]


# ---- restore_plan -----------------------------------------------------------

SNAP = [
    {"id": "c1", "English": "Cartons", "简体中文": "纸箱数", "備註": "數量"},
    {"id": "c2", "English": "Cartons", "简体中文": "纸箱", "備註": "單位"},
    {"id": "p", "English": "Products", "简体中文": "产品", "備註": "繁簡刻意不同"},
    {"id": "gone", "English": "Period", "简体中文": "期间"},
]
LIVE = [
    live_row("c1", "Cartons", 简体中文=rt("箱数"), 備註=rt("數量")),
    live_row("c2", "Cartons", 简体中文=rt("箱数"), 備註=rt("被改掉")),
    live_row("p", "Products", 简体中文=rt("产品"), 備註=rt("繁簡刻意不同")),
]


def test_restore_plan_matches_names_case_insensitively_including_duplicates():
    patches, deleted = gb.restore_plan(SNAP, LIVE, names=["cartons"])
    assert [row["id"] for row, _ in patches] == ["c1", "c2"]
    assert patches[0][1] == {"简体中文": ("箱数", "纸箱数", "rich_text")}
    assert patches[1][1] == {"简体中文": ("箱数", "纸箱", "rich_text"), "備註": ("被改掉", "單位", "rich_text")}
    assert deleted == []


def test_restore_plan_field_filter():
    patches, _ = gb.restore_plan(SNAP, LIVE, names=["Cartons"], fields=["備註"])
    assert [(row["id"], diff) for row, diff in patches] == [("c2", {"備註": ("被改掉", "單位", "rich_text")})]


def test_restore_plan_skips_unchanged_and_reports_deleted():
    patches, deleted = gb.restore_plan(SNAP, LIVE, names=["Products", "Period"])
    assert patches == []
    assert [s["English"] for s in deleted] == ["Period"]


def test_restore_plan_ignores_fields_removed_from_notion():
    snap = [{"id": "p", "English": "Products", "舊欄位": "x"}]
    patches, _ = gb.restore_plan(snap, LIVE, names=None)
    assert patches == []
