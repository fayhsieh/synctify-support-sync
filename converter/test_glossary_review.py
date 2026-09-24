"""glossary_review 的測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_glossary_review.py -v
"""
import ast
import json
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


def full_page(pid, en, zh="", tw="", kind="UI 標籤", note="", ok=False, modules=(), features=(),
              flag=False, groups=(), **extra):
    props = {
        gr.REVIEW_FLAG: {"type": "checkbox", "checkbox": flag},
        "審核群組": {"type": "multi_select", "multi_select": [{"name": g} for g in groups]},
        "模組": {"type": "multi_select", "multi_select": [{"name": m} for m in modules]},
        "功能": {"type": "multi_select", "multi_select": [{"name": f} for f in features]},
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
        # 參考欄位：真實的審核區列是由 pull 建的，這些欄位跟完整表一致（不然每次同步都會被更新）
        "一致性": {"type": "select", "select": {"name": values["一致性"]} if values["一致性"] else None},
        "OMS v0 現況": _rt(values["OMS v0 現況"]), "文件現況": _rt(values["文件現況"]),
        "模組": {"type": "multi_select", "multi_select": [{"name": m} for m in values["模組"]]},
        "功能": {"type": "multi_select", "multi_select": [{"name": f} for f in values["功能"]]},
        "審核群組": {"type": "multi_select", "multi_select": [{"name": g} for g in values["審核群組"]]},
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


# ---- 同步待確認（按鈕）---------------------------------------------------------

def test_pull_creates_only_unconfirmed_rows_not_already_in_review():
    a = full_page("a" * 32, "Cause", "原因", note="OMS order.labels.cause")
    b = full_page("b" * 32, "Add", "添加", "新增", ok=True)
    c = full_page("c" * 32, "Backorder", "")
    review = [review_page("r" * 32, c, 简体中文="心柔改到一半")]
    plan = gr.review_pull_plan([a, b, c], review, CHILDREN, REVIEW_DB, NOW)
    creates = [op for op in ops(plan, 0) if op["method"] == "POST"]
    assert [op["note"] for op in creates] == ["同步待確認：Cause"]
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
    assert plan["counts"] == {"created": 1, "refreshed": 0, "synced": 0, "flagged": 0, "total": 2}


def test_pull_sorted_by_english_and_reference_fields_copied():
    rows = [full_page("b" * 32, "zeta"), full_page("a" * 32, "Alpha", oms="阿尔法", doc="甲", consistency="一致")]
    plan = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW)
    assert [op["note"] for op in ops(plan, 0)] == ["同步待確認：Alpha", "同步待確認：zeta"]
    props = ops(plan, 0)[0]["body"]["properties"]
    assert props["一致性"] == {"select": {"name": "一致"}}
    assert props["OMS v0 現況"]["rich_text"][0]["text"]["content"] == "阿尔法"


def test_pull_flags_review_rows_whose_source_is_gone_or_confirmed():
    gone = full_page("d" * 32, "Orgnization", "组织")
    done = full_page("e" * 32, "Carrier", "承运商")
    done_now = full_page("e" * 32, "Carrier", "承运商", ok=True)
    # Carrier 在審核區被改過，所以不會被「跟上完整表」帶走，仍要提醒推送時會撞衝突
    review = [review_page("1" * 32, gone), review_page("2" * 32, done, 简体中文="物流商")]
    plan = gr.review_pull_plan([done_now], review, CHILDREN, REVIEW_DB, NOW)
    notes = {op["note"]: op["body"]["properties"][gr.REVIEW_STATUS]["rich_text"][0]["text"]["content"]
             for op in ops(plan, 0)}
    assert notes["推送狀態：Orgnization"].startswith("⚠️ 完整表已經沒有這一列")
    assert notes["推送狀態：Carrier"].startswith("⚠️ 完整表這一列已經被勾「已確認」")
    assert plan["counts"]["flagged"] == 2


def test_pull_syncs_instead_of_flagging_untouched_confirmed_row():
    """沒被改過的列在別處被勾了已確認：直接跟上，不必再提醒會衝突。"""
    done = full_page("e" * 32, "Carrier", "承运商")
    done_now = full_page("e" * 32, "Carrier", "承运商", ok=True)
    plan = gr.review_pull_plan([done_now], [review_page("2" * 32, done)], CHILDREN, REVIEW_DB, NOW)
    assert plan["counts"] == {"created": 0, "refreshed": 1, "synced": 1, "flagged": 0, "total": 1}
    assert ops(plan, 0)[0]["body"]["properties"]["已確認"] == {"checkbox": True}


