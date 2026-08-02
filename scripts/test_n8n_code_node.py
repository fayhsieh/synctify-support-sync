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


def run_as_n8n(source, items, allow={"re"}):
    """把 source 包成函式並在受限 import 環境下執行，回傳其 return 值。"""
    wrapped = "def __n8n_main(_items):\n" + textwrap.indent(source, "    ")

    real_import = builtins.__import__

    def guarded(name, *a, **k):
        root = name.split(".")[0]
        if root not in allow and root not in sys.builtin_module_names:
            raise ImportError(f"BLOCKED: {name}（模擬 n8n runner allowlist）")
        return real_import(name, *a, **k)

    ns = {}
    builtins.__import__ = guarded
    try:
        exec(compile(wrapped, "n8n-code-node", "exec"), ns)
        return ns["__n8n_main"](items)
    finally:
        builtins.__import__ = real_import


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

    # ── 情境 3：確認 re 以外的 import 真的被擋（證明沙箱有效）──
    try:
        run_as_n8n("import json\nreturn []", items=[])
        failures.append("沙箱失效：import json 竟然成功")
        print("\n情境 3｜❌ 沙箱失效")
    except ImportError:
        print("\n情境 3｜✅ 沙箱有效（import json 被正確擋下）")

    print("\n" + "=" * 55)
    if failures:
        print("❌ 未通過：" + "、".join(failures))
        sys.exit(1)
    print("✅ 全部通過 —— n8n/code-node.py 可在僅允許 re 的環境下正確執行")


if __name__ == "__main__":
    main()
