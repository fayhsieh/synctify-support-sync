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
PUBCB_WF_OUT = ROOT / "n8n" / "wp-publish-callback.workflow.json"
SYNC_WF_OUT = ROOT / "n8n" / "notion-sync-to-wp.workflow.json"
# 舊檔名（曾經含輪詢）。輪詢移除後名稱誤導，改名並主動刪除舊檔——
# 殘留的過期 workflow 被誤匯入過一次，代價很高。
# 已被取代的產物。建置時主動刪除——殘留的過期 workflow 被誤匯入過一次，
# 而且很難從畫面上看出它落後了幾個節點。
STALE_WF_OUTS = [ROOT / "n8n" / f for f in (
    "notion-poll-to-wp-draft.workflow.json",    # 改名前的主流程（曾含輪詢）
    "notion-button-to-wp-draft.workflow.json",  # 併入主流程前的獨立按鈕版
    "notion-to-elementor-test.workflow.json",   # 開發期的唯讀轉換測試
    "notion-to-wp-draft.workflow.json",         # 第一階段：手動觸發、WP 寫入 mock
    "sync-to-wp.workflow.json",                 # 最初的手工骨架，webhook 無認證
)]

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


def _hrefs_in(template):
    """從產出的 Elementor JSON 撈出所有 href——這是真正寫進 WP 的東西。"""
    found = []
    for _c in template["content"]:
        for _w in _c["elements"]:
            _st = _w["settings"] if "settings" in _w else {}
            for _k in _st:
                _v = _st[_k]
                if isinstance(_v, str):
                    found.extend(re.findall(r'href="([^"]+)"', _v))
                elif isinstance(_v, list):
                    for _item in _v:
                        if isinstance(_item, dict) and "text" in _item:
                            found.extend(re.findall(r'href="([^"]+)"', str(_item["text"])))
    return found


def _run(blocks, meta):
    title = meta["title"] if "title" in meta else "Untitled"
    faq_group = meta["faq_group"] if "faq_group" in meta else "untitled"
    sync_date = meta["sync_date"] if "sync_date" in meta else None
    # image_mode：placeholder（預設）＝ 未上傳的圖換成佔位圖，人工補
    #             keep         ＝ 保留來源網址（Notion S3 預簽章，一小時後失效，僅除錯用）
    image_mode = meta["image_mode"] if "image_mode" in meta else "placeholder"

    markdown, blocks_report = blocks_to_markdown(blocks)
    # Notion 內部連結 → WP 永久連結。對照表由上游兩個節點提供；沒給就跳過解析，
    # 行為與加這個功能之前一致。
    _link_map = build_link_map(meta["hub_rows"] if "hub_rows" in meta else [],
                               meta["wp_docs"] if "wp_docs" in meta else [])
    template, faq_items, report = convert(markdown, title, faq_group,
                                          sync_date=sync_date, link_map=_link_map)
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
        # SEO Meta 段不進正文，改寫進 AIOSEO（POST /synctify/v1/seo/{id}）
        "seo": blocks_report["seo"],
        # 換不掉的 Notion 連結——寫作端要修的內容問題，往上帶方便回報
        "unresolved_notion_links": report["unresolved_notion_links"],
        # 診斷用：連結沒被換掉時，一眼看出是對照表沒進來還是查不到這一篇
        "link_map_size": len(_link_map),
        "link_inputs": {"hub_rows": len(meta["hub_rows"]) if "hub_rows" in meta else 0,
                        "wp_docs": len(meta["wp_docs"]) if "wp_docs" in meta else 0},
        # links_seen  = 中介 markdown 裡的連結（**解析前**）
        # links_written = 最終 Elementor JSON 裡的連結（**解析後**）
        # 兩者一比就知道解析有沒有發生，不必再猜。
        # 每個連結逐一說明：原始網址、解出的 page_id、對照表裡有沒有這一筆。
        # 這樣一欄就能分辨「認不出是 Notion 連結」與「認得出但查不到」。
        "links_seen": [{"url": _u,
                        "page_id": notion_page_id_from_url(_u),
                        "in_map": notion_page_id_from_url(_u) in _link_map}
                       for _u in re.findall(r"\\]\\(([^)]+)\\)", markdown)],
        # 最終寫進 WP 的連結（解析後）——與上面一比就知道解析有沒有發生
        "links_written": _hrefs_in(template),
        # 對照表的前幾個 key，用來確認鍵值格式是否如預期
        "link_map_keys_sample": list(_link_map)[:3],
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

# mode=version_marks：算出「vN 成為現行版本」後，母列與子列要改哪些字
if _payloads and "mode" in _payloads[0] and _payloads[0]["mode"] == "version_marks":
    _p = _payloads[0]
    _plan = plan_version_marks(_p["rows"] if "rows" in _p else [],
                               _p["blocks"] if "blocks" in _p else [],
                               _p["version"] if "version" in _p else "")
    # 兩個清單都可能是空的（已經是正確狀態）——下游用 splitOut 會自然跳過
    return [{"json": {"row_renames": _plan["row_renames"],
                      "block_updates": _plan["block_updates"],
                      "version": short_version(_p["version"] if "version" in _p else ""),
                      "nothing_to_do": (not _plan["row_renames"]
                                        and not _plan["block_updates"])}}]

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


# Content Hub 的 **database** ID（REST API /v1/databases/{id}/query 要的是這個）。
# ⚠️ 別跟 **collection（data source）** ID 3272f2ed-e27d-80f9-8e2d-000be0502aa8 搞混——
# 那個是 Notion 新版 API 的資料源識別碼，丟給 /v1/databases 會回
# 404 object_not_found（2026-08-02 實際踩過）。
# 已用 Notion API 確認此 ID 的 metadata type 為 database、標題為 Support Center Content Hub。
NOTION_DB_ID = "3272f2ed-e27d-807e-9fac-f2313dd2d0de"
# 勾選輪詢用的 checkbox 屬性與間隔。POLLING="removed" 時不會被引用，
# 但把 POLLING 改回 "active" 就需要——一起留著才救得回來。
TRIGGER_PROP = "待同步"
POLL_MINUTES = 10

