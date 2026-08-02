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
DRAFT_WF_OUT = ROOT / "n8n" / "notion-to-wp-draft.workflow.json"
BUTTON_WF_OUT = ROOT / "n8n" / "notion-button-to-wp-draft.workflow.json"
POLL_WF_OUT = ROOT / "n8n" / "notion-poll-to-wp-draft.workflow.json"

# 測試用 Notion 頁面：Manage Exception Orders v2（已有手工轉換版本可比對）
TEST_PAGE_ID = "3822f2ede27d80f1bd47d73c6314bec4"
TEST_TITLE = "Manage Exception Orders"
TEST_FAQ_GROUP = "manage-exception-orders"

# n8n 憑證「引用」——只是識別碼，不含任何密鑰（CLAUDE.md：匯出時確認為引用而非明文）
NOTION_CRED_ID = "xfGHH7Wx4EucMC0X"
NOTION_CRED_NAME = "Support Center Sync"

# 測試站（CLAUDE.md：WP 端改動一律先在測試站驗證）
WP_BASE = "https://support.synctify.io"

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
    # image_mode：placeholder（預設）＝ 未上傳的圖換成佔位圖，人工補
    #             keep         ＝ 保留來源網址（Notion S3 預簽章，一小時後失效，僅除錯用）
    image_mode = meta["image_mode"] if "image_mode" in meta else "placeholder"

    markdown, blocks_report = blocks_to_markdown(blocks)
    template, faq_items, report = convert(markdown, title, faq_group, sync_date=sync_date)
    report["blocks"] = blocks_report

    images_todo = []
    if image_mode == "placeholder":
        # 佔位圖必須取自文章所在站台；跨站會被 CDN／WAF 擋掉而變破圖
        wp_base = meta["wp_base"] if "wp_base" in meta else ""
        images_todo = apply_placeholder_images(
            template, report, placeholder_url_for(wp_base))
    report["images_todo"] = images_todo

    return {
        "template": template,
        "faq_items": faq_items,
        "report": report,
        "markdown": markdown,
        # 方便下游 HTTP 節點直接取用
        "elementor_data": template["content"],
        "title": title,
    }


def _apply_media(payload):
    """mode=apply_media：把上傳結果回填進版面。

    上傳失敗的圖仍是會過期的 Notion S3 網址，直接寫進 WP 會在一小時內變破圖，
    因此回填後再對「仍未替換的圖」套一次佔位圖當安全網。
    """
    template = payload["template"]
    report = payload["report"] if "report" in payload else {"images": []}
    wp_base = payload["wp_base"] if "wp_base" in payload else ""

    media_map = {}
    failed = []
    for m in (payload["media"] if "media" in payload else []):
        if m.get("ok") and m.get("source_url"):
            media_map[m["source_url"]] = m
        else:
            failed.append(m)

    replaced = apply_media_map(template, media_map)
    fallback = apply_placeholder_images(template, report, placeholder_url_for(wp_base))

    return {
        "template": template,
        "elementor_data": template["content"],
        "title": payload["title"] if "title" in payload else "Untitled",
        "faq_items": payload["faq_items"] if "faq_items" in payload else [],
        "media_replaced": replaced,
        "media_failed": failed,
        "still_placeholder": fallback,
    }


_payloads = []
for _it in _items:
    _payloads.append(_it["json"])

if _payloads and "mode" in _payloads[0] and _payloads[0]["mode"] == "apply_media":
    return [{"json": _apply_media(_payloads[0])}]

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
                # ⚠️ Simplify Output 必須關閉。開啟時 n8n 會改寫 block 結構，
                # rich_text／icon／color 都取不到，導致標題、段落、清單、callout
                # 的文字全部變成空字串（表格與圖片因讀 cells/caption 反而正常，
                # 很容易誤判成只是部分內容遺失）。
                # 參數名為 simplifyOutput（取自實際匯出檔；先前猜的 simple 無效）。
                "simplifyOutput": False,
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


