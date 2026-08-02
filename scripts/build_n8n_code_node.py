#!/usr/bin/env python3
"""
把 converter/ 的兩個模組打包成一份可貼進 n8n Python Code node 的程式。

git 仍是唯一真實來源：改動一律改 converter/*.py、跑測試，再用本腳本重新產生，
不要直接在 n8n UI 上編輯（會造成兩邊漂移）。

用法：
    ./.venv/bin/python scripts/build_n8n_code_node.py            # 產生檔案
    ./.venv/bin/python scripts/build_n8n_code_node.py --check    # 檢查現有產物是否為最新

產物：n8n/code-node.py（gitignore 之外，會進版控以便追蹤實際部署內容）

n8n Code node 設定：
  Mode     = Run Once for All Items
  Language = Python
輸入（第一個 item 的 json）：
  blocks     Notion API block 陣列（Notion 節點 Get Child Blocks 的輸出）
  title      文章標題
  faq_group  FAQ group slug
  sync_date  選填，例 "July 29, 2026"
沒有輸入時會跑內建自我測試，方便貼上後直接按 Execute 驗證環境。
"""
import argparse
import json
import pathlib
import re
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "converter"
OUT = ROOT / "n8n" / "code-node.py"
WF_OUT = ROOT / "n8n" / "notion-to-elementor-test.workflow.json"

# 測試用 Notion 頁面：Manage Exception Orders v2（已有手工轉換版本可比對）
TEST_PAGE_ID = "3822f2ede27d80f1bd47d73c6314bec4"
TEST_TITLE = "Manage Exception Orders"
TEST_FAQ_GROUP = "manage-exception-orders"

# n8n 憑證「引用」——只是識別碼，不含任何密鑰（CLAUDE.md：匯出時確認為引用而非明文）
NOTION_CRED_ID = "xfGHH7Wx4EucMC0X"
NOTION_CRED_NAME = "Support Center Sync"

HEADER = '''# ══════════════════════════════════════════════════════════════════
#  自動產生，請勿直接編輯
#  來源：converter/notion_blocks.py + converter/notion2elementor.py
#  重新產生：./.venv/bin/python scripts/build_n8n_code_node.py
#  修改請改 converter/*.py 並跑 pytest，再重新產生後貼回 n8n
# ══════════════════════════════════════════════════════════════════
'''

ADAPTER = '''

# ══════════════════════════════════════════════════════════════════
#  n8n 介面層
# ══════════════════════════════════════════════════════════════════

_SELFTEST_BLOCKS = [
    {"id": "h", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Overview"}]}},
    {"id": "p", "type": "paragraph", "paragraph": {"rich_text": [
        {"plain_text": "Go to "},
        {"plain_text": "Orders > Exception Orders", "annotations": {"code": True}},
        {"plain_text": " to begin."},
    ]}},
    {"id": "n1", "type": "numbered_list_item",
     "numbered_list_item": {"rich_text": [{"plain_text": "First step"}]}},
    {"id": "n2", "type": "numbered_list_item",
     "numbered_list_item": {"rich_text": [{"plain_text": "Second step"}]}},
]


def _collect(payloads):
    """從 n8n 輸入取出 (blocks, meta)。支援兩種上游接法：

      A) 一個 block 一個 item —— n8n Notion 節點 Get Child Blocks 的原生輸出
      B) 單一 item 帶 blocks 陣列 —— 上游接了 Aggregate／Code 整併過

    title／faq_group 由 Set 節點加在 item 上（A 的情況會加在每個 item，取第一個即可）。
    """
    if not payloads:
        return [], {}
    first = payloads[0]
    if "blocks" in first:
        return first["blocks"], first
    if "type" in first:          # item 本身就是 Notion block
        return payloads, first
    return [], first


def _run(blocks, meta):
    title = meta["title"] if "title" in meta else "Untitled"
    faq_group = meta["faq_group"] if "faq_group" in meta else "untitled"
    sync_date = meta["sync_date"] if "sync_date" in meta else None

    markdown, blocks_report = blocks_to_markdown(blocks)
    template, faq_items, report = convert(markdown, title, faq_group, sync_date=sync_date)
    report["blocks"] = blocks_report
    return {
        "template": template,
        "faq_items": faq_items,
        "report": report,
        "markdown": markdown,
    }


_payloads = []
for _it in _items:
    _payloads.append(_it["json"])

_blocks, _meta = _collect(_payloads)

if not _blocks:
    # 沒有真實輸入 → 跑自我測試，確認執行環境與程式本身都正常
    _out = _run(_SELFTEST_BLOCKS, {"title": "Self Test", "faq_group": "self-test",
                                   "sync_date": "July 29, 2026"})
    _steps = 0
    _direction_ok = False
    for _c in _out["template"]["content"]:
        for _w in _c["elements"]:
            if _w["widgetType"] == "docly_list_item":
                _steps = len(_w["settings"]["ul_icon_list"])
            if _w["widgetType"] == "text-editor":
                if "[direction]Orders &gt; Exception Orders[/direction]" in _w["settings"]["editor"]:
                    _direction_ok = True
    _checks = {
        "數字清單 2 步（單一 widget、編號連續）": _steps == 2,
        "inline code → [direction] 且 > 轉成 &gt;": _direction_ok,
        "標題 Notion H1 → h2": _out["markdown"].startswith("## Overview"),
    }
    return [{"json": {
        "SELF_TEST": "PASS" if all(_checks.values()) else "FAIL",
        "checks": _checks,
        "containers": len(_out["template"]["content"]),
        "widgets": _out["report"]["widgets"],
        "note": "未收到 blocks 輸入，這是自我測試。接上 Notion 節點後會轉換真實內容。",
    }}]

return [{"json": _run(_blocks, _meta)}]
'''