def test_pull_does_not_rewrite_same_flag():
    gone = full_page("d" * 32, "Orgnization")
    msg = "⚠️ 完整表已經沒有這一列（可能被刪除或合併），不會推送；確認後可以刪掉這列"
    plan = gr.review_pull_plan([], [review_page("1" * 32, gone, status=msg)], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 0) == []


def test_pull_summary_written_to_paragraph_last():
    plan = gr.review_pull_plan([full_page("a" * 32, "Cause")], [], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 1) == [{"method": "PATCH", "path": "/blocks/para-1", "note": "狀態列", "body": {
        "paragraph": {"rich_text": [{"type": "text", "text": {"content":
            "最後動作：2026-09-15 19:00 同步待確認｜新增 1 列｜共 1 列"}}]}}}]


def test_pull_without_summary_paragraph_skips_summary():
    plan = gr.review_pull_plan([full_page("a" * 32, "Cause")], [], [], REVIEW_DB, NOW)
    assert ops(plan, 1) == []


def test_archived_pages_are_ignored():
    archived = dict(full_page("a" * 32, "Cause"), archived=True)
    plan = gr.review_pull_plan([archived], [], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 0) == []


# ---- 待複審：已確認但要請人再看一次（跨模組用詞）-------------------------

def test_pull_takes_confirmed_row_when_flagged():
    """跨模組用詞要心柔一起決定，但取消「已確認」會擋住翻譯，所以改用旗標（Fay 2026-09-23）。"""
    plain = full_page("a" * 32, "Add", "添加", ok=True)
    flagged = full_page("b" * 32, "Add Product", "添加产品", ok=True, flag=True)
    plan = gr.review_pull_plan([plain, flagged], [], CHILDREN, REVIEW_DB, NOW)
    assert [op["note"] for op in ops(plan, 0)] == ["同步待確認：Add Product"]


def test_pull_unchecks_confirmed_on_flagged_row():
    """待複審的列到審核區時先取消打勾：審核區的「已確認」意思是心柔複審過了。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    props = ops(gr.review_pull_plan([src], [], CHILDREN, REVIEW_DB, NOW), 0)[0]["body"]["properties"]
    assert props["已確認"] == {"checkbox": False}
    assert gr.decode_snapshot(props[gr.REVIEW_SNAPSHOT]["rich_text"][0]["text"]["content"])["已確認"] is True


def test_feature_doc_keeps_confirmed_on_flagged_row():
    """OMS 功能文件是交付清單，不是待辦清單：已確認就是已確認，不動打勾。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True, features=["Sales Orders"])
    plan = gr.review_pull_plan([src], [], CHILDREN, REVIEW_DB, NOW,
                               features=["Sales Orders"], pending_only=False, label="從完整表同步")
    assert ops(plan, 0)[0]["body"]["properties"]["已確認"] == {"checkbox": True}


def test_push_never_unconfirms_full_table():
    """還沒複審的列（審核區未勾）推送時不能把完整表的「已確認」取消掉。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 简体中文="新增", 已確認=False)
    props = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)["phases"][0][0]["body"]["properties"]
    assert set(props) == {"简体中文"}


def test_pull_refreshes_stale_reference_fields():
    """審核區列已經存在時，參考欄位要跟完整表對齊——後來才補標的審核群組不能漏（Fay 2026-09-23）。"""
    src = full_page("a" * 32, "3PL Orders", "3PL 订单", ok=True, flag=True, groups=["3. 中英文空格"])
    rev = review_page("1" * 32, src, 已確認=False)
    src["properties"]["審核群組"]["multi_select"].append({"name": "4. 選單名加管理"})   # 事後又標了一組
    src["properties"]["一致性"]["select"] = {"name": "一致"}
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW)
    assert plan["counts"]["refreshed"] == 1
    op, = ops(plan, 0)
    assert op == {"method": "PATCH", "path": "/pages/" + "1" * 32, "note": "更新參考欄位：3PL Orders",
                  "body": {"properties": {
                      "一致性": {"select": {"name": "一致"}},
                      "審核群組": {"multi_select": [{"name": "3. 中英文空格"}, {"name": "4. 選單名加管理"}]}}}}


def test_pull_does_not_touch_snapshot_or_human_fields_when_refreshing():
    """更新參考欄位時不能動快照與心柔改的內容，不然真正的衝突會被吃掉。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True, groups=["6. 添加 vs 新增"])
    rev = review_page("1" * 32, src, 简体中文="新增", 已確認=False)
    src["properties"]["模組"]["multi_select"].append({"name": "order"})
    props = ops(gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW), 0)[0]["body"]["properties"]
    assert set(props) == {"模組"}