def build_draft_workflow(code):
    """第一階段 workflow：Notion → 轉換（圖片用佔位圖）→ WP 建草稿 → 寫入 Elementor。

    刻意不含：圖片上傳、FAQ 寫入、發佈、Notion 回寫、Switch 分路。
    這些在第一階段由人工處理，待此段穩定後再逐步加。
    """
    def nid():
        return str(uuid.uuid4())

    CONV_NODE = "轉換：blocks → Elementor JSON"

    def http(method, url, body=None):
        p = {
            "method": method,
            "url": url,
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "options": {},
        }
        if body is not None:
            p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        return p

    nodes = [
        {"parameters": {}, "id": nid(), "name": "手動觸發",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [-200, 300]},

        {"parameters": {
            "resource": "block", "operation": "getAll",
            "blockId": {"__rl": True, "value": TEST_PAGE_ID, "mode": "id"},
            "returnAll": True, "fetchNestedBlocks": True, "simplifyOutput": False,
         },
         "id": nid(), "name": "Notion：取得頁面 blocks",
         "type": "n8n-nodes-base.notion", "typeVersion": 2.2, "position": [20, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Return All 與 Also Fetch Nested Blocks 需開啟、Simplify Output 需關閉。"},

        {"parameters": {
            "assignments": {"assignments": [
                {"id": nid(), "name": "title", "value": TEST_TITLE, "type": "string"},
                {"id": nid(), "name": "faq_group", "value": TEST_FAQ_GROUP, "type": "string"},
                # 佔位圖要取自文章所在站台；跨站會被 CDN／WAF 擋掉變破圖
                {"id": nid(), "name": "wp_base", "value": WP_BASE, "type": "string"},
            ]},
            "includeOtherFields": True, "options": {},
         },
         "id": nid(), "name": "補上標題與 FAQ group",
         "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [240, 300],
         "notes": "Include Other Fields 必須開啟，否則 block 內容會被覆蓋掉。\n"
                  "換文章時改這裡的 title / faq_group，以及 Notion 節點的 Block ID。\n"
                  "wp_base 決定佔位圖從哪個站台取，需與寫入的站台一致。"},

        {"parameters": {"language": "pythonNative", "pythonCode": code},
         "id": nid(), "name": CONV_NODE,
         "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [460, 300],
         "notes": "自動產生，請勿直接編輯。改 converter/*.py 後跑 "
                  "scripts/build_n8n_code_node.py 重新產生。\n"
                  "圖片預設走 placeholder 模式（換成 Elementor 佔位圖並標「待補圖 N」）。"},

        {"parameters": http(
            "POST", f"{WP_BASE}/wp-json/wp/v2/docs",
            '={{ { "title": $json.title, "status": "draft" } }}'),
         "id": nid(), "name": "WP：建立草稿",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [680, 300],
         "notes": "建立 docs 草稿。每執行一次就會多一篇草稿——重跑前記得清掉舊的。"},

        {"parameters": http(
            # URL 內含表達式時必須以 `=` 開頭，否則 n8n 會當成字面字串，
            # {{ $json.id }} 不會被求值
            "POST", f"={WP_BASE}/wp-json/synctify/v1/elementor/{{{{ $json.id }}}}",
            '={{ { "elementor_data": $(\'' + CONV_NODE + '\').item.json.elementor_data } }}'),
         "id": nid(), "name": "WP：寫入 Elementor 版面",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [900, 300],
         "notes": "自訂端點，寫入前會自動備份最近 3 版。"},

        {"parameters": {
            "assignments": {"assignments": [
                {"id": nid(), "name": "post_id",
                 "value": "={{ $('WP：建立草稿').item.json.id }}", "type": "number"},
                {"id": nid(), "name": "編輯連結",
                 "value": "={{ '" + WP_BASE + "/wp-admin/post.php?post=' + "
                          "$('WP：建立草稿').item.json.id + '&action=elementor' }}", "type": "string"},
                {"id": nid(), "name": "待補圖",
                 "value": "={{ $('" + CONV_NODE + "').item.json.report.images_todo }}",
                 "type": "array"},
                {"id": nid(), "name": "FAQ 待人工建立",
                 "value": "={{ $('" + CONV_NODE + "').item.json.faq_items }}", "type": "array"},
            ]},
            "options": {},
         },
         "id": nid(), "name": "結果摘要",
         "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": [1120, 300],
         "notes": "給人工接手用：草稿連結、要補哪幾張圖、要手動建立的 FAQ 問答。"},
    ]

    order = ["手動觸發", "Notion：取得頁面 blocks", "補上標題與 FAQ group", CONV_NODE,
             "WP：建立草稿", "WP：寫入 Elementor 版面", "結果摘要"]
    connections = {}
    for a, b in zip(order, order[1:]):
        connections[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}

    return {
        "name": "Synctify — Notion→WP 草稿（第一階段）",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"synctify_note": "第一階段：只建草稿並寫入版面。"
                                  "圖片用佔位圖、FAQ 與發佈由人工處理。"},
        "tags": [],
    }


WEBHOOK_PATH = "synctify-draft"

# Content Hub 的 **database** ID（REST API /v1/databases/{id}/query 要的是這個）。
# ⚠️ 別跟 **collection（data source）** ID 3272f2ed-e27d-80f9-8e2d-000be0502aa8 搞混——
# 那個是 Notion 新版 API 的資料源識別碼，丟給 /v1/databases 會回
# 404 object_not_found（2026-08-02 實際踩過）。
# 已用 Notion API 確認此 ID 的 metadata type 為 database、標題為 Support Center Content Hub。
NOTION_DB_ID = "3272f2ed-e27d-807e-9fac-f2313dd2d0de"
# 觸發用的 checkbox 屬性名稱（需在 Notion 手動新增；勿與既有的 Button 屬性同名）
TRIGGER_PROP = "待同步"
POLL_MINUTES = 1


def build_polling_workflow(code):
    """輪詢版：不需 Notion Plus 方案。

    勾選 Content Hub 的「待同步」checkbox → n8n 定時查到 → 逐篇建草稿 → 取消勾選。

    設計重點：
    1. **取消勾選放在每篇的開頭**（先認領）。若放結尾，處理時間一旦超過輪詢間隔，
       下一輪會再抓到同一列而重複建草稿。
    2. **一次處理所有勾選的列**：查詢不限一筆 → Split Out 拆成多個 item →
       Loop Over Items 每次取一篇跑完整流程。Code node 會把輸入的所有 item 視為
       「同一篇的 blocks」，因此必須靠迴圈逐篇處理，不能讓多篇同時進去。
    3. 迴圈中某篇失敗會中止該輪，但**失敗那篇已被取消勾選**（不會無限重試），
       其餘仍保持勾選，下一輪接續處理。
    """
    def nid():
        return str(uuid.uuid4())

    LOOP, PICK = "逐篇處理", "取出本列資訊"
    CONV, PARAMS = "轉換：blocks → Elementor JSON", "組裝參數"
    MOTHER = "Notion：取得母列"

    def notion_http(method, url, body=None):
        p = {"method": method, "url": url,
             "authentication": "predefinedCredentialType",
             "nodeCredentialType": "notionApi",
             "sendHeaders": True,
             "headerParameters": {"parameters": [
                 {"name": "Notion-Version", "value": "2022-06-28"}]},
             "options": {}}
        if body is not None:
            p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        return p

    def wp_http(method, url, body=None):
        p = {"method": method, "url": url,
             "authentication": "genericCredentialType",
             "genericAuthType": "httpBasicAuth", "options": {}}
        if body is not None:
            p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        return p

    doc_name = f"$('{PICK}').first().json.doc_name"
    clean_title = (f"({doc_name})"
                   ".replace(/\\s+[-–]\\s*v\\d.*$/i, '')"
                   ".replace(/\\s*\\(Current\\)\\s*$/i, '').trim()")
    page_id = f"$('{PICK}').first().json.page_id"

    nodes = [
        {"parameters": {"rule": {"interval": [{"field": "minutes",
                                               "minutesInterval": POLL_MINUTES}]}},
         "id": nid(), "name": "定時檢查", "type": "n8n-nodes-base.scheduleTrigger",
         "typeVersion": 1.2, "position": [-460, 300],
         "notes": f"每 {POLL_MINUTES} 分鐘檢查一次是否有勾選「{TRIGGER_PROP}」的列。\n"
                  "手動觸發只會跑一輪；要持續自動處理需 Activate。"},

        {"parameters": notion_http(
            "POST", f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            '={{ { "filter": { "property": "' + TRIGGER_PROP + '", '
            '"checkbox": { "equals": true } }, "page_size": 100 } }}'),
         "id": nid(), "name": "查詢待同步列", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [-240, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "一次取回所有勾選的列（上限 100），下游用迴圈逐篇處理。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.results.length }}",
                            "operator": {"type": "number", "operation": "gt"},
                            "rightValue": 0}],
            "combinator": "and"}},
         "id": nid(), "name": "有待同步的列？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [-20, 300]},

        {"parameters": {}, "id": nid(), "name": "無事可做（結束）",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [200, 480]},

        {"parameters": {"fieldToSplitOut": "results", "options": {}},
         "id": nid(), "name": "拆成每列一筆", "type": "n8n-nodes-base.splitOut",
         "typeVersion": 1, "position": [200, 300],
         "notes": "把 results 陣列拆成多個 item，每個 item 是一列 Notion 頁面。"},

        {"parameters": {"batchSize": 1, "options": {}},
         "id": nid(), "name": LOOP, "type": "n8n-nodes-base.splitInBatches",
         "typeVersion": 3, "position": [420, 300],
         "notes": "每次取一篇跑完整流程，跑完再回到本節點取下一篇。\n"
                  "輸出 0 = done（全部跑完）、輸出 1 = loop（本輪要處理的那一篇）。"},

        {"parameters": {}, "id": nid(), "name": "全部完成",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [640, 140]},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "page_id",
             "value": "={{ $json.id.replace(/-/g, '') }}", "type": "string"},
            {"id": nid(), "name": "doc_name",
             "value": "={{ $json.properties['Doc name'].title[0].plain_text }}",
             "type": "string"},
            # 狀態與 WP Post ID 記在「母列」（一篇 guide 的穩定身分）。
            # 沒有 Parent item 時代表這一列本身就是母列，用自己。
            {"id": nid(), "name": "mother_id",
             "value": "={{ ($json.properties['Parent item']?.relation?.[0]?.id "
                      "?? $json.id).replace(/-/g, '') }}", "type": "string"},
        ]}, "options": {}},
         "id": nid(), "name": PICK, "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [640, 300],
         "notes": "把本輪這一列的 page id 與標題固定下來，後續節點統一引用此節點，"
                  "避免 item 數量變動後取錯對象。"},

        {"parameters": notion_http(
            "PATCH", "=https://api.notion.com/v1/pages/{{ $json.page_id }}",
            '={{ { "properties": { "' + TRIGGER_PROP + '": { "checkbox": false } } } }}'),
         "id": nid(), "name": "先取消勾選（認領）", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [860, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "⚠️ 刻意放在每篇的開頭。若移到結尾，處理時間超過輪詢間隔時，"
                  "下一輪會再抓到同一列而重複建草稿。"},

        {"parameters": {
            "resource": "block", "operation": "getAll",
            "blockId": {"__rl": True, "mode": "id", "value": "={{ " + page_id + " }}"},
            "returnAll": True, "fetchNestedBlocks": True, "simplifyOutput": False},
         "id": nid(), "name": "Notion：取得頁面 blocks",
         "type": "n8n-nodes-base.notion", "typeVersion": 2.2, "position": [1080, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Return All／Also Fetch Nested Blocks 開啟、Simplify Output 關閉。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "title", "value": "={{ " + clean_title + " }}",
             "type": "string"},
            {"id": nid(), "name": "faq_group",
             "value": "={{ (" + clean_title + ").toLowerCase()"
                      ".replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') }}",
             "type": "string"},
            {"id": nid(), "name": "wp_base", "value": WP_BASE, "type": "string"},
            # keep = 先保留 Notion 來源網址，稍後由 sideload 上傳並回填；
            # 若這裡用 placeholder，來源網址會先被換掉而無圖可上傳
            {"id": nid(), "name": "image_mode", "value": "keep", "type": "string"},
        ]}, "includeOtherFields": True, "options": {}},
         "id": nid(), "name": PARAMS, "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [1300, 300],
         "notes": "標題取自 Doc name 並去掉「- vN」「(Current)」後綴。"
                  "Include Other Fields 必須開啟。"},

        {"parameters": {"language": "pythonNative", "pythonCode": code},
         "id": nid(), "name": CONV, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1520, 300],
         "notes": "自動產生，請勿直接編輯。改 converter/*.py 後重新產生。"},

        {"parameters": wp_http(
            "POST", f"{WP_BASE}/wp-json/synctify/v1/media/sideload",
            "={{ { \"images\": $json.report.images"
            ".filter(i => i.pending_upload)"
            ".map(i => ({ url: i.url, alt: i.alt, caption: i.caption ?? i.alt })) } }}"),
         "id": nid(), "name": "WP：上傳圖片", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [1740, 300],
         "notes": "把 Notion S3 上的圖 sideload 進 WP 媒體庫。\n"
                  "來源網址一小時後失效，故必須在寫入版面前完成。沒有待上傳圖片時回空陣列。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "mode", "value": "apply_media", "type": "string"},
            {"id": nid(), "name": "template",
             "value": "={{ $('" + CONV + "').item.json.template }}", "type": "object"},
            {"id": nid(), "name": "report",
             "value": "={{ $('" + CONV + "').item.json.report }}", "type": "object"},
            {"id": nid(), "name": "faq_items",
             "value": "={{ $('" + CONV + "').item.json.faq_items }}", "type": "array"},
            {"id": nid(), "name": "title",
             "value": "={{ $('" + CONV + "').item.json.title }}", "type": "string"},
            {"id": nid(), "name": "wp_base", "value": WP_BASE, "type": "string"},
            {"id": nid(), "name": "media", "value": "={{ $json.images }}", "type": "array"},
        ]}, "options": {}},
         "id": nid(), "name": "組合回填輸入", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [1960, 300],
         "notes": "Code node（Python）只看得到自己的輸入，跨節點取值需先由此 Set 節點"
                  "用表達式匯集（Set 用的是 JS 表達式，可引用其他節點）。"},

        {"parameters": {"language": "pythonNative", "pythonCode": code},
         "id": nid(), "name": "回填媒體網址", "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [2180, 300],
         "notes": "與轉換節點同一份程式，靠 mode=apply_media 走回填分支。\n"
                  "上傳失敗的圖會退回佔位圖，避免把會過期的網址寫進 WP。"},

        {"parameters": notion_http(
            "GET", "=https://api.notion.com/v1/pages/{{ " + f"$('{PICK}').first().json.mother_id" + " }}"),
         "id": nid(), "name": MOTHER, "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [2400, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "母列存放 WP Post ID／上稿狀態——判斷要新建還是更新既有文章的依據。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.properties['WP Post ID']"
                                         "?.rich_text?.[0]?.plain_text ?? '' }}",
                            "operator": {"type": "string", "operation": "notEmpty",
                                         "singleValue": True}, "rightValue": ""}],
            "combinator": "and"}},
         "id": nid(), "name": "母列有 WP Post ID？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [2620, 300]},

        {"parameters": wp_http("GET",
            "=" + WP_BASE + "/wp-json/wp/v2/docs/{{ $json.properties['WP Post ID']"
            ".rich_text[0].plain_text }}?context=edit"),
         "id": nid(), "name": "WP：查詢既有文章", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [2840, 180],
         "notes": "取得既有文章的 status，決定要直接更新草稿還是寫成 Elementor 草稿。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "target_post_id", "value": "={{ $json.id }}",
             "type": "number"},
            # 已發佈的文章不直接覆蓋版面，改寫入 Elementor 草稿（/draft），前台不受影響
            {"id": nid(), "name": "write_path",
             "value": "={{ $json.status === 'publish' ? '/draft' : '' }}", "type": "string"},
            {"id": nid(), "name": "sync_status",
             "value": "={{ $json.status === 'publish' ? '待確認發佈' : '草稿已建立' }}",
             "type": "string"},
        ]}, "options": {}},
         "id": nid(), "name": "目標：更新既有", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [3060, 180]},

        {"parameters": wp_http("POST", f"{WP_BASE}/wp-json/wp/v2/docs",
            '={{ { "title": $(\'回填媒體網址\').item.json.title, "status": "draft" } }}'),
         "id": nid(), "name": "WP：建立新草稿", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [2840, 420],
         "notes": "母列沒有 WP Post ID → 這是第一次上稿，建立新草稿。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "target_post_id", "value": "={{ $json.id }}",
             "type": "number"},
            {"id": nid(), "name": "write_path", "value": "", "type": "string"},
            {"id": nid(), "name": "sync_status", "value": "草稿已建立", "type": "string"},
        ]}, "options": {}},
         "id": nid(), "name": "目標：新建", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [3060, 420]},

        {"parameters": wp_http(
            "POST",
            "=" + WP_BASE + "/wp-json/synctify/v1/elementor/"
            "{{ $json.target_post_id }}{{ $json.write_path }}",
            '={{ { "elementor_data": $(\'回填媒體網址\').item.json.elementor_data } }}'),
         "id": nid(), "name": "WP：寫入 Elementor 版面",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3280, 300],
         "notes": "路徑由上游決定：空＝直接寫入版面（新建／草稿）；"
                  "/draft＝已發佈文章，只寫 Elementor 草稿，前台不受影響。"},

        {"parameters": notion_http(
            "PATCH",
            "=https://api.notion.com/v1/pages/{{ " + f"$('{PICK}').first().json.mother_id" + " }}",
            '={{ { "properties": { '
            '"WP Post ID": { "rich_text": [ { "text": { "content": '
            'String($(\'WP：寫入 Elementor 版面\').item.json.post_id) } } ] }, '
            # 上稿狀態由寫入回應判斷：/draft 端點會回 autosave_id，一般端點不會。
            # 這樣不必跨分路取值（$json 此時是寫入回應，沒有上游的 sync_status）
            '"上稿狀態": { "select": { "name": '
            '$json.autosave_id ? "待確認發佈" : "草稿已建立" } }, '
            '"最後同步時間": { "date": { "start": $now.toISO() } } } } }}'),
         "id": nid(), "name": "Notion：回寫母列",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3500, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "把 WP Post ID／上稿狀態／同步時間寫回母列——下次同步靠它判斷\n"
                  "要新建還是更新。寫完回到「逐篇處理」取下一篇。"},
    ]

    conns = {
        "定時檢查": {"main": [[{"node": "查詢待同步列", "type": "main", "index": 0}]]},
        "查詢待同步列": {"main": [[{"node": "有待同步的列？", "type": "main", "index": 0}]]},
        "有待同步的列？": {"main": [
            [{"node": "拆成每列一筆", "type": "main", "index": 0}],
            [{"node": "無事可做（結束）", "type": "main", "index": 0}]]},
        "拆成每列一筆": {"main": [[{"node": LOOP, "type": "main", "index": 0}]]},
        # splitInBatches：輸出 0 = done、輸出 1 = loop
        LOOP: {"main": [
            [{"node": "全部完成", "type": "main", "index": 0}],
            [{"node": PICK, "type": "main", "index": 0}]]},
    }
    chain = [PICK, "先取消勾選（認領）", "Notion：取得頁面 blocks", PARAMS, CONV,
             "WP：上傳圖片", "組合回填輸入", "回填媒體網址", MOTHER, "母列有 WP Post ID？"]
    for a, b in zip(chain, chain[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}

    # 分路：有 Post ID → 查既有文章狀態；沒有 → 建新草稿。兩路都匯到寫入版面。
    conns["母列有 WP Post ID？"] = {"main": [
        [{"node": "WP：查詢既有文章", "type": "main", "index": 0}],
        [{"node": "WP：建立新草稿", "type": "main", "index": 0}]]}
    conns["WP：查詢既有文章"] = {"main": [[{"node": "目標：更新既有", "type": "main", "index": 0}]]}
    conns["WP：建立新草稿"] = {"main": [[{"node": "目標：新建", "type": "main", "index": 0}]]}
    for n in ("目標：更新既有", "目標：新建"):
        conns[n] = {"main": [[{"node": "WP：寫入 Elementor 版面", "type": "main", "index": 0}]]}
    conns["WP：寫入 Elementor 版面"] = {"main": [
        [{"node": "Notion：回寫母列", "type": "main", "index": 0}]]}
    # 本篇跑完 → 回到迴圈取下一篇
    conns["Notion：回寫母列"] = {"main": [[{"node": LOOP, "type": "main", "index": 0}]]}

    return {
        "name": "Synctify — Notion 勾選 → WP 草稿（輪詢）",
        "nodes": nodes, "connections": conns, "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"synctify_note":
                 f"每 {POLL_MINUTES} 分鐘輪詢；勾選「{TRIGGER_PROP}」即觸發。"
                 "一次處理所有勾選的列（迴圈逐篇），每篇開頭先取消勾選以避免重複。"
                 "不去重、不更新既有文章。"},
        "tags": [],
    }


