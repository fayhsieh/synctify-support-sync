"""glossary_review 的測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_glossary_review.py -v
"""
import ast
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import glossary_review as gr  # noqa: E402

REVIEW_DB = "0caf57e29f4a4831b93b7c5766a97fa4"
REVIEW_PAGE = "3dc2f2ede27d81609ffae4e44ee1d02e"
NOW = "2026-09-15 19:00"


def _rt(text):
    return {"type": "rich_text", "rich_text": [{"plain_text": text}] if text else []}


def full_page(pid, en, zh="", tw="", kind="UI 標籤", note="", ok=False, **extra):
    props = {
        "English": {"type": "title", "title": [{"plain_text": en}]},
        "简体中文": _rt(zh), "繁體中文": _rt(tw), "備註": _rt(note),
        "類型": {"type": "select", "select": {"name": kind} if kind else None},
        "已確認": {"type": "checkbox", "checkbox": ok},
        "一致性": {"type": "select", "select": {"name": extra.get("consistency", "僅 OMS 有")}},
        "OMS v0 現況": _rt(extra.get("oms", "")), "文件現況": _rt(extra.get("doc", "")),
    }
    return {"id": pid, "properties": props}


def review_page(rid, source_page, snapshot_of=None, status="", **changes):
    """從完整表的列複製成審核區的列；changes 覆寫審核區的值（模擬心柔的修改）。"""
    base = gr.review_values(source_page)
    values = dict(base)
    values.update(changes)
    props = {
        "English": {"type": "title", "title": [{"plain_text": values["English"]}]},
        "简体中文": _rt(values["简体中文"]), "繁體中文": _rt(values["繁體中文"]), "備註": _rt(values["備註"]),
        "類型": {"type": "select", "select": {"name": values["類型"]} if values["類型"] else None},
        "已確認": {"type": "checkbox", "checkbox": values["已確認"]},
        gr.REVIEW_SOURCE: {"type": "url", "url": gr.REVIEW_PAGE_URL_PREFIX + base["id"]},
        gr.REVIEW_SNAPSHOT: _rt(gr.encode_snapshot(gr.review_values(snapshot_of or source_page))),
        gr.REVIEW_STATUS: _rt(status),
    }
    return {"id": rid, "properties": props}


CHILDREN = [
    {"id": "para-1", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "最後動作：（尚未執行）"}]}},
    {"id": "callout", "type": "callout", "callout": {"rich_text": []}},
]


def ops(plan, phase):
    return plan["phases"][phase]


# ---- 基本 ---------------------------------------------------------------

