"""glossary_sync 的純函式測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest scripts/test_glossary_sync.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import glossary_sync as gs  # noqa: E402


def props(**kw):
    """用 Notion API 的欄位格式組出一列的 properties。"""
    out = {}
    for k, v in kw.items():
        if k in ("文件出現次數", "OMS 使用處數"):
            out[k] = {"type": "number", "number": v}
        elif k == "一致性":
            out[k] = {"type": "select", "select": {"name": v} if v else None}
        else:
            out[k] = {"type": "rich_text", "rich_text": [{"plain_text": v}] if v else []}
    return out


# ---- classify ---------------------------------------------------------------

def test_classify_existing_categories_unchanged():
    assert gs.classify(["订单"], ["订单"]) == "一致"
    assert gs.classify(["订单"], ["订单管理"]) == "文件與 OMS 不一致"
    assert gs.classify(["订单", "订单管理"], ["订单"]) == "OMS 自己不一致"
    assert gs.classify(["订单"], []) == "僅 OMS 有"
    assert gs.classify(["订单", "订单管理"], []) == "OMS 自己不一致"
    assert gs.classify([], ["订单"]) == "僅文件有"


def test_classify_oms_key_without_chinese():
    assert gs.classify([], [], oms_has_key=True) == "OMS 缺中文"


def test_classify_nothing_to_compare():
    assert gs.classify([], []) == "無資料可比對"


def test_classify_never_returns_placeholder():
    """「待比對」只代表腳本還沒跑過，跑完不能再停在這裡。"""
    for oms_cn in ([], ["a"], ["a", "b"]):
        for doc_cn in ([], ["a"]):
            for has_key in (False, True):
                assert gs.classify(oms_cn, doc_cn, has_key) != "待比對"


# ---- plan_row ---------------------------------------------------------------

WANT = {"文件現況": "", "OMS v0 現況": "订单", "i18n key": "order.title",
        "一致性": "僅 OMS 有", "文件出現次數": 0, "OMS 使用處數": 1}


def test_draft_row_writes_full_diff():
    p = props(**{"一致性": "僅文件有", "OMS v0 現況": "旧值", "文件出現次數": 3})
    write, rest = gs.plan_row(p, WANT, fill_only=False)
    assert write["一致性"] == "僅 OMS 有"
    assert write["OMS v0 現況"] == "订单"
    assert write["文件出現次數"] == 0
    assert rest == {}


def test_fill_only_fills_blank_and_placeholder():
    p = props(**{"一致性": "待比對", "OMS v0 現況": "", "i18n key": ""})
    write, rest = gs.plan_row(p, WANT, fill_only=True)
    assert write == {"一致性": "僅 OMS 有", "OMS v0 現況": "订单", "i18n key": "order.title",
                     "文件出現次數": 0, "OMS 使用處數": 1}
    assert rest == {}


def test_fill_only_never_overwrites_existing_values():
    p = props(**{"一致性": "僅文件有", "OMS v0 現況": "人工寫的", "i18n key": "",
                 "文件出現次數": 5, "OMS 使用處數": None})
    write, rest = gs.plan_row(p, WANT, fill_only=True)
    assert write == {"i18n key": "order.title", "OMS 使用處數": 1}
    assert rest == {"一致性": "僅 OMS 有", "OMS v0 現況": "订单", "文件出現次數": 0}


def test_fill_only_does_not_write_empty_values():
    """現值是 None、比對結果是空字串：寫進去沒有意義，也不算差異要回報。"""
    p = {"文件現況": {"type": "rich_text", "rich_text": []}}
    write, _ = gs.plan_row(p, {"文件現況": ""}, fill_only=True)
    assert write == {}


def test_fill_only_leaves_already_classified_row_alone():
    p = props(**{"一致性": "無資料可比對"})
    write, rest = gs.plan_row(p, {"一致性": "無資料可比對"}, fill_only=True)
    assert write == {} and rest == {}
