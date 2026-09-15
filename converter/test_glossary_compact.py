"""glossary_compact 的測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_glossary_compact.py -v
"""
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glossary_compact as gc  # noqa: E402


def page(en, zh="", tw="", kind="UI 標籤", ok=True, key="", wrapper="properties"):
    def rt(t):
        return {"type": "rich_text", "rich_text": [{"plain_text": t}] if t else []}
    props = {
        "English": {"type": "title", "title": [{"plain_text": en}]},
        "简体中文": rt(zh), "繁體中文": rt(tw), "i18n key": rt(key),
        "類型": {"type": "select", "select": {"name": kind} if kind else None},
        "已確認": {"type": "checkbox", "checkbox": ok},
    }
    return {"id": en, wrapper: props}


def test_module_has_no_imports():
    """要能原樣打包進 n8n Python Code node（只允許 re，這裡連 re 都不用）。"""
    tree = ast.parse((HERE / "glossary_compact.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_compact_rows_reads_raw_pages_and_fetch_glossary_rows():
    raw = gc.compact_rows([page("Products", "产品", "商品", ok=True)])
    slim = gc.compact_rows([page("Products", "产品", "商品", ok=True, wrapper="props")])
    expected = [{"en": "Products", "zh": "产品", "tw": "商品", "kind": "UI 標籤", "key": "", "ok": True}]
    assert raw == expected
    assert slim == expected


def test_lines_only_confirmed_with_chinese_sorted():
    rows = gc.compact_rows([
        page("products", "产品", "商品"),
        page("Add Product", "添加产品", "新增商品"),
        page("Draft Term", "草稿", ok=False),
        page("No Chinese Yet", "", ok=True),
    ])
    assert gc.compact_lines(rows) == [
        gc.COMPACT_HEADER,
        "Add Product | 添加产品 | 新增商品 | UI 標籤",
        "products | 产品 | 商品 | UI 標籤",
    ]


def test_lines_mark_missing_traditional_and_type():
    rows = gc.compact_rows([page("Link", "关联", "", kind="")])
    assert gc.compact_lines(rows)[1] == "Link | 关联 | （繁中未定） | —"


def test_lines_disambiguate_duplicate_english_with_first_key():
    """Cartons 拆成「數量」「單位」兩列，只看英文分不出來。"""
    rows = gc.compact_rows([
        page("Cartons", "纸箱", "紙箱", key="integration.providers.wayfair.labels.shipment_unit_carton、integration.b"),
        page("Cartons", "纸箱数", "紙箱數", key="shipment_routing.detail.labels.carton_quantity 等 2 個"),
    ])
    assert gc.compact_lines(rows)[1:] == [
        "Cartons | 纸箱 | 紙箱 | UI 標籤 | 用於 integration.providers.wayfair.labels.shipment_unit_carton",
        "Cartons | 纸箱数 | 紙箱數 | UI 標籤 | 用於 shipment_routing.detail.labels.carton_quantity",
    ]


def test_lines_collapse_identical_duplicates():
    """Merchant SKU 在完整表有兩列、譯法一模一樣：精簡表只列一行，也不用附 key。"""
    rows = gc.compact_rows([
        page("Merchant SKU", "Merchant SKU", "Merchant SKU", key="automation.routing_v2.labels.merchant_sku"),
        page("Merchant SKU", "Merchant SKU", "Merchant SKU", key="automation.routing_v2.labels.merchant_sku"),
    ])
    assert gc.compact_lines(rows)[1:] == ["Merchant SKU | Merchant SKU | Merchant SKU | UI 標籤"]


def test_cells_escape_pipe_and_newline():
    rows = gc.compact_rows([page("A | B", "甲\n乙", "甲乙")])
    assert gc.compact_lines(rows)[1] == "A ／ B | 甲 乙 | 甲乙 | UI 標籤"


def test_summary_counts():
    rows = gc.compact_rows([page("Products", "产品", "商品"), page("Draft", "草稿", ok=False),
                            page("Empty", "", ok=True)])
    assert gc.compact_summary(rows, "2026-09-15 18:00") == (
        "最後更新：2026-09-15 18:00｜已確認 1 詞（完整表共 3 列）"
        "｜另有 1 個已確認但沒有簡中的詞未收｜由完整術語表自動產生，請勿手動編輯")


def test_chunks_rejoin_to_original_and_respect_limit():
    text = "\n".join(f"Term {i} | 词{i} | 詞{i} | UI 標籤" for i in range(400))
    chunks = gc.compact_chunks(text, size=500)
    assert "".join(chunks) == text
    assert all(len(c) <= 500 for c in chunks)
    # 除了第一段，每段都從換行開始＝沒有一行被切開
    assert all(c.startswith("\n") for c in chunks[1:])


def test_chunks_hard_split_overlong_line():
    text = "short\n" + "字" * 1200
    chunks = gc.compact_chunks(text, size=500)
    assert "".join(chunks) == text
    assert all(len(c) <= 500 for c in chunks)


def test_chunks_short_text_single_chunk():
    assert gc.compact_chunks("a\nb") == ["a\nb"]
    assert gc.compact_chunks("") == []