def test_module_has_no_imports():
    """要能原樣放進 n8n Python Code node（只允許 re，這裡連 re 都不用）。"""
    tree = ast.parse((HERE / "glossary_review.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_norm_id_accepts_dashed_ids_and_urls():
    raw = "3dc2f2ed-e27d-8160-9ffa-e4e44ee1d02e"
    assert gr.review_norm_id(raw) == "3dc2f2ede27d81609ffae4e44ee1d02e"
    assert gr.review_norm_id("https://app.notion.com/p/3dc2f2ede27d81609ffae4e44ee1d02e?pvs=204") \
        == "3dc2f2ede27d81609ffae4e44ee1d02e"
    assert gr.review_norm_id("https://www.notion.so/Some-Title-3dc2f2ede27d81609ffae4e44ee1d02e") \
        == "3dc2f2ede27d81609ffae4e44ee1d02e"
    assert gr.review_norm_id("") == ""
    assert gr.review_norm_id("not an id") == ""


def test_snapshot_roundtrip_with_newlines_tabs_backslashes():
    values = {"English": "Tab\there", "简体中文": "第一行\n第二行", "繁體中文": "C:\\path\\n",
              "類型": "", "備註": "⚠️ 備註\n\n含空行", "已確認": True}
    assert gr.decode_snapshot(gr.encode_snapshot(values)) == values


def test_snapshot_broken_returns_none():
    assert gr.decode_snapshot("") is None
    assert gr.decode_snapshot("English\tA\n简体中文\tB") is None


# ---- 取出待確認 ---------------------------------------------------------

def test_pull_creates_only_unconfirmed_rows_not_already_in_review():
    a = full_page("a" * 32, "Cause", "原因", note="OMS order.labels.cause")
    b = full_page("b" * 32, "Add", "添加", "新增", ok=True)
    c = full_page("c" * 32, "Backorder", "")
    review = [review_page("r" * 32, c, 简体中文="心柔改到一半")]
    plan = gr.review_pull_plan([a, b, c], review, CHILDREN, REVIEW_DB, NOW)
    creates = [op for op in ops(plan, 0) if op["method"] == "POST"]
    assert [op["note"] for op in creates] == ["取出：Cause"]
    props = creates[0]["body"]["properties"]
    assert creates[0]["body"]["parent"] == {"database_id": REVIEW_DB}
    assert props["简体中文"] == {"rich_text": [{"type": "text", "text": {"content": "原因"}}]}
    assert props["備註"]["rich_text"][0]["text"]["content"] == "OMS order.labels.cause"
    assert props["已確認"] == {"checkbox": False}
    assert props[gr.REVIEW_SOURCE] == {"url": gr.REVIEW_PAGE_URL_PREFIX + "a" * 32}
    snap = gr.decode_snapshot(props[gr.REVIEW_SNAPSHOT]["rich_text"][0]["text"]["content"])
    assert snap["简体中文"] == "原因" and snap["已確認"] is False
    # 審核區已有的 Backorder 沒有被覆蓋（沒有對它的任何操作）
    assert not [op for op in ops(plan, 0) if "Backorder" in op["note"]]
    assert plan["counts"] == {"created": 1, "flagged": 0, "total": 2}


def test_pull_sorted_by_english_and_reference_fields_copied():
    rows = [full_page("b" * 32, "zeta"), full_page("a" * 32, "Alpha", oms="阿尔法", doc="甲", consistency="一致")]
    plan = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW)
    assert [op["note"] for op in ops(plan, 0)] == ["取出：Alpha", "取出：zeta"]
    props = ops(plan, 0)[0]["body"]["properties"]
    assert props["一致性"] == {"select": {"name": "一致"}}
    assert props["OMS v0 現況"]["rich_text"][0]["text"]["content"] == "阿尔法"


def test_pull_flags_review_rows_whose_source_is_gone_or_confirmed():
    gone = full_page("d" * 32, "Orgnization", "组织")
    done = full_page("e" * 32, "Carrier", "承运商")
    done_now = full_page("e" * 32, "Carrier", "承运商", ok=True)
    review = [review_page("1" * 32, gone), review_page("2" * 32, done)]
    plan = gr.review_pull_plan([done_now], review, CHILDREN, REVIEW_DB, NOW)
    notes = {op["note"]: op["body"]["properties"][gr.REVIEW_STATUS]["rich_text"][0]["text"]["content"]
             for op in ops(plan, 0)}
    assert notes["推送狀態：Orgnization"].startswith("⚠️ 完整表已經沒有這一列")
    assert notes["推送狀態：Carrier"].startswith("⚠️ 完整表這一列已經被勾「已確認」")
    assert plan["counts"]["flagged"] == 2


def test_pull_does_not_rewrite_same_flag():
    gone = full_page("d" * 32, "Orgnization")
    msg = "⚠️ 完整表已經沒有這一列（可能被刪除或合併），不會推送；確認後可以刪掉這列"
    plan = gr.review_pull_plan([], [review_page("1" * 32, gone, status=msg)], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 0) == []


def test_pull_summary_written_to_paragraph_last():
    plan = gr.review_pull_plan([full_page("a" * 32, "Cause")], [], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 1) == [{"method": "PATCH", "path": "/blocks/para-1", "note": "狀態列", "body": {
        "paragraph": {"rich_text": [{"type": "text", "text": {"content":
            "最後動作：2026-09-15 19:00 取出待確認｜新增 1 列｜審核區共 1 列"}}]}}}]


def test_pull_without_summary_paragraph_skips_summary():
    plan = gr.review_pull_plan([full_page("a" * 32, "Cause")], [], [], REVIEW_DB, NOW)
    assert ops(plan, 1) == []


def test_archived_pages_are_ignored():
    archived = dict(full_page("a" * 32, "Cause"), archived=True)
    plan = gr.review_pull_plan([archived], [], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 0) == []


# ---- 推送回完整表 -------------------------------------------------------

def test_push_writes_changed_fields_and_keeps_unconfirmed_row_with_new_snapshot():
    src = full_page("a" * 32, "Cause", "", note="舊備註")
    rev = review_page("1" * 32, src, 简体中文="原因", 繁體中文="原因")
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)
    full_ops, review_ops, final_ops = plan["phases"]
    assert full_ops == [{"method": "PATCH", "path": "/pages/" + "a" * 32, "note": "寫回：Cause", "body": {
        "properties": {"简体中文": {"rich_text": [{"type": "text", "text": {"content": "原因"}}]},
                       "繁體中文": {"rich_text": [{"type": "text", "text": {"content": "原因"}}]}}}}]
    assert len(review_ops) == 1 and review_ops[0]["path"] == "/pages/" + "1" * 32
    props = review_ops[0]["body"]["properties"]
    assert gr.decode_snapshot(props[gr.REVIEW_SNAPSHOT]["rich_text"][0]["text"]["content"])["简体中文"] == "原因"
    assert props[gr.REVIEW_STATUS]["rich_text"][0]["text"]["content"] == "✓ 2026-09-15 19:00 已推送：简体中文、繁體中文"
    assert plan["counts"] == {"pushed": 1, "confirmed": 0, "conflicts": 0, "missing": 0, "broken": 0}


