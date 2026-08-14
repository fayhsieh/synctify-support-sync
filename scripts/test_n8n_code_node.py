"""
在本機模擬 n8n Python Code node 的執行環境，驗證 n8n/code-node.py 真的能跑。

模擬三件 n8n 的行為：
  1. 程式被包在函式內（所以頂層可以 `return`）
  2. 提供 `_items` 變數（Run Once for All Items 模式）
  3. 只允許 `import re`（n8n v2 runner 的 stdlib allowlist）

執行：
    ./.venv/bin/python scripts/test_n8n_code_node.py
"""
import builtins
import json
import pathlib
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE = ROOT / "n8n" / "code-node.py"
TP_CODE = ROOT / "n8n" / "tp-code-node.py"

# n8n v2 的 Python runner 會拒絕「不安全的內建函式」。實測 `hasattr` 即被擋
# （錯誤：name 'hasattr' is not defined），故此處以保守清單模擬：
# 內省／動態執行／IO 類一律視為不可用，只留純資料處理需要的內建。
BLOCKED_BUILTINS = {
    "hasattr", "getattr", "setattr", "delattr", "vars", "dir",
    "globals", "locals", "eval", "exec", "compile", "__import__",
    "open", "input", "breakpoint", "memoryview", "id", "callable",
}


def run_as_n8n(source, items, allow={"re"}):
    """把 source 包成函式並在受限環境下執行（限制 import 與內建函式），回傳其 return 值。"""
    wrapped = "def __n8n_main(_items):\n" + textwrap.indent(source, "    ")

    real_import = builtins.__import__

    def guarded(name, *a, **k):
        root = name.split(".")[0]
        if root not in allow and root not in sys.builtin_module_names:
            raise ImportError(f"BLOCKED: {name}（模擬 n8n runner import allowlist）")
        return real_import(name, *a, **k)

    # 以剔除過的 __builtins__ 執行，模擬 runner 拒絕不安全內建的行為
    safe_builtins = {k: v for k, v in vars(builtins).items()
                     if k not in BLOCKED_BUILTINS}
    safe_builtins["__import__"] = guarded

    ns = {"__builtins__": safe_builtins}
    try:
        exec(compile(wrapped, "n8n-code-node", "exec"), ns)
        return ns["__n8n_main"](items)
    finally:
        pass