def test_pull_syncs_untouched_fields_from_full_table():
    """沒被動過的欄位跟著完整表走：別處決定完推回完整表後，功能文件不該停在舊值（Fay 2026-09-24）。"""
    src = full_page("a" * 32, "Allocations", "", ok=False)
    rev = review_page("1" * 32, src)                       # 建立時與完整表一致
    src["properties"]["简体中文"] = _rt("库存动态分配")          # 之後在完整表填了譯法並勾確認
    src["properties"]["已確認"]["checkbox"] = True
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW, pending_only=False)
    assert plan["counts"]["synced"] == 1
    op, = ops(plan, 0)
    assert op["note"] == "跟上完整表：Allocations"
    props = op["body"]["properties"]
    assert props["简体中文"]["rich_text"][0]["text"]["content"] == "库存动态分配"
    assert props["已確認"] == {"checkbox": True}
    snap = gr.decode_snapshot(props[gr.REVIEW_SNAPSHOT]["rich_text"][0]["text"]["content"])
    assert snap["简体中文"] == "库存动态分配" and snap["已確認"] is True   # 快照跟著更新，之後不會誤判成衝突


def test_pull_never_touches_a_row_someone_edited():
    """只要有一欄被改過就整列不碰：那一列正等著推送回去，動它會把未定案的修改變成定案。"""
    src = full_page("a" * 32, "Cause", "原因", note="舊備註")
    rev = review_page("1" * 32, src, 简体中文="起因")            # 心柔改了简中
    src["properties"]["備註"] = _rt("新備註")                   # 完整表那邊改了備註
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW)
    assert plan["counts"]["synced"] == 0
    assert ops(plan, 0) == []                                 # 備註也不碰，留給推送去判斷


def test_pull_leaves_flagged_rows_confirmation_alone():
    """待複審的列取出時刻意取消打勾，那不算「沒人動過」，不可以被完整表的已確認蓋回去。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 已確認=False)
    assert gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW)["counts"]["synced"] == 0


def test_pull_skips_sync_when_snapshot_is_broken():
    """快照壞掉就不知道誰改過什麼，寧可不動——推送那邊會請人刪掉重來。"""
    src = full_page("a" * 32, "Cause", "原因")
    rev = review_page("1" * 32, src)
    rev["properties"][gr.REVIEW_SNAPSHOT] = _rt("壞掉的快照")
    src["properties"]["简体中文"] = _rt("起因")
    assert gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW)["counts"]["synced"] == 0


def test_feature_doc_never_sends_marketing_only_fields():
    """功能文件沒有「審核群組」這一欄——送過去 Notion 會回 400，整批操作全滅（2026-09-24 實際踩到）。"""
    src = full_page("a" * 32, "Orders", "订单", ok=True, features=["Shell"], groups=["4. 選單名加管理"])
    plan = gr.review_pull_plan([src], [], CHILDREN, REVIEW_DB, NOW,
                               features=["Shell"], pending_only=False, label="從完整表同步")
    assert "審核群組" not in ops(plan, 0)[0]["body"]["properties"]
    assert "模組" in ops(plan, 0)[0]["body"]["properties"]          # 其他參考欄位照送


def test_pull_skips_fields_the_target_database_lacks():
    """各功能文件的欄位不見得一樣：目標資料庫沒有的欄位一律不送。"""
    src = full_page("a" * 32, "Orders", "订单", ok=True, features=["Shell"], modules=["order"])
    rev = review_page("1" * 32, src)
    del rev["properties"]["模組"]                                   # 這份文件沒有「模組」欄
    src["properties"]["模組"]["multi_select"] = [{"name": "menu"}]   # 完整表改了模組
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW,
                               features=["Shell"], pending_only=False)
    assert ops(plan, 0) == []                                       # 沒有可送的欄位就不發呼叫


def test_pull_copies_all_review_groups():
    """審核群組是多選：一列可能同時撞到兩個議題，審核區要看得到全部（Fay 2026-09-23）。"""
    src = full_page("a" * 32, "3PL Orders", "3PL 订单", ok=True, flag=True,
                    groups=["3. 中英文空格", "4. 選單名加管理"])
    props = ops(gr.review_pull_plan([src], [], CHILDREN, REVIEW_DB, NOW), 0)[0]["body"]["properties"]
    assert props["審核群組"] == {"multi_select": [{"name": "3. 中英文空格"}, {"name": "4. 選單名加管理"}]}


def test_pull_does_not_flag_flagged_row_as_conflict():
    """待複審的列是刻意收進來的，不該再標「完整表已勾確認」。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 已確認=False)
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW)
    assert ops(plan, 0) == []