def build_button_workflow(code):
    """由 Notion 按鈕觸發的建草稿 workflow（第一階段 + webhook 觸發）。

    與手動版的差別：page_id 由按鈕帶入、標題自 Notion 讀取（不再寫死），
    因此換文章不必改 workflow。仍只建新草稿——不去重、不更新既有文章。
    """
    def nid():
        return str(uuid.uuid4())

    WH, CONV = "Webhook：Notion 按鈕", "轉換：blocks → Elementor JSON"
    PAGE, PARAMS = "Notion：取得頁面屬性", "組裝參數"

    def notion_http(url):
        return {
            "method": "GET", "url": url,
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "notionApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Notion-Version", "value": "2022-06-28"}]},
            "options": {},
        }

    def http(method, url, body=None):
        p = {"method": method, "url": url,
             "authentication": "genericCredentialType",
             "genericAuthType": "httpBasicAuth", "options": {}}
        if body is not None:
            p.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        return p

    # 標題：去掉版本後綴（子列的 Doc name 形如「… - v2 (Current)」）
    raw_title = (f"$('{PAGE}').item.json.properties['Doc name'].title[0].plain_text")
    clean_title = (f"({raw_title})"
                   ".replace(/\\s+[-–]\\s*v\\d.*$/i, '')"
                   ".replace(/\\s*\\(Current\\)\\s*$/i, '').trim()")

    nodes = [
        {"parameters": {"httpMethod": "POST", "path": WEBHOOK_PATH,
                        "responseMode": "responseNode", "options": {}},
         "id": nid(), "name": WH, "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [-260, 300], "webhookId": nid(),
         "notes": "Notion 按鈕呼叫此網址，body 需含 page_id，header 需帶 x-synctify-token。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.headers['x-synctify-token'] }}",
                            "operator": {"type": "string", "operation": "equals"},
                            "rightValue": "={{ $env.N8N_WEBHOOK_TOKEN }}"}],
            "combinator": "and"}},
         "id": nid(), "name": "驗證 token", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [-40, 300],
         "notes": "需在 n8n 設環境變數 N8N_WEBHOOK_TOKEN。"},

        {"parameters": {"respondWith": "json",
                        "responseBody": '={{ { "error": "invalid or missing token" } }}',
                        "options": {"responseCode": 401}},
         "id": nid(), "name": "回應 401", "type": "n8n-nodes-base.respondToWebhook",
         "typeVersion": 1, "position": [180, 480]},

        # page_id 容錯擷取：Notion 按鈕的 payload 形狀依設定而異
        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "page_id",
             "value": "={{ ($json.body?.page_id ?? $json.body?.id ?? "
                      "$json.body?.data?.id ?? $json.query?.page_id ?? '')"
                      ".toString().replace(/-/g, '') }}", "type": "string"},
            {"id": nid(), "name": "raw_body", "value": "={{ $json.body }}", "type": "object"},
        ]}, "options": {}},
         "id": nid(), "name": "解析 page_id", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [180, 300],
         "notes": "同時保留 raw_body，若擷取不到 page_id 可從回應中看見 Notion 實際送了什麼。"},

        {"parameters": notion_http(
            "=https://api.notion.com/v1/pages/{{ $json.page_id }}"),
         "id": nid(), "name": PAGE, "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [400, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "讀取頁面屬性以取得 Doc name 當文章標題。"},

        {"parameters": {
            "resource": "block", "operation": "getAll",
            "blockId": {"__rl": True, "mode": "id",
                        "value": "={{ $('解析 page_id').item.json.page_id }}"},
            "returnAll": True, "fetchNestedBlocks": True, "simplifyOutput": False},
         "id": nid(), "name": "Notion：取得頁面 blocks",
         "type": "n8n-nodes-base.notion", "typeVersion": 2.2, "position": [620, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Return All／Also Fetch Nested Blocks 開啟、Simplify Output 關閉。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "title", "value": "={{ " + clean_title + " }}",
             "type": "string"},
            {"id": nid(), "name": "faq_group",
             "value": "={{ (" + clean_title + ").toLowerCase()"
                      ".replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') }}",
             "type": "string"},
            {"id": nid(), "name": "wp_base", "value": WP_BASE, "type": "string"},
        ]}, "includeOtherFields": True, "options": {}},
         "id": nid(), "name": PARAMS, "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [840, 300],
         "notes": "標題取自 Notion Doc name 並去掉「- vN」「(Current)」後綴；"
                  "faq_group 由標題轉 slug。Include Other Fields 必須開啟。"},

        {"parameters": {"language": "pythonNative", "pythonCode": code},
         "id": nid(), "name": CONV, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1060, 300],
         "notes": "自動產生，請勿直接編輯。改 converter/*.py 後重新產生。"},

        {"parameters": http("POST", f"{WP_BASE}/wp-json/wp/v2/docs",
                            '={{ { "title": $json.title, "status": "draft" } }}'),
         "id": nid(), "name": "WP：建立草稿", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [1280, 300],
         "notes": "⚠️ 每次觸發都會建立一篇新草稿——目前尚未依 WP Post ID 去重。"},

        {"parameters": http(
            "POST", f"={WP_BASE}/wp-json/synctify/v1/elementor/{{{{ $json.id }}}}",
            '={{ { "elementor_data": $(\'' + CONV + '\').item.json.elementor_data } }}'),
         "id": nid(), "name": "WP：寫入 Elementor 版面",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1500, 300]},

        {"parameters": {"respondWith": "json", "responseBody":
            '={{ { "ok": true, '
            '"post_id": $(\'WP：建立草稿\').item.json.id, '
            '"title": $(\'' + PARAMS + '\').item.json.title, '
            '"edit_url": "' + WP_BASE + '/wp-admin/post.php?post=" + '
            '$(\'WP：建立草稿\').item.json.id + "&action=elementor", '
            '"images_todo": $(\'' + CONV + '\').item.json.report.images_todo, '
            '"faq_items": $(\'' + CONV + '\').item.json.faq_items } }}',
            "options": {}},
         "id": nid(), "name": "回應成功", "type": "n8n-nodes-base.respondToWebhook",
         "typeVersion": 1, "position": [1720, 300],
         "notes": "回傳草稿連結、待補圖與 FAQ 問答，Notion 端可直接看到結果。"},
    ]

    conns = {WH: {"main": [[{"node": "驗證 token", "type": "main", "index": 0}]]},
             "驗證 token": {"main": [
                 [{"node": "解析 page_id", "type": "main", "index": 0}],
                 [{"node": "回應 401", "type": "main", "index": 0}]]}}
    chain = ["解析 page_id", PAGE, "Notion：取得頁面 blocks", PARAMS, CONV,
             "WP：建立草稿", "WP：寫入 Elementor 版面", "回應成功"]
    for a, b in zip(chain, chain[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}

    return {
        "name": "Synctify — Notion 按鈕 → WP 草稿",
        "nodes": nodes, "connections": conns, "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"synctify_note": "Notion 按鈕觸發建草稿；不去重、不更新既有文章。"},
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
    print(f"✓ 已產生 {WF_OUT}（唯讀測試）")

    DRAFT_WF_OUT.write_text(json.dumps(build_draft_workflow(body), ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"✓ 已產生 {DRAFT_WF_OUT}（第一階段：手動觸發建 WP 草稿）")

    BUTTON_WF_OUT.write_text(json.dumps(build_button_workflow(body), ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"✓ 已產生 {BUTTON_WF_OUT}（Notion 按鈕觸發，需 Plus 方案）")

    POLL_WF_OUT.write_text(json.dumps(build_polling_workflow(body), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"✓ 已產生 {POLL_WF_OUT}（Notion 勾選觸發，輪詢）")


if __name__ == "__main__":
    main()