def build():
    blocks_src = (CONVERTER / "notion_blocks.py").read_text(encoding="utf-8")
    conv_src = (CONVERTER / "notion2elementor.py").read_text(encoding="utf-8")

    # 移除 CLI 區塊（會 import json/sys，n8n 端不需要）
    conv_src = re.split(r'^if __name__ == "__main__":', conv_src, flags=re.M)[0]

    # 兩個模組都有 `import re`，保留第一個即可
    conv_src = re.sub(r"^import re$", "", conv_src, count=1, flags=re.M)

    body = "\n".join([
        HEADER,
        "# ─── converter/notion_blocks.py ───",
        blocks_src.rstrip(),
        "",
        "# ─── converter/notion2elementor.py ───",
        conv_src.rstrip(),
        ADAPTER.rstrip(),
        "",
    ])
    return body


def build_workflow(code):
    """產生獨立的唯讀測試 workflow：Notion 讀取 → 補欄位 → 轉換。不寫任何東西。"""
    def nid():
        return str(uuid.uuid4())

    nodes = [
        {
            "parameters": {},
            "id": nid(), "name": "手動觸發",
            "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
            "position": [0, 300],
        },
        {
            "parameters": {
                "resource": "block",
                "operation": "getAll",
                # blockId 是 resource locator，必須給物件；給純字串會讓 mode 未設定，
                # 底層呼叫失敗且 n8n 的 prepareNotionError 會跟著崩
                # （錯誤訊息會變成 "Cannot read properties of undefined (reading 'match')"）
                "blockId": {
                    "__rl": True,
                    "value": TEST_PAGE_ID,
                    "mode": "id",
                },
                "returnAll": True,
                "fetchNestedBlocks": True,
            },
            "id": nid(), "name": "Notion：取得頁面 blocks",
            "type": "n8n-nodes-base.notion", "typeVersion": 2.2,
            "position": [240, 300],
            # 憑證「引用」（非明文），省去每次匯入重選；值取自 Fay 的實際匯出檔
            "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
            "notes": "務必確認 Return All 與 Also Fetch Nested Blocks 兩個開關都開啟；"
                     "表格的列(table_row)與步驟下的圖片都是巢狀 block，沒開會抓不到。",
        },
        {
            "parameters": {
                "includeOtherFields": True,
                "assignments": {"assignments": [
                    {"id": nid(), "name": "title", "value": TEST_TITLE, "type": "string"},
                    {"id": nid(), "name": "faq_group", "value": TEST_FAQ_GROUP, "type": "string"},
                ]},
                "options": {},
            },
            "id": nid(), "name": "補上標題與 FAQ group",
            "type": "n8n-nodes-base.set", "typeVersion": 3.4,
            "position": [480, 300],
            "notes": "Include Other Fields 必須開啟，否則 block 內容會被覆蓋掉。",
        },
        {
            "parameters": {
                # ⚠️ 語言值必須是 "pythonNative"（n8n 2.x 原生 Python runner）。
                # 舊版 Pyodide 的 "python" 或 UI 標籤 "Python" 都不合法，n8n 會把整個
                # 節點重設為預設值——連 pythonCode 一起丟掉，導致跑的是預設範本程式
                # 而非我們的轉換器（且不會報錯，很容易誤判成成功）。
                # 值取自 n8n 2.32.6 的實際匯出檔。
                "language": "pythonNative",
                # mode 不指定，沿用預設的 Run Once for All Items（n8n 匯出時亦不帶此鍵）
                "pythonCode": code,
            },
            "id": nid(), "name": "轉換：blocks → Elementor JSON",
            "type": "n8n-nodes-base.code", "typeVersion": 2,
            "position": [720, 300],
            "notes": "自動產生，請勿直接編輯。改 converter/*.py 後跑 "
                     "scripts/build_n8n_code_node.py 重新產生。",
        },
    ]

    def link(a, b):
        return {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}}

    connections = {}
    for a, b in [("手動觸發", "Notion：取得頁面 blocks"),
                 ("Notion：取得頁面 blocks", "補上標題與 FAQ group"),
                 ("補上標題與 FAQ group", "轉換：blocks → Elementor JSON")]:
        connections.update(link(a, b))

    return {
        "name": "Synctify — Notion→Elementor 轉換測試（唯讀）",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"synctify_note": "唯讀測試：只讀 Notion，不寫 Notion/WP。"},
        "tags": [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只檢查產物是否與來源同步，不寫檔（CI／提交前用）")
    args = ap.parse_args()

    body = build()
    if args.check:
        problems = []
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != body:
            problems.append(str(OUT))
        # workflow 只比對內嵌的程式，避免每次重生的 uuid 造成假性差異
        if WF_OUT.exists():
            embedded = json.loads(WF_OUT.read_text(encoding="utf-8"))
            code_node = [n for n in embedded["nodes"]
                         if n["type"] == "n8n-nodes-base.code"]
            if not code_node or code_node[0]["parameters"]["pythonCode"] != body:
                problems.append(str(WF_OUT))
        else:
            problems.append(str(WF_OUT))
        if problems:
            print("✗ 與 converter/ 來源不同步，請重新產生：\n  - " + "\n  - ".join(problems))
            sys.exit(1)
        print("✓ 產物與來源同步")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(f"✓ 已產生 {OUT}（{len(body.splitlines())} 行）")

    WF_OUT.write_text(json.dumps(build_workflow(body), ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(f"✓ 已產生 {WF_OUT}（可直接匯入 n8n）")


if __name__ == "__main__":
    main()