def test_push_confirmed_row_is_written_then_archived():
    src = full_page("a" * 32, "Cause", "原因")
    rev = review_page("1" * 32, src, 已確認=True)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)
    full_ops, review_ops, _ = plan["phases"]
    assert full_ops[0]["body"] == {"properties": {"已確認": {"checkbox": True}}}
    assert review_ops == [{"method": "PATCH", "path": "/pages/" + "1" * 32,
                           "body": {"archived": True}, "note": "移出審核區：Cause"}]


def test_push_already_confirmed_and_identical_row_just_archived():
    src = full_page("a" * 32, "Cause", "原因", ok=True)
    rev = review_page("1" * 32, src)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0] == []
    assert plan["phases"][1][0]["body"] == {"archived": True}


def test_push_english_rename_and_empty_select():
    src = full_page("a" * 32, "Orgnization", "组织", kind="UI 標籤")
    rev = review_page("1" * 32, src, English="Organization", 類型="")
    props = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)["phases"][0][0]["body"]["properties"]
    assert props["English"] == {"title": [{"type": "text", "text": {"content": "Organization"}}]}
    assert props["類型"] == {"select": None}


def test_push_conflict_when_full_table_changed_a_field_since_pull():
    at_pull = full_page("a" * 32, "Carrier", "承运商", note="舊")
    now_full = full_page("a" * 32, "Carrier", "承运商", note="Fay 在完整表改了備註")
    rev = review_page("1" * 32, now_full, snapshot_of=at_pull, 简体中文="物流商", 備註="舊")
    plan = gr.review_push_plan([now_full], [rev], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0] == []
    status = plan["phases"][1][0]["body"]["properties"][gr.REVIEW_STATUS]["rich_text"][0]["text"]["content"]
    assert status.startswith("⚠️ 完整表在取出後被改過（備註）")
    assert plan["counts"]["conflicts"] == 1
    assert "1 列沒有推送" in plan["summary"]