def main():
    source = CODE.read_text(encoding="utf-8")
    failures = []

    # ── 情境 1：無輸入 → 自我測試分支 ──
    out = run_as_n8n(source, items=[])
    res = out[0]["json"]
    print("情境 1｜無輸入（自我測試分支）")
    print(f"  SELF_TEST = {res['SELF_TEST']}")
    for k, v in res["checks"].items():
        print(f"    {'✅' if v else '❌'} {k}")
    print(f"  containers={res['containers']} widgets={res['widgets']}")
    if res["SELF_TEST"] != "PASS":
        failures.append("自我測試分支未通過")

    # ── 情境 2：真實 blocks 輸入 ──
    blocks = [
        {"id": "h", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Reinstate"}]}},
        {"id": "n1", "type": "numbered_list_item",
         "numbered_list_item": {"rich_text": [{"plain_text": "Open the menu"}]}},
        {"id": "n2", "type": "numbered_list_item", "has_children": True,
         "numbered_list_item": {"rich_text": [{"plain_text": "Select it"}]},
         "children": [{"id": "img", "type": "image",
                       "image": {"external": {"url": "https://x/a.png"},
                                 "caption": [{"plain_text": "The menu"}]}}]},
        {"id": "n3", "type": "numbered_list_item",
         "numbered_list_item": {"rich_text": [{"plain_text": "Click Submit"}]}},
        {"id": "t", "type": "table", "table": {"table_width": 2}, "children": [
            {"id": "r1", "type": "table_row", "table_row": {"cells": [
                [{"plain_text": "Column"}], [{"plain_text": "Description"}]]}},
            {"id": "r2", "type": "table_row", "table_row": {"cells": [
                [{"plain_text": "Source"}], [{"plain_text": "System or Custom"}]]}},
        ]},
        {"id": "c", "type": "callout", "callout": {
            "rich_text": [{"plain_text": "Note", "annotations": {"bold": True}}],
            "icon": {"emoji": "ℹ️"}, "color": "blue_background"}},
    ]
    out = run_as_n8n(source, items=[{"json": {
        "blocks": blocks, "title": "Manage Exception Orders",
        "faq_group": "manage-exception-orders", "sync_date": "July 29, 2026"}}])
    r = out[0]["json"]
    tpl = r["template"]

    def widgets(els):
        acc = []
        for e in els:
            if e.get("elType") == "widget":
                acc.append(e)
            acc += widgets(e.get("elements", []))
        return acc

    ws = widgets(tpl["content"])
    olists = [w for w in ws if w["widgetType"] == "docly_list_item"]
    tables = [w for w in ws if w["widgetType"] == "text-editor"
              and "<table>" in w["settings"].get("editor", "")]
    alerts = [w for w in ws if w["widgetType"] == "docly_alerts_box"]
    steps = olists[0]["settings"]["ul_icon_list"] if olists else []

    checks = {
        "template schema 正確": set(tpl.keys()) == {"content", "page_settings", "version",
                                                    "title", "type"},
        "title 正確": tpl["title"] == "Manage Exception Orders",
        "數字清單單一 widget、3 步": len(olists) == 1 and len(steps) == 3,
        "步驟內嵌圖 → [caption] shortcode": bool(steps) and "[caption" in steps[1]["text"],
        "caption 含 size-large 1024x576": bool(steps) and 'width="1024" height="576"' in steps[1]["text"],
        "表格 → HTML <table>": len(tables) == 1,
        "callout → info": bool(alerts) and alerts[0]["settings"].get("alert_type") == "info",
        "回傳含 markdown/report": "markdown" in r and "report" in r,
    }
    print("\n情境 2｜真實 blocks 輸入")
    for k, v in checks.items():
        print(f"    {'✅' if v else '❌'} {k}")
    failures += [k for k, v in checks.items() if not v]

    # ── 情境 2b：n8n Notion 節點的原生形狀（一個 block 一個 item）──
    # Set 節點會把 title/faq_group 加到每個 item 上
    items_per_block = [{"json": dict(b, title="Manage Exception Orders",
                                     faq_group="manage-exception-orders",
                                     sync_date="July 29, 2026")} for b in blocks]
    out_b = run_as_n8n(source, items=items_per_block)
    rb = out_b[0]["json"]
    ws_b = widgets(rb["template"]["content"])
    olists_b = [w for w in ws_b if w["widgetType"] == "docly_list_item"]
    checks_b = {
        "一個 block 一個 item 也能處理": len(olists_b) == 1
                                       and len(olists_b[0]["settings"]["ul_icon_list"]) == 3,
        "title 從 item 上讀到": rb["template"]["title"] == "Manage Exception Orders",
        "與單一 item 形狀結果一致": rb["template"]["content"] == tpl["content"],
    }
    print("\n情境 2b｜Notion 節點原生形狀（一個 block 一個 item）")
    for k, v in checks_b.items():
        print(f"    {'✅' if v else '❌'} {k}")
    failures += [k for k, v in checks_b.items() if not v]

    # ── 情境 2c：Notion 內部連結解析 ──
    # 這一項專門守「被包進函式後模組層級狀態會變成外層區域變數」的坑：
    # convert() 宣告 global 寫入，讀取端若沒宣告就會讀到外層那份空的，
    # 結果是查得到卻換不掉、而且完全不報錯（2026-08-11 實測踩到）。
    # 必須跑過打包後的程式才抓得到——直接 import 模組時不會重現。
    link_blocks = [
        {"id": "h", "type": "heading_2",
         "heading_2": {"rich_text": [{"plain_text": "Export"}]}},
        {"id": "p", "type": "paragraph", "paragraph": {"rich_text": [
            {"plain_text": "See ", "annotations": {}},
            {"plain_text": "7-1 Reports Center", "annotations": {},
             "href": "https://app.notion.com/p/3272f2ede27d808db5d4d7f4a6796142"},
        ]}},
    ]
    hub_rows = [{"id": "3272f2ed-e27d-808d-b5d4-d7f4a6796142",
                 "properties": {
                     "WP Post ID": {"rich_text": [{"plain_text": "6118"}]},
                     "Doc name": {"title": [{"plain_text": "7-1 Reports Center"}]},
                     "Parent item": {"relation": []}}}]
    wp_docs = [{"id": 6118,
                "link": "https://support.synctify.io/docs/x/reports-center/",
                "title": {"rendered": "Reports Center"}}]
    out_c = run_as_n8n(source, items=[{"json": dict(
        b, title="T", faq_group="t", sync_date="July 29, 2026",
        hub_rows=hub_rows, wp_docs=wp_docs)} for b in link_blocks])
    rc = out_c[0]["json"]
    written = rc["links_written"]
    checks_c = {
        "對照表有組起來": rc["link_map_size"] == 1,
        "診斷認得出這是 Notion 連結": rc["links_seen"][0]["in_map"] is True,
        "實際寫出的是 WP 永久連結": written == ["https://support.synctify.io/docs/x/reports-center/"],
        "連結文字換成 WP 標題（去掉 7-1 編號）":
            ">Reports Center</a>" in json.dumps(rc["template"], ensure_ascii=False),
    }
    print("\n情境 2c｜Notion 內部連結 → WP 永久連結")
    for k, v in checks_c.items():
        print(f"    {'✅' if v else '❌'} {k}")
    failures += [k for k, v in checks_c.items() if not v]

    # ── 情境 3：確認沙箱真的有在擋（import 與不安全內建）──
    print("\n情境 3｜沙箱自我驗證")
    try:
        run_as_n8n("import json\nreturn []", items=[])
        failures.append("沙箱失效：import json 竟然成功")
        print("    ❌ import json 未被擋")
    except ImportError:
        print("    ✅ import json 被正確擋下")
    try:
        run_as_n8n("return [hasattr({}, 'get')]", items=[])
        failures.append("沙箱失效：hasattr 竟然可用")
        print("    ❌ hasattr 未被擋")
    except NameError:
        print("    ✅ hasattr 被正確擋下（對應 n8n 實測錯誤）")

    # ── 情境 4：Workflow 3 的區塊抽取節點 ──
    print("\n情境 4｜tp-code-node.py（抽出待翻譯區塊）")
    tp_source = TP_CODE.read_text(encoding="utf-8")

    page = (
        '<html><body><header><p>側邊欄不該被抽到</p></header>'
        '<div data-elementor-type="wp-post" data-elementor-id="7251" '
        'class="elementor elementor-7251">'
        "<p>Find <strong>New Order Frozen Period</strong> and enable it.</p>"
        "<p>Already translated by a human.</p>"
        '<p><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe></p>'
        "</div><footer><p>頁尾不該被抽到</p></footer></body></html>"
        + "<!-- padding -->" * 40          # 撐過介面層的長度門檻
    )
    tp_items = [
        {"json": {"data": page}},                       # HTTP：頁面（responseFormat=text）
        {"json": {"language": "zh_CN", "total": 1, "items": [
            {"id": 1, "original": "Already translated by a human.",
             "translated": "已由人工翻譯。", "status": 2, "block_type": 1}]}},
    ]
    out = run_as_n8n(tp_source, items=tp_items)[0]["json"]
    pend = [b["original"] for b in out.get("pending", [])]
    checks_d = {
        "在只允許 re 的環境下跑得起來": out.get("ok") is True,
        "自己從頁面認出 post_id": out.get("post_id") == 7251,
        "抽到待翻譯的區塊": pend == ["Find <strong>New Order Frozen Period</strong> and enable it."],
        "人工譯文（status=2）不再重送": "Already translated by a human." not in pend,
        "側邊欄與頁尾被範圍擋掉": not any("不該被抽到" in p for p in pend),
        "GTM 的 iframe 被當成非散文跳過": not any("googletagmanager" in p for p in pend),
    }
    for k, v in checks_d.items():
        print(f"    {'✅' if v else '❌'} {k}")
    failures += [k for k, v in checks_d.items() if not v]

    # 兩個輸入互換順序仍要能認出來——實際接線可能是 Merge，也可能兩條線直接接進來
    out2 = run_as_n8n(tp_source, items=list(reversed(tp_items)))[0]["json"]
    ok_order = out2.get("ok") is True and out2.get("post_id") == 7251
    print(f"    {'✅' if ok_order else '❌'} 輸入順序顛倒也認得出來")
    if not ok_order:
        failures.append("tp：輸入順序顛倒就失敗")

    # 缺 HTML 時要回可讀的原因，而不是丟例外讓 n8n 顯示 undefined
    out3 = run_as_n8n(tp_source, items=[{"json": {"foo": "bar"}}])[0]["json"]
    # 訊息刻意用 n8n UI 上的標籤（Response Format），照著找得到那個設定
    ok_err = out3.get("ok") is False and "Response Format" in (out3.get("error") or "")
    print(f"    {'✅' if ok_err else '❌'} 缺頁面 HTML 時給出可讀的原因")
    if not ok_err:
        failures.append("tp：缺 HTML 時沒有可讀訊息")

    print("\n" + "=" * 55)
    if failures:
        print("❌ 未通過：" + "、".join(failures))
        sys.exit(1)
    print("✅ 全部通過 —— n8n/*code-node.py 可在僅允許 re 的環境下正確執行")


if __name__ == "__main__":
    main()