def test_push_clears_flag_when_confirmed():
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 简体中文="新增", 已確認=True)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)
    props = plan["phases"][0][0]["body"]["properties"]
    assert props["简体中文"]["rich_text"][0]["text"]["content"] == "新增"
    assert props[gr.REVIEW_FLAG] == {"checkbox": False}       # 清掉旗標，這列退出審核區
    assert plan["phases"][1][0]["body"] == {"archived": True}


def test_push_keeps_flag_until_confirmed():
    """還沒勾已確認就推送：進度寫回完整表，但旗標留著，列也留在審核區。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 简体中文="新增", 已確認=False)
    props = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)["phases"][0][0]["body"]["properties"]
    assert gr.REVIEW_FLAG not in props


def test_push_clears_flag_even_without_other_changes():
    """心柔看完覺得原譯法就好、只勾確認：仍要清旗標，否則下次同步又被撈回來。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, flag=True)
    rev = review_page("1" * 32, src, 已確認=True)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["phases"][0][0]["body"]["properties"] == {gr.REVIEW_FLAG: {"checkbox": False}}
    assert plan["counts"]["pushed"] == 0 and plan["counts"]["reviewed"] == 1
    assert "複審通過" in plan["summary"]
    log = [op for op in plan["phases"][2] if op["note"] == "推送紀錄"]
    assert "複審通過，維持原譯" in json.dumps(log, ensure_ascii=False)     # 沒改動也要留紀錄


# ---- OMS 功能文件（一個功能一個獨立資料庫）-------------------------------

def test_pull_features_only_takes_those_features():
    """篩選檢視擋不住 AI 讀到別的功能，所以功能文件真的只建該功能的列（Fay 2026-09-22）。"""
    rows = [full_page("a" * 32, "Cause", "原因", features=["Sales Orders"]),
            full_page("b" * 32, "Pallets", "托盘", features=["Shipment Routing"]),
            full_page("c" * 32, "Carrier", "承运商", features=["Orders (Shared)", "Parcel Monitoring"])]
    plan = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW,
                               features=["Sales Orders", "Orders (Shared)"])
    assert [op["note"] for op in ops(plan, 0)] == ["同步待確認：Carrier", "同步待確認：Cause"]
    assert ops(plan, 0)[0]["body"]["properties"]["功能"] == {
        "multi_select": [{"name": "Orders (Shared)"}, {"name": "Parcel Monitoring"}]}
    assert "（Sales Orders、Orders (Shared)）" in plan["summary"]


def test_pull_label_shows_in_summary_and_notes():
    """OMS 的 Glossary 收全部的詞，按鈕叫「同步待確認」會誤導（Fay 2026-09-22）。"""
    rows = [full_page("a" * 32, "Cause", "原因", features=["Sales Orders"])]
    plan = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW, features=["Sales Orders"],
                               pending_only=False, label="從完整表同步")
    assert ops(plan, 0)[0]["note"] == "從完整表同步：Cause"
    assert plan["summary"].startswith("最後動作：2026-09-15 19:00 從完整表同步（Sales Orders）")


def test_pull_features_takes_confirmed_rows_too():
    """交付給工程的清單要完整：pending_only=False 連已確認的也收。"""
    rows = [full_page("a" * 32, "Cause", "原因", features=["Sales Orders"]),
            full_page("b" * 32, "Add", "添加", ok=True, features=["Sales Orders"])]
    pending = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW, features=["Sales Orders"])
    full = gr.review_pull_plan(rows, [], CHILDREN, REVIEW_DB, NOW,
                               features=["Sales Orders"], pending_only=False)
    assert [op["note"] for op in ops(pending, 0)] == ["同步待確認：Cause"]
    assert [op["note"] for op in ops(full, 0)] == ["同步待確認：Add", "同步待確認：Cause"]


def test_pull_features_does_not_flag_confirmed_source():
    """功能文件本來就收已確認的列，不該被標成「完整表已勾確認」。"""
    src = full_page("a" * 32, "Add", "添加", ok=True, features=["Sales Orders"])
    rev = review_page("1" * 32, src, 已確認=False)
    plan = gr.review_pull_plan([src], [rev], CHILDREN, REVIEW_DB, NOW,
                               features=["Sales Orders"], pending_only=False)
    assert ops(plan, 0) == []