def test_push_no_conflict_when_full_table_already_has_the_same_value():
    """上次推送寫到一半就失敗：完整表已經是新值，重按不算衝突、也不重寫。"""
    at_pull = full_page("a" * 32, "Cause", "")
    now_full = full_page("a" * 32, "Cause", "原因")
    rev = review_page("1" * 32, now_full, snapshot_of=at_pull)
    plan = gr.review_push_plan([now_full], [rev], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0] == []
    assert plan["counts"]["conflicts"] == 0
    # 快照更新成現值，下次改別的欄位才不會誤判
    snap_op = plan["phases"][1][0]
    assert gr.decode_snapshot(snap_op["body"]["properties"][gr.REVIEW_SNAPSHOT]["rich_text"][0]["text"]["content"]) \
        ["简体中文"] == "原因"
    assert gr.REVIEW_STATUS not in snap_op["body"]["properties"]


def test_push_unchanged_row_produces_no_ops():
    src = full_page("a" * 32, "Cause", "原因")
    plan = gr.review_push_plan([src], [review_page("1" * 32, src)], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0] == [] and plan["phases"][1] == []
    assert plan["summary"].endswith("沒有需要寫回的改動")


def test_push_missing_source_and_broken_snapshot_are_flagged_not_written():
    src = full_page("a" * 32, "Cause", "原因")
    gone = review_page("1" * 32, full_page("b" * 32, "Gone"), 简体中文="x")
    broken = review_page("2" * 32, src, 简体中文="新")
    broken["properties"][gr.REVIEW_SNAPSHOT] = _rt("被人手動改掉")
    plan = gr.review_push_plan([src], [gone, broken], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0] == []
    messages = [op["body"]["properties"][gr.REVIEW_STATUS]["rich_text"][0]["text"]["content"]
                for op in plan["phases"][1]]
    assert any(m.startswith("⚠️ 完整表已經沒有這一列") for m in messages)
    assert any(m.startswith("⚠️「取出時內容」被改過") for m in messages)
    assert plan["counts"]["missing"] == 1 and plan["counts"]["broken"] == 1


def test_push_log_records_before_and_after_in_full():
    long_note = "字" * 2500
    src = full_page("a" * 32, "Cause", "", note=long_note)
    rev = review_page("1" * 32, src, 简体中文="原因", 已確認=True)
    final_ops = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)["phases"][2]
    log_op, summary_op = final_ops
    assert log_op["path"] == "/blocks/" + REVIEW_PAGE + "/children"
    toggle = log_op["body"]["children"][0]
    assert toggle["toggle"]["rich_text"][0]["text"]["content"] == "2026-09-15 19:00｜寫回 1 列、移出 1 列"
    item = toggle["toggle"]["children"][0]["bulleted_list_item"]["rich_text"]
    text = "".join(seg["text"]["content"] for seg in item)
    assert text == "Cause\n简体中文：（空白） → 原因\n已確認：☐ 未確認 → ☑ 已確認"
    assert item[0]["annotations"] == {"bold": True}
    assert summary_op["path"] == "/blocks/para-1"


def test_log_blocks_split_into_multiple_toggles():
    entries = [(f"T{i}", [("简体中文", "", f"词{i}")]) for i in range(200)]
    blocks = gr.review_log_blocks("標題", entries, per_toggle=90)
    assert [len(b["toggle"]["children"]) for b in blocks] == [90, 90, 20]
    assert blocks[1]["toggle"]["rich_text"][0]["text"]["content"] == "標題（第 2／3 段）"


def test_push_phase_order_full_table_before_review():
    """完整表還沒寫成功，不能先把審核區的列移出——phases 的順序就是執行順序。"""
    src = full_page("a" * 32, "Cause", "")
    rev = review_page("1" * 32, src, 简体中文="原因", 已確認=True)
    phases = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)["phases"]
    assert phases[0][0]["path"] == "/pages/" + "a" * 32
    assert phases[1][0]["body"] == {"archived": True}