# Notion 按鈕 webhook 的路徑。**這裡刻意只放佔位字串**——實際路徑就是這條 webhook
# 的唯一憑證，屬於 CLAUDE.md 明列不可入庫的東西。匯入 n8n 後手動改成隨機字串，
# 不要寫回這個檔案。
# 2026-08-11 實測：automation.internal.synctify.net 雖名為 internal，公開 DNS 解析得到，
# /webhook/* 亦正常回應（未註冊路徑回 404，0.58s），故 Notion 打得到。
WEBHOOK_PATH = "synctify-sync-CHANGE-ME-TO-A-RANDOM-STRING"

# webhook 的授權：Notion 按鈕的 Send webhook 支援 Add custom header，所以能做真正的
# header 驗證，而不是只靠「網址猜不到」（網址會滲進 proxy log、瀏覽器紀錄）。
# 密鑰存在 n8n 的 Header Auth 憑證裡，workflow JSON 只留引用，不入庫。
WEBHOOK_AUTH_CRED_NAME = "Synctify Notion Button"
# WP 發佈回呼的 webhook（觸發者是 WordPress 外掛，不是 Notion）
PUBLISH_WEBHOOK_PATH = "synctify-published-CHANGE-ME-TO-A-RANDOM-STRING"

# 輪詢的去留（Fay 2026-08-11：更新頻率不高，按鈕通了就不需要一直空掃）
#   "active"   定時觸發啟用
#   "standby"  節點保留但觸發器停用 —— 不會空掃，按鈕出事時 UI 上一鍵可救回
#   "removed"  輪詢節點整組移除
# 按鈕實測通過後改成 "removed" 重新產生即可，不必動其他任何地方。
POLLING = "removed"
# 輪詢移除時要一併拿掉的節點。「先取消勾選（認領）」也在內——它的唯一作用是
# 認領：處理時間若超過輪詢間隔，下一輪會再抓到同一列而重複建草稿。按鈕觸發
# 一次只送一列，沒有這個問題；而且 Fay 移除輪詢後把「待同步」欄位也從 Notion
# 刪了，留著這個節點會直接以「待同步 is not a property that exists」失敗
# （2026-08-11 實測踩到）。
POLL_NODE_NAMES = ("定時檢查", "查詢待同步列", "有待同步的列？",
                   "無事可做（結束）", "拆成每列一筆", "先取消勾選（認領）")


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
                   ".replace(/\\s*\\(Current\\)\\s*$/i, '')"
                   # Notion 的管理編號前綴（4-10、5-2、10-1…）只用於 Notion 內部排序，
                   # 不可進站上標題——它同時會被 WP 拿去生 slug（/4-10-bigcommerce-…）。
                   ".replace(/^\\s*\\d+[-–]\\d+[.\\s]\\s*/, '')"
                   ".trim()")
    page_id = f"$('{PICK}').first().json.page_id"

    # Notion 按鈕（Plus 方案）與定時輪詢共用同一條處理鏈。
    # ⚠️ 刻意不另開一份 workflow：先前分家的版本落後了 14 個節點，
    #    兩份各自演化只會讓修正漏掉其中一邊。
    button_nodes = [
        {"parameters": {
            "httpMethod": "POST",
            "path": WEBHOOK_PATH,
            "responseMode": "onReceived",
            "authentication": "headerAuth",
            "options": {}},
         "id": nid(), "name": "Notion 按鈕（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [-460, 20], "webhookId": nid(),
         "credentials": {"httpHeaderAuth": {"id": "", "name": WEBHOOK_AUTH_CRED_NAME}},
         "notes": "Notion Content Hub 的「同步到 WP」按鈕 → Send webhook。\n"
                  "\n"
                  "【匯入後要做兩件事】\n"
                  "1. Path 換成一組隨機字串（目前是佔位字串）。\n"
                  "2. 建立 Header Auth 憑證「" + WEBHOOK_AUTH_CRED_NAME + "」，\n"
                  "   自訂 header 名稱與一組長隨機值；在 Notion 按鈕的 Send webhook\n"
                  "   用「Add custom header」填同一組。\n"
                  "   兩者都不可寫回 repo（CLAUDE.md：webhook token 不入庫）。\n"
                  "\n"
                  "為何要 header 而不是只靠猜不到的網址：網址會滲進 proxy log、\n"
                  "瀏覽器紀錄與轉寄的訊息裡，header 才是真正可輪替的憑證。\n"
                  "\n"
                  "responseMode=onReceived：立刻回 200，不讓 Notion 等整條流程跑完。\n"
                  "按鈕請放在「版本子列」上：母列沒有內容區塊，按了會轉出空文章。"},

        {"parameters": {"assignments": {"assignments": [
            # Notion 按鈕 webhook 的實際 payload 結構請以第一次執行的 log 為準；
            # 這裡把幾種可能的位置都試一遍，順便支援 ?page_id= 手動測試。
            {"id": nid(), "name": "page_id",
             "value": "={{ ($json.body?.data?.id ?? $json.body?.page?.id ?? "
                      "$json.body?.id ?? $json.query?.page_id ?? '')"
                      ".toString().replace(/-/g, '') }}",
             "type": "string"},
        ]}, "options": {}},
         "id": nid(), "name": "解析 page_id", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [-240, 20],
         "notes": "從 webhook payload 取出頁面 id。Notion 的 payload 結構若與預期\n"
                  "不同，這裡會取到空字串，由下一個節點擋下並留下痕跡。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.page_id }}",
                            "operator": {"type": "string", "operation": "notEmpty"}}],
            "combinator": "and"}},
         "id": nid(), "name": "取得到 page_id？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [-20, 20]},

        {"parameters": {}, "id": nid(), "name": "payload 無 page_id（結束）",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [200, -100],
         "notes": "走到這裡代表 webhook payload 的結構跟預期不符。\n"
                  "打開這次執行的「Notion 按鈕（Webhook）」節點輸出，\n"
                  "照實際結構修正「解析 page_id」的取值路徑即可。"},

        {"parameters": notion_http(
            "GET", "=https://api.notion.com/v1/pages/{{ $json.page_id }}"),
         "id": nid(), "name": "Notion：取得該列", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [200, 20],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "取回完整的頁面物件，讓後續節點看到的形狀與輪詢分支完全一致\n"
                  "（同樣有 id／properties），因此兩條觸發路徑共用同一條鏈。"},
    ]

    nodes = button_nodes + [
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
            # Notion Category（如 "5. Orders"）→ WP 上 Synctify Documentation 底下的
            # 同名分類頁。序號前綴由 /doc/defaults 端點負責剝除。
            {"id": nid(), "name": "category",
             "value": "={{ $json.properties['Category']?.select?.name ?? '' }}",
             "type": "string"},
            # 結構是三層：母列 → 版本子列 → (Draft) 草稿層。只有中間那層可以同步。
            # 沒有 Parent item ＝ 最上層母列（沒有內容區塊，同步會轉出空文章）。
            {"id": nid(), "name": "is_mother",
             "value": "={{ !($json.properties['Parent item']?.relation?.length) }}",
             "type": "boolean"},
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
         "type": "n8n-nodes-base.notion", "typeVersion": 2.2, "position": [1440, 300],
         "executeOnce": True,
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Return All／Also Fetch Nested Blocks 開啟、Simplify Output 關閉。\n"
                  "\n"
                  "⚠️ Execute Once 必須開啟：上游「WP：取得文章網址」回傳陣列，\n"
                  "n8n 會拆成一個文章一個項目，不設就會被執行二十幾次。\n"
                  "本節點的 blockId 用顯式節點引用，與輸入項目無關，跑一次即可。"},

        {"parameters": notion_http(
            "POST", f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            '={{ { "page_size": 100 } }}'),
         "id": nid(), "name": "Notion：取得連結對照",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1080, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "撈整個 Content Hub，用來把文章裡的 Notion 內部連結換成 WP 永久連結。\n"
                  "寫作端引用其他文章時會貼 Notion 連結，那對讀者是打不開的私有網址。\n"
                  "\n"
                  "⚠️ page_size 上限 100。Hub 超過 100 列時要改成分頁抓取，\n"
                  "否則排在後面的文章會解析不到（目前約 90 列）。"},

        {"parameters": wp_http("GET",
            "=" + WP_BASE + "/wp-json/wp/v2/docs?per_page=100&_fields=id,link,title"
            "&status=publish,draft&include="
            "{{ $json.results.map(r => r.properties['WP Post ID']?.rich_text?.[0]"
            "?.plain_text).filter(Boolean).join(',') || '0' }}"),
         "id": nid(), "name": "WP：取得文章網址", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [1260, 300],
         "notes": "只取上一步撈到的 WP Post ID 對應的文章，不是整個 docs（站上有 179 篇，\n"
                  "含其他佈景主題的示範內容）。永久連結含分類路徑，拼不出來只能查。\n"
                  "沒有任何 id 時用 include=0 讓它回空陣列——留空會變成回傳全部。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "title", "value": "={{ " + clean_title + " }}",
             "type": "string"},
            # .item 會對應到「當前這一筆」，但這兩個節點的輸出與區塊項目沒有一對一
            # 關係，必須用 .first() / .all() 取整批。WP 那支回傳陣列會被 n8n 拆成
            # 多個項目，所以要 .all().map() 收回成陣列（2026-08-11 實測踩到：
            # 直接用 .item.json 會拿到單一物件而型別驗證失敗）。
            {"id": nid(), "name": "hub_rows", "type": "array",
             "value": "={{ $('Notion：取得連結對照').first().json.results }}"},
            {"id": nid(), "name": "wp_docs", "type": "array",
             "value": "={{ $('WP：取得文章網址').all().map(i => i.json) }}"},
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

        # ── 防呆①：按到最上層母列
        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            # ⚠️ 必須顯式引用 PICK，不能用 $json——上游是「先取消勾選」那個 PATCH 節點，
            # 此時 $json 是 Notion 的回應物件，沒有 is_mother，判斷會永遠落在 false，
            # 防呆等於形同虛設（2026-08-11 實測第一次按鈕時發現）。
            "conditions": [{"id": nid(),
                            "leftValue": "={{ " + f"$('{PICK}').first().json.is_mother" + " }}",
                            "operator": {"type": "boolean", "operation": "true",
                                         "singleValue": True}, "rightValue": ""}],
            "combinator": "and"}},
         "id": nid(), "name": "是母列？（誤按防呆）", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [1080, 460],
         "notes": "按鈕是資料庫欄位，每一列都有，母列上藏不掉——只能在這裡擋。\n"
                  "母列沒有內容區塊，放行的話會轉出一篇空文章蓋掉正式內容。\n"
                  "輸出 true＝是母列（拒絕）／false＝版本子列（繼續）。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "fail_reason", "type": "string",
             "value": "這是最上層母列，沒有內容區塊。請改按版本子列（帶 - vN 的那列）。"},
        ]}, "options": {}},
         "id": nid(), "name": "原因：按到母列", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [1300, 560]},

        # ── 防呆②：按到第三層（老闆的 (Draft) 草稿）
        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.properties['Parent item']"
                                         "?.relation?.length ?? 0 }}",
                            "operator": {"type": "number", "operation": "gt"},
                            "rightValue": 0}],
            "combinator": "and"}},
         "id": nid(), "name": "母列自己還有上層？（草稿層防呆）",
         "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [2620, 560],
         "notes": "5-1／5-3／5-4 底下藏著早期沒有 spec 時做的 (Draft) 草稿（深度 3）。\n"
                  "若「母列」自己還有 Parent item，代表按到的是草稿層，一律不同步。\n"
                  "用深度判斷而非命名或 Status——結構訊號不依賴命名紀律。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "fail_reason", "type": "string",
             "value": "這是 (Draft) 草稿層（深度 3），不會同步到站上。"
                      "請改按正式的版本子列。"},
        ]}, "options": {}},
         "id": nid(), "name": "原因：按到草稿層", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [2840, 660]},

        # ── 兩條拒絕路徑共用的回報
        {"parameters": notion_http(
            "PATCH", "=https://api.notion.com/v1/pages/{{ " + f"$('{PICK}').first().json.page_id" + " }}",
            '={{ { "properties": { "上稿狀態": { "select": { "name": "❌ 同步失敗" } } } } }}'),
         "id": nid(), "name": "回寫：同步失敗", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [3060, 620],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "寫在「被按下的那一列」而不是母列——使用者在哪裡按就在哪裡看到結果。"},

        {"parameters": notion_http(
            "POST", "https://api.notion.com/v1/comments",
            '={{ { "parent": { "page_id": ' + f"$('{PICK}').first().json.page_id" + ' }, '
            '"rich_text": [ { "text": { "content": "⚠️ 同步已中止：" + $json.fail_reason } } ] } }}'),
         "id": nid(), "name": "Notion：留言說明原因", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [3280, 620],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "按鈕觸發時沒人在看 n8n，所以把原因留言回 Notion 頁面上。\n"
                  "留言比 select 值能承載更多資訊，使用者當場就知道該怎麼做。"},

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
         "typeVersion": 4.2, "position": [2840, 100],
         "notes": "取得既有文章的 status，決定要直接更新草稿還是寫成 Elementor 草稿。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(), "leftValue": "={{ $json.status }}",
                            "operator": {"type": "string", "operation": "equals"},
                            "rightValue": "trash"}],
            "combinator": "and"}},
         "id": nid(), "name": "既有文章在垃圾桶？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [3060, 100],
         "notes": "母列的 WP Post ID 可能指向已被刪除的文章（2026-08-11 實測踩到：\n"
                  "7553 被丟進垃圾桶，流程照樣往它寫，寫進去也沒人看得到）。\n"
                  "垃圾桶 → 改走新建分路；回寫母列時會把新的 Post ID 蓋上去，自動修好。"},

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

        {"parameters": wp_http(
            "POST",
            "=" + WP_BASE + "/wp-json/synctify/v1/doc/defaults/"
            "{{ $('WP：寫入 Elementor 版面').item.json.post_id }}",
            '={{ { "category": ' + f"$('{PICK}').first().json.category" + ', '
            # 把 Notion 母列 id 存進文章 meta：WP 端按下發佈時，外掛靠它知道要回寫哪一列
            '"notion_page_id": ' + f"$('{PICK}').first().json.mother_id" + ', '
            '"notion_row_id": ' + f"$('{PICK}').first().json.page_id" + ', '
            '"allow_published": true } }}'),
         "id": nid(), "name": "WP：套用站方預設欄位",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3500, 300],
         "notes": "封面照 opengraph／作者 The Synctify Team／討論 closed／\n"
                  "Parent 依 Notion Category 對到分類頁。兩條分路都會經過。\n"
                  "allow_published=true（Fay 2026-08-02 決定）：既有已發佈文章也直接\n"
                  "校正這四項，讓站上欄位始終以 Notion 為準。回應的 diff 會列出改了什麼。\n"
                  "分類在站上找不到會回 422 並附可用清單——刻意不靜默留在根目錄。"},

        {"parameters": wp_http(
            "POST",
            "=" + WP_BASE + "/wp-json/synctify/v1/seo/"
            "{{ $('WP：寫入 Elementor 版面').item.json.post_id }}",
            # 用 ?. ——文章若沒寫 SEO Meta 段，seo 是空物件，兩個欄位皆 undefined，
            # 序列化後直接消失，端點會視為「未指定」而保留站上現值。
            '={{ { "title": ' + f"$('{CONV}').item.json.seo?.title" + ', '
            '"description": ' + f"$('{CONV}').item.json.seo?.description" + ', '
            '"allow_published": true } }}'),
         "id": nid(), "name": "WP：寫入 SEO meta",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3720, 300],
         "notes": "取自 Notion 文末的 SEO Meta 段（不進正文）。\n"
                  "AIOSEO meta 沒有草稿機制，寫下去即線上生效。\n"
                  "allow_published=true（Fay 2026-08-02 決定）：既有已發佈文章也直接寫，\n"
                  "理由是 SEO 文案與內文出自同一份已審核的 Notion 來源。\n"
                  "回應的 previous 保留改動前的值，需要時可據以還原。\n"
                  "\n"
                  "端點預設保護 title：站上現值若是 AIOSEO 智慧標籤模板\n"
                  "（如 #post_title: Requests & Labels #separator_sa #site_title）\n"
                  "則跳過不覆蓋，並在回應的 skipped_smart_tags 列出；description\n"
                  "一律以 Notion 為準。要全部照寫可傳 preserve_smart_tags: []。"},

        {"parameters": wp_http(
            "POST", f"{WP_BASE}/wp-json/synctify/v1/faq/sync",
            '={{ { "group": ' + f"$('{PARAMS}').first().json.faq_group" + ', '
            '"items": ' + f"$('{CONV}').first().json.faq_items" + ' } }}'),
         "id": nid(), "name": "WP：同步 FAQ",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3830, 300],
         "notes": "FAQ 段落（## FAQs／## Troubleshooting 底下的問答）寫進 Arconix FAQ，\n"
                  "group 分類詞＝文章 slug，與頁面上的 [faq group=\"…\"] shortcode 對應。\n"
                  "\n"
                  "FAQ 是獨立文章，沒辦法像內文那樣放進 Elementor 草稿暫存，所以對已發佈\n"
                  "文章會立刻反映到前台（Fay 2026-08-11 決定，與 SEO meta 一致：\n"
                  "Notion 是單一真實來源，直接校正）。\n"
                  "\n"
                  "端點以標題比對，人工建立的既有題目會被認領而非重複建立；\n"
                  "移除只動管過的且僅移到垃圾桶；items 為空時刻意不清除\n"
                  "（那比較像 FAQ 段落沒被解析出來，而非真的要刪光）。\n"
                  "回應的 created／updated／adopted／trashed／orphans 可核對結果。"},

        {"parameters": notion_http(
            "PATCH",
            "=https://api.notion.com/v1/pages/{{ " + f"$('{PICK}').first().json.mother_id" + " }}",
            '={{ { "properties": { '
            '"WP Post ID": { "rich_text": [ { "text": { "content": '
            'String($(\'WP：寫入 Elementor 版面\').item.json.post_id) } } ] }, '
            # 同步成功一律「草稿已建立」（Fay 2026-08-11 決定）。原本會依 autosave_id
            # 分寫「待確認發佈」，但實務上兩者對小編是同一件事：看到這個狀態就代表
            # 同步成功、可以去 WP 處理。「已發佈」改由外掛在 WP 端按下發佈時回呼寫入。
            '"上稿狀態": { "select": { "name": "草稿已建立" } }, '
            '"最後同步時間": { "date": { "start": $now.toISO() } } } } }}'),
         "id": nid(), "name": "Notion：回寫母列",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3940, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "把 WP Post ID／上稿狀態／同步時間寫回母列——下次同步靠它判斷\n"
                  "要新建還是更新。WP Post ID 只記在母列（它是整篇文章的穩定身分，\n"
                  "跨版本不變），版本子列不記。"},

        {"parameters": notion_http(
            "PATCH",
            "=https://api.notion.com/v1/pages/{{ " + f"$('{PICK}').first().json.page_id" + " }}",
            '={{ { "properties": { '
            '"上稿狀態": { "select": { "name": "草稿已建立" } }, '
            '"最後同步時間": { "date": { "start": $now.toISO() } } } } }}'),
         "id": nid(), "name": "Notion：回寫子列",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [4160, 300],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "同樣的狀態也寫回「被按下的那一列」。\n"
                  "理由是一致性：失敗時本來就寫在按下的那列，成功卻只寫母列的話，\n"
                  "使用者在自己按的地方看不到任何回應（2026-08-11 Fay 回報）。\n"
                  "\n"
                  "兩邊各有用途：母列是整篇文章的彙總與下次同步的依據；\n"
                  "子列是「這個版本何時被同步過」的紀錄，舊版本保留舊時間是正確的。"},
    ]

    conns = {
        # 按鈕分支：webhook → 解析 → 取回頁面 → 併入共用迴圈
        "Notion 按鈕（Webhook）": {"main": [[{"node": "解析 page_id", "type": "main", "index": 0}]]},
        "解析 page_id": {"main": [[{"node": "取得到 page_id？", "type": "main", "index": 0}]]},
        "取得到 page_id？": {"main": [
            [{"node": "Notion：取得該列", "type": "main", "index": 0}],
            [{"node": "payload 無 page_id（結束）", "type": "main", "index": 0}]]},
        "Notion：取得該列": {"main": [[{"node": LOOP, "type": "main", "index": 0}]]},

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
    # 防呆①刻意排在「先取消勾選」之後：先認領再檢查，否則被拒絕的列會一直留著勾，
    # 輪詢每一輪都重抓同一列。輪詢移除時整個認領節點也不存在，直接接防呆。
    claim = [] if POLLING == "removed" else ["先取消勾選（認領）"]
    chain = [PICK] + claim + ["是母列？（誤按防呆）"]
    chain2 = ["Notion：取得連結對照", "WP：取得文章網址", "Notion：取得頁面 blocks",
              PARAMS, CONV,
              "WP：上傳圖片", "組合回填輸入", "回填媒體網址", MOTHER,
              "母列自己還有上層？（草稿層防呆）"]
    for a, b in zip(chain, chain[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    for a, b in zip(chain2, chain2[1:]):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}

    # IF 的輸出 0＝true（命中＝要拒絕）、輸出 1＝false（放行）
    conns["是母列？（誤按防呆）"] = {"main": [
        [{"node": "原因：按到母列", "type": "main", "index": 0}],
        [{"node": chain2[0], "type": "main", "index": 0}]]}
    conns["母列自己還有上層？（草稿層防呆）"] = {"main": [
        [{"node": "原因：按到草稿層", "type": "main", "index": 0}],
        [{"node": "母列有 WP Post ID？", "type": "main", "index": 0}]]}

    # 兩條拒絕路徑匯流 → 回寫失敗 → 留言 → 回到迴圈取下一篇
    for n in ("原因：按到母列", "原因：按到草稿層"):
        conns[n] = {"main": [[{"node": "回寫：同步失敗", "type": "main", "index": 0}]]}
    conns["回寫：同步失敗"] = {"main": [
        [{"node": "Notion：留言說明原因", "type": "main", "index": 0}]]}
    conns["Notion：留言說明原因"] = {"main": [[{"node": LOOP, "type": "main", "index": 0}]]}

    # 分路：有 Post ID → 查既有文章狀態；沒有 → 建新草稿。兩路都匯到寫入版面。
    conns["母列有 WP Post ID？"] = {"main": [
        [{"node": "WP：查詢既有文章", "type": "main", "index": 0}],
        [{"node": "WP：建立新草稿", "type": "main", "index": 0}]]}
    conns["WP：查詢既有文章"] = {"main": [
        [{"node": "既有文章在垃圾桶？", "type": "main", "index": 0}]]}
    # true＝在垃圾桶 → 當作全新文章重建；false＝正常 → 更新既有
    conns["既有文章在垃圾桶？"] = {"main": [
        [{"node": "WP：建立新草稿", "type": "main", "index": 0}],
        [{"node": "目標：更新既有", "type": "main", "index": 0}]]}
    conns["WP：建立新草稿"] = {"main": [[{"node": "目標：新建", "type": "main", "index": 0}]]}
    for n in ("目標：更新既有", "目標：新建"):
        conns[n] = {"main": [[{"node": "WP：寫入 Elementor 版面", "type": "main", "index": 0}]]}
    # 寫完版面 → 套站方預設欄位 → 寫 SEO meta → 回寫母列
    for a, b in (("WP：寫入 Elementor 版面", "WP：套用站方預設欄位"),
                 ("WP：套用站方預設欄位", "WP：寫入 SEO meta"),
                 ("WP：寫入 SEO meta", "WP：同步 FAQ"),
                 ("WP：同步 FAQ", "Notion：回寫母列"),
                 ("Notion：回寫母列", "Notion：回寫子列")):
        conns[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
    # 本篇跑完 → 回到迴圈取下一篇
    conns["Notion：回寫子列"] = {"main": [[{"node": LOOP, "type": "main", "index": 0}]]}

    # ── 輪詢的去留：standby＝保留節點但停用觸發器（不空掃、可一鍵救回）
    #                removed ＝整組拿掉
    if POLLING == "standby":
        for n in nodes:
            if n["name"] == "定時檢查":
                n["disabled"] = True
                n["notes"] = ("⏸ 已停用（Fay 2026-08-11 決定）。更新頻率不高，"
                              "改由 Notion 按鈕觸發，不需要每分鐘空掃 Notion。\n\n"
                              "按鈕若出問題，把這個節點重新啟用即可恢復批次同步——"
                              "底下整條鏈是共用的，不必改任何設定。")
    elif POLLING == "removed":
        drop = set(POLL_NODE_NAMES)
        nodes = [n for n in nodes if n["name"] not in drop]
        conns = {src: c for src, c in conns.items() if src not in drop}
        for c in conns.values():
            c["main"] = [[l for l in out if l["node"] not in drop] for out in c["main"]]

    name = {"active":  "Synctify — Notion → WP 草稿（按鈕 ＋ 勾選輪詢）",
            "standby": "Synctify — Notion → WP 草稿（按鈕觸發；輪詢待命）",
            "removed": "Synctify — Notion → WP 草稿（按鈕觸發）"}[POLLING]

    return {
        "name": name,
        "nodes": nodes, "connections": conns, "active": False,
        "settings": {"executionOrder": "v1"},
        "meta": {"synctify_note":
                 f"每 {POLL_MINUTES} 分鐘輪詢；勾選「{TRIGGER_PROP}」即觸發。"
                 "一次處理所有勾選的列（迴圈逐篇），每篇開頭先取消勾選以避免重複。"
                 "不去重、不更新既有文章。"},
        "tags": [],
    }


def build_publish_callback_workflow(code):
    """WP 按下發佈 → 外掛打這個 webhook → 把 Notion 母列標成「已發佈」。

    刻意獨立成一條極短的 workflow：它的觸發者是 WordPress 而非 Notion，
    生命週期與上稿流程無關，混在同一條裡只會讓那張圖更難讀。

    外掛送的 body：{ event, post_id, notion_page_id, permalink }
      event = published                （草稿 → 發佈，新文章）
            | elementor_draft_applied  （既有已發佈文章套用了 Elementor 草稿）
    """
    _n = [0]
    def nid():
        _n[0] += 1
        return f"pub{_n[0]:03d}"

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

    nodes = [
        {"parameters": {
            "httpMethod": "POST",
            "path": PUBLISH_WEBHOOK_PATH,
            "responseMode": "onReceived",
            "authentication": "headerAuth",
            "options": {}},
         "id": nid(), "name": "WP 發佈回呼（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [-300, 300], "webhookId": nid(),
         "credentials": {"httpHeaderAuth": {"id": "", "name": WEBHOOK_AUTH_CRED_NAME}},
         "notes": "由輔助外掛（0.2.0+）觸發，不是 Notion 按鈕。\n"
                  "\n"
                  "WP 端要在 wp-config.php 定義三個常數：\n"
                  "  SYNCTIFY_PUBLISH_WEBHOOK_URL    ← 本節點的 Production URL\n"
                  "  SYNCTIFY_PUBLISH_WEBHOOK_HEADER ← 與憑證同名的 header\n"
                  "  SYNCTIFY_PUBLISH_WEBHOOK_SECRET ← 與憑證同值\n"
                  "未定義時外掛靜默停用回呼，其他功能不受影響。\n"
                  "\n"
                  "憑證沿用「" + WEBHOOK_AUTH_CRED_NAME + "」——同一批人、同一個信任邊界，\n"
                  "分開兩組密鑰只是多一份要輪替的東西，換不到實質隔離。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": nid(),
                            "leftValue": "={{ $json.body?.notion_page_id ?? '' }}",
                            "operator": {"type": "string", "operation": "notEmpty",
                                         "singleValue": True}, "rightValue": ""}],
            "combinator": "and"}},
         "id": nid(), "name": "有帶 Notion 母列 id？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [-80, 300],
         "notes": "非同步流程建立的文章沒有這個 meta，外掛本來就不會送；\n"
                  "這裡再擋一次，避免把空 id 打進 Notion API。"},

        {"parameters": {}, "id": nid(), "name": "略過（非同步文章）",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [140, 440]},

        {"parameters": {
            "method": "PATCH",
            "url": "=https://api.notion.com/v1/pages/{{ $json.body.notion_page_id }}",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "notionApi",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": '={{ { "properties": { '
                        '"上稿狀態": { "select": { "name": "已發佈" } }, '
                        '"最後同步時間": { "date": { "start": $now.toISO() } } } } }}',
            "options": {}},
         "id": nid(), "name": "Notion：標記已發佈",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [140, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "兩種 event 都寫「已發佈」：\n"
                  "  published                → 新文章從草稿發佈\n"
                  "  elementor_draft_applied  → 既有已發佈文章套用了 Elementor 草稿\n"
                  "對小編而言兩者是同一件事：站上內容已經是最新的。"},
    ]

    nodes.append(
        {"parameters": {
            "method": "PATCH",
            "url": "=https://api.notion.com/v1/pages/"
                   "{{ $('WP 發佈回呼（Webhook）').item.json.body.notion_row_id }}",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "notionApi",
            "sendBody": True, "specifyBody": "json",
            # Status 一併轉成 Existing：內容已經上線，不再是 Content Approved
            # 的待上稿狀態（Fay 2026-08-11）。Status 是 status 型，不是 select。
            "jsonBody": '={{ { "properties": { '
                        '"上稿狀態": { "select": { "name": "已發佈" } }, '
                        '"Status": { "status": { "name": "Existing" } } } } }}',
            "options": {}},
         "id": nid(), "name": "Notion：子列也標記已發佈",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [360, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "onError": "continueRegularOutput",
         "notes": "母列與子列的狀態要一起走，否則母列變「已發佈」時子列還停在\n"
                  "「草稿已建立」，看起來像沒同步成功。\n"
                  "\n"
                  "設 onError=continue：舊文章的 meta 裡沒有 notion_row_id（那是\n"
                  "外掛 0.2.1 之後才存的），這一步失敗不該讓整條回呼算失敗。"})

    # ── 版本標記自動化（Fay 2026-08-11）：發佈後老闆與小編常忘記手動改這四處，
    #    改由流程接手。文字判斷全在 Python 那層（有單元測試），這裡只負責打 API。
    WH = "WP 發佈回呼（Webhook）"
    row_id = f"$('{WH}').item.json.body.notion_row_id"
    mother_id = f"$('{WH}').item.json.body.notion_page_id"

    nodes += [
        {"parameters": notion_http(
            "GET", "=https://api.notion.com/v1/pages/{{ " + row_id + " }}"),
         "id": nid(), "name": "Notion：讀取本次發佈的版本",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [580, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "取這一列的 Version 屬性，決定誰是新的現行版本。"},

        {"parameters": notion_http(
            "POST", f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
            '={{ { "filter": { "property": "Parent item", "relation": '
            '{ "contains": ' + mother_id + ' } }, "page_size": 50 } }}'),
         "id": nid(), "name": "Notion：取得同篇所有版本",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [800, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "用 relation contains 一次撈回同一母列底下的所有版本子列，\n"
                  "不必逐列 GET。要改名的與要拿掉 (Current) 的都在這批裡。"},

        {"parameters": notion_http(
            "GET", "=https://api.notion.com/v1/blocks/{{ " + mother_id + " }}/children"
                   "?page_size=100"),
         "id": nid(), "name": "Notion：取得母列內容區塊",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1020, 220],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Overview 的 Current Version 與 Version History 的標題都在這裡面。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": nid(), "name": "mode", "value": "version_marks", "type": "string"},
            {"id": nid(), "name": "version", "type": "string",
             "value": "={{ $('Notion：讀取本次發佈的版本').item.json"
                      ".properties['Version']?.select?.name ?? '' }}"},
            {"id": nid(), "name": "rows", "type": "array",
             "value": "={{ $('Notion：取得同篇所有版本').item.json.results.map(r => ({"
                      " id: r.id,"
                      " title: r.properties['Doc name']?.title?.[0]?.plain_text ?? '',"
                      " version: r.properties['Version']?.select?.name ?? '' })) }}"},
            {"id": nid(), "name": "blocks", "type": "array",
             "value": "={{ $('Notion：取得母列內容區塊').item.json.results }}"},
        ]}, "options": {}},
         "id": nid(), "name": "組裝版本標記輸入", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [1240, 220]},

        {"parameters": {"language": "pythonNative", "pythonCode": code},
         "id": nid(), "name": "算出要改哪些字", "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1460, 220],
         "notes": "與轉換節點同一份程式，靠 mode=version_marks 走這條分支。\n"
                  "已經是正確狀態時兩個清單都是空的，下游 splitOut 自然什麼都不做。"},

        {"parameters": {"fieldToSplitOut": "row_renames", "options": {}},
         "id": nid(), "name": "拆出要改名的子列", "type": "n8n-nodes-base.splitOut",
         "typeVersion": 1, "position": [1680, 120]},

        {"parameters": notion_http(
            "PATCH", "=https://api.notion.com/v1/pages/{{ $json.id }}",
            '={{ { "properties": { "Doc name": { "title": [ { "text": '
            '{ "content": $json.title } } ] } } } }}'),
         "id": nid(), "name": "Notion：改子列篇名",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1900, 120],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "只改 (Current) 後綴。站上文章標題不受影響——同步時的 clean_title\n"
                  "本來就會把版本後綴與 (Current) 剝掉。"},

        {"parameters": {"fieldToSplitOut": "block_updates", "options": {}},
         "id": nid(), "name": "拆出要改寫的區塊", "type": "n8n-nodes-base.splitOut",
         "typeVersion": 1, "position": [1680, 320]},

        {"parameters": notion_http(
            "PATCH", "=https://api.notion.com/v1/blocks/{{ $json.id }}",
            '={{ { [$json.type]: { "rich_text": $json.rich_text } } }}'),
         "id": nid(), "name": "Notion：改寫母列區塊",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1900, 320],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "Overview 的 Current Version 那行、以及 Version History 的標題。\n"
                  "rich_text 由 Python 端組好並沿用原本的粗體，避免排版跑掉。"},

        {"parameters": notion_http(
            "PATCH", "=https://api.notion.com/v1/pages/{{ " + mother_id + " }}",
            '={{ { "properties": Object.assign('
            '{ "Version": { "select": { "name": '
            "$('Notion：讀取本次發佈的版本').item.json.properties['Version'].select.name"
            ' } } }, '
            # 沒有日期時傳空物件而非 null——傳 null 會把母列既有的日期清掉
            "$('Notion：讀取本次發佈的版本').item.json.properties['Last edited date']"
            "?.date?.start"
            ' ? { "Last edited date": { "date": { "start": '
            "$('Notion：讀取本次發佈的版本').item.json.properties['Last edited date']"
            ".date.start"
            ' } } } : {}) } }}'),
         "id": nid(), "name": "Notion：母列 Version 與日期對齊",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1680, 480],
         "credentials": {"notionApi": {"id": NOTION_CRED_ID, "name": NOTION_CRED_NAME}},
         "notes": "母列的 Version＝目前的現行版本，沿用子列的原始標籤\n"
                  "（v1 的標籤是「v1 (Initial Version)」，不可自行縮寫）。\n"
                  "\n"
                  "Last edited date 一併對齊為現行版本的日期——母列的\n"
                  "Content Freshness 公式吃這個欄位，不對齊的話新鮮度會是錯的\n"
                  "（Fay 2026-08-11）。子列沒有日期時整個欄位不送，\n"
                  "避免把母列既有的值清成空白。"},
    ]

    conns = {
        WH: {"main": [[{"node": "有帶 Notion 母列 id？", "type": "main", "index": 0}]]},
        "有帶 Notion 母列 id？": {"main": [
            [{"node": "Notion：標記已發佈", "type": "main", "index": 0}],
            [{"node": "略過（非同步文章）", "type": "main", "index": 0}]]},
        "Notion：標記已發佈": {"main": [
            [{"node": "Notion：子列也標記已發佈", "type": "main", "index": 0}]]},
        "Notion：子列也標記已發佈": {"main": [
            [{"node": "Notion：讀取本次發佈的版本", "type": "main", "index": 0}]]},
        "Notion：讀取本次發佈的版本": {"main": [
            [{"node": "Notion：取得同篇所有版本", "type": "main", "index": 0}]]},
        "Notion：取得同篇所有版本": {"main": [
            [{"node": "Notion：取得母列內容區塊", "type": "main", "index": 0}]]},
        "Notion：取得母列內容區塊": {"main": [
            [{"node": "組裝版本標記輸入", "type": "main", "index": 0}]]},
        "組裝版本標記輸入": {"main": [
            [{"node": "算出要改哪些字", "type": "main", "index": 0}]]},
        # 三條並行的收尾：改名、改區塊、對齊母列 Version
        "算出要改哪些字": {"main": [[
            {"node": "拆出要改名的子列", "type": "main", "index": 0},
            {"node": "拆出要改寫的區塊", "type": "main", "index": 0},
            {"node": "Notion：母列 Version 與日期對齊", "type": "main", "index": 0}]]},
        "拆出要改名的子列": {"main": [
            [{"node": "Notion：改子列篇名", "type": "main", "index": 0}]]},
        "拆出要改寫的區塊": {"main": [
            [{"node": "Notion：改寫母列區塊", "type": "main", "index": 0}]]},
    }
    return {
        "name": "Synctify — WP 發佈回呼 → Notion 標記已發佈",
        "nodes": nodes, "connections": conns, "active": False,
        "settings": {"executionOrder": "v1"},
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
        if SYNC_WF_OUT.exists():
            embedded = json.loads(SYNC_WF_OUT.read_text(encoding="utf-8"))
            code_node = [n for n in embedded["nodes"]
                         if n["type"] == "n8n-nodes-base.code"]
            if not code_node or code_node[0]["parameters"]["pythonCode"] != body:
                problems.append(str(SYNC_WF_OUT))
        else:
            problems.append(str(SYNC_WF_OUT))
        if problems:
            print("✗ 與 converter/ 來源不同步，請重新產生：\n  - " + "\n  - ".join(problems))
            sys.exit(1)
        print("✓ 產物與來源同步")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(f"✓ 已產生 {OUT}（{len(body.splitlines())} 行）")

    for stale in STALE_WF_OUTS:
        if stale.exists():
            stale.unlink()
            print(f"✓ 已移除過期檔 {stale.name}")

    SYNC_WF_OUT.write_text(json.dumps(build_polling_workflow(body), ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"✓ 已產生 {SYNC_WF_OUT}（Notion 按鈕觸發）")

    PUBCB_WF_OUT.write_text(
        json.dumps(build_publish_callback_workflow(body), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"✓ 已產生 {PUBCB_WF_OUT}（WP 發佈回呼 → Notion 標記已發佈）")


if __name__ == "__main__":
    main()