def test_push_without_archive_keeps_rows():
    src = full_page("a" * 32, "Cause", "", features=["Sales Orders"])
    rev = review_page("1" * 32, src, 简体中文="原因", 已確認=True)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW, archive_confirmed=False)
    full_ops, review_ops, _ = plan["phases"]
    assert full_ops[0]["body"]["properties"]["简体中文"]["rich_text"][0]["text"]["content"] == "原因"
    assert [op["body"].get("archived") for op in review_ops] == [None]      # 沒有封存，只更新快照
    assert review_ops[0]["note"] == "更新快照：Cause"
    assert "移出審核區" not in plan["summary"]


def test_push_without_archive_is_idempotent():
    """推送完列還在，再按一次不該重寫。"""
    src = full_page("a" * 32, "Cause", "原因", features=["Sales Orders"], ok=True)
    rev = review_page("1" * 32, src, 已確認=True)
    plan = gr.review_push_plan([src], [rev], CHILDREN, REVIEW_PAGE, NOW, archive_confirmed=False)
    assert plan["phases"][0] == [] and plan["phases"][1] == []
    assert plan["summary"].endswith("沒有需要寫回的改動")


# ---- 同步新詞同時建進審核區 ---------------------------------------------

def written_to_read(props):
    """寫入格式 → Notion 讀回的格式（模擬審核區建好後查到的列）。"""
    out = {}
    for name, p in props.items():
        if "title" in p:
            out[name] = {"type": "title", "title": [{"plain_text": s["text"]["content"]} for s in p["title"]]}
        elif "rich_text" in p:
            out[name] = {"type": "rich_text",
                         "rich_text": [{"plain_text": s["text"]["content"]} for s in p["rich_text"]]}
        elif "select" in p:
            out[name] = {"type": "select", "select": p["select"]}
        elif "checkbox" in p:
            out[name] = {"type": "checkbox", "checkbox": p["checkbox"]}
        elif "url" in p:
            out[name] = {"type": "url", "url": p["url"]}
    return out


def test_create_ops_from_rows_the_sync_just_created():
    created = dict(full_page("a" * 32, "Short-Shipped", "", note="2026-09-15 同步〈X〉時自動建立"), object="page")
    result = gr.review_create_ops([created], REVIEW_DB)
    assert [op["note"] for op in result] == ["同步新詞：Short-Shipped"]
    body = result[0]["body"]
    assert body["parent"] == {"database_id": REVIEW_DB}
    assert body["properties"][gr.REVIEW_SOURCE] == {"url": gr.REVIEW_PAGE_URL_PREFIX + "a" * 32}
    assert body["properties"]["備註"]["rich_text"][0]["text"]["content"] == "2026-09-15 同步〈X〉時自動建立"


def test_create_ops_skip_failed_responses_and_confirmed_rows():
    failed = {"error": {"message": "403 insufficient permissions"}}
    confirmed = full_page("a" * 32, "Add", "添加", ok=True)
    assert gr.review_create_ops([failed, confirmed], REVIEW_DB) == []


def test_rows_created_by_sync_push_without_conflict():
    """Fay 2026-09-15：兩邊寫入一樣的內容，推送回去時不會衝突。"""
    created = full_page("a" * 32, "Short-Shipped", "")
    body = gr.review_create_ops([created], REVIEW_DB)[0]["body"]
    review = {"id": "1" * 32, "properties": written_to_read(body["properties"])}
    review["properties"]["简体中文"] = _rt("短装")              # 心柔補簡中、勾確認
    review["properties"]["已確認"] = {"type": "checkbox", "checkbox": True}
    plan = gr.review_push_plan([created], [review], CHILDREN, REVIEW_PAGE, NOW)
    assert plan["counts"]["conflicts"] == 0
    assert plan["phases"][0][0]["body"]["properties"] == {
        "简体中文": {"rich_text": [{"type": "text", "text": {"content": "短装"}}]},
        "已確認": {"checkbox": True}}
    assert plan["phases"][1][0]["body"] == {"archived": True}


def test_pull_after_sync_does_not_duplicate():
    """同步已經建進審核區的詞，之後按「同步待確認」不會重複建立。"""
    created = full_page("a" * 32, "Short-Shipped", "")
    body = gr.review_create_ops([created], REVIEW_DB)[0]["body"]
    review = {"id": "1" * 32, "properties": written_to_read(body["properties"])}
    plan = gr.review_pull_plan([created], [review], CHILDREN, REVIEW_DB, NOW)
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
    assert plan["counts"] == {"pushed": 1, "reviewed": 0, "confirmed": 0,
                              "conflicts": 0, "missing": 0, "broken": 0}


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
    assert status.startswith("⚠️ 完整表在同步到審核區後被改過（備註）")
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
