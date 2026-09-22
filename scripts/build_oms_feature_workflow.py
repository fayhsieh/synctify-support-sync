"""產生「OMS 功能術語」的 n8n workflow：每個功能的 Glossary 頁上兩顆按鈕，**所有功能共用這一支**。

    ./.venv/bin/python scripts/build_oms_feature_workflow.py
    → n8n/local/oms-feature-glossary.workflow.json（帶真實 webhook path，不入庫）

跟 Marketing 的術語審核區分開（Fay 2026-09-22 的架構圖）：術語審核區只服務 Support Center 上稿，
OMS Docs 的 Glossary 文件是產品端給工程師的，完整術語表在中間當唯一真相。

- **一個功能一個真正獨立的資料庫**，不是篩選過的連結檢視——檢視的篩選不是權限，
  工程師用 AI 讀那一頁時照樣查得到別的功能的詞
- 功能＝OMS Docs 的 **Sub-module** 欄（Sales Orders、Exception Orders…），不是 i18n key 的第一段：
  key 第一段只到模組（order），而 order.labels 這種結構性 key 佔 176 個，看不出功能
- Glossary 頁收該功能**全部**的詞（含已確認），推送後列留著，所以它同時是交付清單
- 推送紀錄寫到同一個 Sub-module、名稱結尾是「Push Log」的那一頁；找不到就寫在 Glossary 頁自己底下

**功能再多也只有這一支 workflow**：按鈕是 OMS Docs 資料庫的按鈕屬性，送 webhook 時會帶那一列的
page id。流程自己讀那一列的 Sub-module、掃那一頁底下的資料庫、再找對應的 Push Log 頁。
新增功能＝複製一組 Glossary／Push Log 文件、選好 Sub-module，不必動 n8n。
"""
import argparse
import json
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import error_codes as ec  # noqa: E402
import wp_env  # noqa: E402

ROOT = HERE.parent
CONVERTER = ROOT / "converter"
OUT_DIR = ROOT / "n8n" / "local"

NOTION_CRED = {"id": "xfGHH7Wx4EucMC0X", "name": "Support Center Sync"}
WEBHOOK_AUTH_CRED = {"id": "8rnHKnTbrXTDCzwc", "name": "Header Auth"}   # 與其他按鈕共用
WEBHOOK_ENV = "N8N_GLOSSARY_REVIEW_WEBHOOK_PATH"                        # 與審核區共用同一組隨機字串
WEBHOOK_PLACEHOLDER = "synctify-glossary-review-CHANGE-ME-TO-A-RANDOM-STRING"

GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"    # 完整術語表
OMS_DOCS_DB = "e64b664440c9449ea96db9e2ea128a6d"    # OMS Docs：Glossary 與 Push Log 文件都在這
FEATURE_PROP = "Sub-module"                         # 功能名稱（Sales Orders、Exception Orders…）
LOG_NAME_SUFFIX = "push log"                        # 同 Sub-module 裡名稱這樣結尾的那一頁＝推送紀錄

PARSE = "解析 page_id"
CONFIG = "讀功能與資料庫"
PLAN = "計算要做的事"
REASON_FAIL = "原因：節點失敗"
PHASES = 3

_ID_NS = uuid.UUID("9f1c7a54-2b83-4de6-8a07-5c19e3b6d240")


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


CONFIG_JS = """
// 這一頁的「FEATURE_PROP」＝要收哪個功能的詞；頁面底下的資料庫＝要寫進哪裡。
// 兩者都讀不到就直接停，訊息寫清楚要補什麼（新增功能時最容易漏的兩步）。
const page = $('Notion：取 Glossary 頁').first().json;
const blocks = $('Notion：取頁面區塊').all().flatMap(i => i.json.results || []);
const parsed = $('PARSE_NODE').first().json;

const feature = page.properties?.['FEATURE_PROP']?.select?.name || '';
if (!feature) {
  throw new Error('這一頁的「FEATURE_PROP」是空的。請先選這一頁對應的功能（例如 Sales Orders），再按一次');
}
// 連結檢視在 API 看起來也是 child_database，抓錯就會寫到別的功能的表。
// 所以要求這一頁只有一個資料庫：多個就停下來，讓人自己決定留哪一個。
const dbs = blocks.filter(b => b.type === 'child_database');
if (!dbs.length) {
  throw new Error('這一頁底下沒有術語資料庫。請先插入一個（可從別的功能的 Glossary 頁複製），再按一次');
}
if (dbs.length > 1) {
  const titles = dbs.map(b => (b.child_database || {}).title || '（無標題）').join('、');
  throw new Error('這一頁有 ' + dbs.length + ' 個資料庫（' + titles + '），不知道要寫進哪一個。'
                  + '請只留術語清單那一個，其餘（例如舊的連結檢視）刪掉再按一次');
}
const db = dbs[0];
return [{ json: {
  action: parsed.action,
  now: parsed.now,
  page_id: parsed.page_id,
  feature,
  feature_db: db.id.replace(/-/g, ''),
  children: blocks,
} }];
"""

ADAPTER = r'''

# ─── n8n 轉接層 ───
# 上游是 Merge（append）：完整表查詢、功能資料庫查詢、OMS Docs 查詢，以及「讀功能與資料庫」的設定。
# **依形狀分辨**而不是依順序：設定那筆有 feature_db，頁面依 parent.database_id 分三堆。
_GLOSSARY_DB = __GLOSSARY_DB__
_OMS_DOCS_DB = __OMS_DOCS_DB__
_LOG_SUFFIX = __LOG_SUFFIX__

_cfg = None
_pages = []
for _it in _items:
    _j = _it['json']
    if 'feature_db' in _j:
        _cfg = _j
        continue
    _res = _j.get('results')
    if isinstance(_res, list):
        _pages.extend([_r for _r in _res if _r.get('object') == 'page'])

if _cfg is None:
    raise ValueError('沒有拿到功能設定——確認「讀功能與資料庫」有執行')

_feature_db = review_norm_id(_cfg['feature_db'])
_full, _review, _docs = [], [], []
for _r in _pages:
    _db = review_norm_id((_r.get('parent') or {}).get('database_id') or '')
    if _db == _feature_db:
        _review.append(_r)
    elif _db == review_norm_id(_GLOSSARY_DB):
        _full.append(_r)
    elif _db == review_norm_id(_OMS_DOCS_DB):
        _docs.append(_r)

if not _full:
    raise ValueError('完整術語表讀到 0 列——確認 Support Center Sync 已連到術語表頁面')

# 推送紀錄寫到同一個功能、名稱結尾是 Push Log 的那一頁；找不到就寫在 Glossary 頁自己底下
_log_page = _cfg['page_id']
for _d in _docs:
    _props = _d.get('properties') or {}
    _sub = ((_props.get('__FEATURE_PROP__') or {}).get('select') or {}).get('name') or ''
    _name = ''.join(_t.get('plain_text', '') for _t in (_props.get('Name') or {}).get('title') or [])
    if _sub == _cfg['feature'] and _name.strip().lower().endswith(_LOG_SUFFIX):
        _log_page = review_norm_id(_d.get('id') or '')
        break

if _cfg['action'] == 'pull':
    # Glossary 頁收該功能全部的詞（含已確認）：它同時是交付給工程的清單（Fay 2026-09-22）
    _plan = review_pull_plan(_full, _review, _cfg.get('children') or [], _cfg['feature_db'],
                             _cfg['now'], features=[_cfg['feature']], pending_only=False,
                             label='從完整表同步')
elif _cfg['action'] == 'push':
    # 推送後列留著；紀錄寫到 Push Log 那一頁
    _plan = review_push_plan(_full, _review, _cfg.get('children') or [], _log_page,
                             _cfg['now'], archive_confirmed=False)
else:
    raise ValueError('不知道要從完整表同步還是推送（action=' + repr(_cfg['action']) + '）')

_plan['feature'] = _cfg['feature']
_plan['log_page'] = _log_page
_plan['stat_full'] = len(_full)
_plan['stat_review'] = len(_review)
return [{'json': _plan}]
'''


def plan_code():
    src = re.split(r'^if __name__ == "__main__":',
                   (CONVERTER / "glossary_review.py").read_text(encoding="utf-8"), flags=re.M)[0]
    header = ("# " + "=" * 66 + "\n"
              "#  自動產生，請勿直接編輯\n"
              "#  來源：converter/glossary_review.py（與術語審核區共用同一份判斷）\n"
              "#  重新產生：./.venv/bin/python scripts/build_oms_feature_workflow.py\n"
              "# " + "=" * 66 + "\n")
    adapter = (ADAPTER.replace("__GLOSSARY_DB__", json.dumps(GLOSSARY_DB))
               .replace("__OMS_DOCS_DB__", json.dumps(OMS_DOCS_DB))
               .replace("__LOG_SUFFIX__", json.dumps(LOG_NAME_SUFFIX))
               .replace("__FEATURE_PROP__", FEATURE_PROP))
    return header + src + adapter


PHASE_OPS_JS = """
// 取出這一階段要打的 Notion API。沒有事做也要輸出一筆 skip，下游的 IF 才會往下走。
const plan = $('PLAN_NODE').first().json;
const ops = (plan.phases || [])[PHASE_INDEX] || [];
return ops.length ? ops.map(op => ({ json: op })) : [{ json: { skip: true } }];
"""

PHASE_CHECK_JS = """
// 執行節點設成 continueRegularOutput：失敗的呼叫變成帶 error 的 item，同階段其他呼叫照樣做完。
// 這裡一次檢查全部，有任何失敗就丟錯（只丟一次），後面的階段不會執行。
const plan = $('PLAN_NODE').first().json;
const expected = ((plan.phases || [])[PHASE_INDEX] || []).length;
const results = $input.all();
const failed = results.filter(r => r.json && r.json.error);
if (failed.length || results.length !== expected) {
  const e = failed.length ? failed[0].json.error : null;
  const detail = e ? (typeof e === 'string' ? e : (e.message || JSON.stringify(e))) : '';
  throw new Error('第 PHASE_NO 階段有 ' + failed.length + ' 個 Notion 呼叫失敗（共 ' + expected + ' 個，回來 '
                  + results.length + ' 個）' + (detail ? '：' + String(detail).slice(0, 500) : ''));
}
return [{ json: { phase: PHASE_NO, done: results.length } }];
"""


def build(webhook_base):
    n = lambda k: det("v2", k)
    notion_headers = {"parameters": [{"name": "Notion-Version", "value": "2022-06-28"}]}
    page_expr = "$('" + PARSE + "').first().json.page_id"

    def notion_get(key, name, url, pos, notes=None):
        return {"parameters": {"url": url,
                               "authentication": "predefinedCredentialType",
                               "nodeCredentialType": "notionApi",
                               "sendHeaders": True, "headerParameters": notion_headers,
                               "options": {}},
                "credentials": {"notionApi": NOTION_CRED},
                "id": n(key), "name": name, "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2, "position": pos, "notes": notes or ""}

    def query_node(key, name, url, pos, notes):
        return {"parameters": {
            "method": "POST", "url": url,
            "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
            "sendHeaders": True, "headerParameters": notion_headers,
            "sendBody": True, "specifyBody": "json",
            "jsonBody": '={{ { "page_size": 100 } }}',
            "options": {"pagination": {"pagination": {
                "paginationMode": "updateAParameterInEachRequest",
                "parameters": {"parameters": [
                    {"type": "body", "name": "start_cursor", "value": "={{ $response.body.next_cursor }}"}]},
                "paginationCompleteWhen": "other",
                "completeExpression": "={{ $response.body.has_more === false }}",
                "limitPagesFetched": True, "maxRequestsF": 20}}}},
            "credentials": {"notionApi": NOTION_CRED},
            "id": n(key), "name": name, "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": pos, "notes": notes}

    def action_node(key, name, action, pos):
        # includeOtherFields 一定要開：Set 預設只輸出自己設的欄位，webhook 的 body 會被丟掉，
        # 下一個節點就讀不到 body.data.id（2026-09-22 實際踩到：流程安靜走到「沒有 page id」）。
        return {"parameters": {"includeOtherFields": True, "assignments": {"assignments": [
            {"id": n(key + "-a"), "name": "action", "value": action, "type": "string"},
        ]}, "options": {}},
            "id": n(key), "name": name, "type": "n8n-nodes-base.set",
            "typeVersion": 3.4, "position": pos,
            "notes": "把 webhook 的 body 一起往下帶（includeOtherFields），解析 page_id 才讀得到。"}

    button_notes = ("每個 Glossary 頁上的「{label}」按鈕區塊 → Send webhook。\n"
                    "網址用這個節點的 Production URL；Add custom header 填與「同步到 WP」按鈕同一組。\n\n"
                    "payload 的 body.data.id 就是那一頁的 id（2026-09-22 實測），所以同一支流程\n"
                    "服務所有功能，按鈕不必做成 OMS Docs 的資料庫屬性——那會讓每一份 Spec、\n"
                    "Guideline 都跑出按鈕（Fay 2026-09-22 不要這樣）。複製 Glossary 頁時按鈕一起複製，\n"
                    "送出的是新那一頁的 id，不用改設定。\n\n"
                    "⚠️ Support Center Sync 要連到 OMS Docs（含各 Glossary／Push Log 頁），\n"
                    "否則讀頁面與資料庫會回 404（錯誤代碼 C8）。\n\n"
                    "path 取自 .env 的 " + WEBHOOK_ENV + "（不入庫），後綴 -oms-{suffix}。")

    nodes = [
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-oms-pull",
                        "responseMode": "onReceived", "authentication": "headerAuth", "options": {}},
         "id": n("wh-pull"), "name": "按鈕：從完整表同步（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 200], "webhookId": n("wh-pull-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="從完整表同步", suffix="pull")},
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-oms-push",
                        "responseMode": "onReceived", "authentication": "headerAuth", "options": {}},
         "id": n("wh-push"), "name": "按鈕：推送回完整表（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 520], "webhookId": n("wh-push-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="推送回完整表", suffix="push")},

        action_node("act-pull", "動作：從完整表同步", "pull", [220, 200]),
        action_node("act-push", "動作：推送回完整表", "push", [220, 520]),

        {"parameters": {"assignments": {"assignments": [
            {"id": n("p-page"), "name": "page_id", "type": "string",
             "value": "={{ ($json.body?.data?.id ?? $json.body?.page?.id ?? $json.body?.id"
                      " ?? $json.query?.page_id ?? '').toString().replace(/-/g, '') }}"},
            {"id": n("p-action"), "name": "action", "value": "={{ $json.action }}", "type": "string"},
            {"id": n("p-now"), "name": "now", "type": "string",
             "value": "={{ $now.setZone('Asia/Taipei').toFormat('yyyy-MM-dd HH:mm') }}"},
        ]}, "options": {}},
         "id": n("parse"), "name": PARSE, "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [440, 360],
         "notes": "與同步、翻譯按鈕同一套取值路徑，也支援 ?page_id= 手動測試。"},

        {"parameters": {"conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
            "conditions": [{"id": n("has-c"), "leftValue": "={{ $json.page_id }}",
                            "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}],
            "combinator": "and"}, "options": {}},
         "id": n("has"), "name": "取得到 page_id？", "type": "n8n-nodes-base.if",
         "typeVersion": 2.2, "position": [660, 360]},

        {"parameters": {"errorMessage": "webhook 沒帶 page id（payload 少了 body.data.id）："
                                        "確認按鈕是從 Glossary 頁送出的 Send webhook，"
                                        "以及「動作：…」節點有開 includeOtherFields"},
         "id": n("no-page"), "name": "payload 無 page_id（失敗）",
         "type": "n8n-nodes-base.stopAndError", "typeVersion": 1, "position": [880, 560],
         "notes": "2026-09-22 實際踩到：Set 節點沒開 includeOtherFields 把 body 丟掉了，\n"
                  "流程安靜走到這裡結束、Notion 沒反應也沒留言，n8n 執行還顯示成功，很難查。\n"
                  "所以這裡標成失敗，至少在 Executions 看得到紅色。"},

        notion_get("page", "Notion：取 Glossary 頁",
                   "=https://api.notion.com/v1/pages/{{ " + page_expr + " }}", [880, 260],
                   "讀那一列的「" + FEATURE_PROP + "」，決定要收完整表裡標了哪個功能的詞。"),

        notion_get("kids", "Notion：取頁面區塊",
                   "=https://api.notion.com/v1/blocks/{{ " + page_expr + " }}/children?page_size=100",
                   [1100, 260],
                   "找頁面底下的術語資料庫（child_database）當寫入目標，\n"
                   "以及頁首「最後動作：」那一段的 id（寫狀態列）。"),

        {"parameters": {"jsCode": CONFIG_JS.replace("FEATURE_PROP", FEATURE_PROP)
                                           .replace("PARSE_NODE", PARSE)},
         "id": n("cfg"), "name": CONFIG, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1320, 260],
         "notes": "功能與目標資料庫都是從那一頁讀出來的——新增功能不必改這支流程。"},

        query_node("q-full", "Notion：取完整術語表",
                   "https://api.notion.com/v1/databases/" + GLOSSARY_DB + "/query", [1540, 120],
                   "全部列都要：Glossary 頁收該功能全部的詞（含已確認）。**要分頁**（上限 100）。"),

        query_node("q-feature", "Notion：取功能術語資料庫",
                   "=https://api.notion.com/v1/databases/{{ $json.feature_db }}/query", [1540, 300],
                   "資料庫 id 來自上一個節點掃到的 child_database，**不寫死**。**要分頁**。"),

        query_node("q-docs", "Notion：取 OMS Docs",
                   "https://api.notion.com/v1/databases/" + OMS_DOCS_DB + "/query", [1540, 480],
                   "用來找同一個 " + FEATURE_PROP + "、名稱結尾是 Push Log 的那一頁（推送紀錄寫在那）。\n"
                   "找不到就寫在 Glossary 頁自己底下。"),

        {"parameters": {"mode": "append", "numberInputs": 4},
         "id": n("merge"), "name": "合流", "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "position": [1760, 260],
         "notes": "四條線：完整表、功能資料庫、OMS Docs、功能設定。下游依形狀分辨，不依順序。"},

        {"parameters": {"language": "pythonNative", "pythonCode": plan_code()},
         "id": n("plan"), "name": PLAN, "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1980, 260],
         "notes": "自動產生，勿直接編輯——改 converter/glossary_review.py 後重新跑產生器。\n"
                  "與術語審核區共用同一份判斷，只是依功能收全部的詞、推送後不移出。"},

        # ── 失敗路徑：在該 Glossary 頁留言 ─────────────────────────────
        {"parameters": {"assignments": {"assignments": [
            {"id": n("f-reason"), "name": "fail_reason", "type": "string",
             "value": "={{ " + ec.to_reason_js() + "("
                      "typeof $json.error === 'string' ? $json.error"
                      " : ($json.error && $json.error.message ? $json.error.message"
                      " : JSON.stringify($json).slice(0, 800))) }}"},
            {"id": n("f-node"), "name": "fail_node", "type": "string", "value": "={{ $prevNode.name }}"},
        ]}, "options": {}},
         "id": n("fail"), "name": REASON_FAIL, "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [2640, 900], "executeOnce": True,
         "notes": "executeOnce：錯誤是逐筆的，不設會一筆留一則言。"},

        {"parameters": {
            "method": "POST", "url": "https://api.notion.com/v1/comments",
            "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
            "sendHeaders": True, "headerParameters": notion_headers,
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { parent: { page_id: " + page_expr + " }, rich_text: ["
                        " { text: { content: '功能術語操作失敗' }, annotations: { bold: true } },"
                        " { text: { content: ($json.fail_node ? '（卡在「' + $json.fail_node + '」）' : '')"
                        " + '：' + ($json.fail_reason || '') + '\\n\\n處理後可以直接再按一次：從完整表同步不會重複建立已有的列；'"
                        " + '推送時已寫進完整表的內容不會重寫，也不會被當成衝突。' } } ] } }}",
            "options": {}},
         "credentials": {"notionApi": NOTION_CRED},
         "id": n("fail-comment"), "name": "Notion：Glossary 頁留言失敗", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [2860, 900], "executeOnce": True},

        {"parameters": {"errorMessage": "=功能術語未完成：{{ $('" + REASON_FAIL + "').first().json.fail_reason }}"},
         "id": n("stop"), "name": "標記本次執行失敗", "type": "n8n-nodes-base.stopAndError",
         "typeVersion": 1, "position": [3080, 900]},
    ]

    def to(name, index=0):
        return {"node": name, "type": "main", "index": index}

    conns = {
        "按鈕：從完整表同步（Webhook）": {"main": [[to("動作：從完整表同步")]]},
        "按鈕：推送回完整表（Webhook）": {"main": [[to("動作：推送回完整表")]]},
        "動作：從完整表同步": {"main": [[to(PARSE)]]},
        "動作：推送回完整表": {"main": [[to(PARSE)]]},
        PARSE: {"main": [[to("取得到 page_id？")]]},
        "取得到 page_id？": {"main": [[to("Notion：取 Glossary 頁")], [to("payload 無 page_id（失敗）")]]},
        "Notion：取 Glossary 頁": {"main": [[to("Notion：取頁面區塊")]]},
        "Notion：取頁面區塊": {"main": [[to(CONFIG)]]},
        CONFIG: {"main": [[to("Notion：取完整術語表"), to("Notion：取功能術語資料庫"),
                           to("Notion：取 OMS Docs"), to("合流", 3)]]},
        "Notion：取完整術語表": {"main": [[to("合流", 0)]]},
        "Notion：取功能術語資料庫": {"main": [[to("合流", 1)]]},
        "Notion：取 OMS Docs": {"main": [[to("合流", 2)]]},
        "合流": {"main": [[to(PLAN)]]},
        REASON_FAIL: {"main": [[to("Notion：Glossary 頁留言失敗")]]},
        "Notion：Glossary 頁留言失敗": {"main": [[to("標記本次執行失敗")]]},
    }

    failable = ["Notion：取 Glossary 頁", "Notion：取頁面區塊", CONFIG,
                "Notion：取完整術語表", "Notion：取功能術語資料庫", "Notion：取 OMS Docs", PLAN]
    for i in range(PHASES):
        no = i + 1
        ops_name, if_name = f"第 {no} 階段：取出操作", f"第 {no} 階段有事要做？"
        run_name, check_name = f"Notion：執行第 {no} 階段", f"第 {no} 階段全部成功？"
        x = 2200 + i * 900
        js = lambda s: (s.replace("PLAN_NODE", PLAN).replace("PHASE_INDEX", str(i))
                        .replace("PHASE_NO", str(no)))
        nodes += [
            {"parameters": {"jsCode": js(PHASE_OPS_JS)}, "id": n(f"ops{no}"), "name": ops_name,
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [x, 260]},
            {"parameters": {"conditions": {
                "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
                "conditions": [{"id": n(f"if{no}-c"), "leftValue": "={{ $json.skip !== true }}",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"}, "options": {}},
             "id": n(f"if{no}"), "name": if_name, "type": "n8n-nodes-base.if", "typeVersion": 2.2,
             "position": [x + 220, 260]},
            {"parameters": {
                "method": "={{ $json.method }}",
                "url": "=https://api.notion.com/v1{{ $json.path }}",
                "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
                "sendHeaders": True, "headerParameters": notion_headers,
                "sendBody": True, "specifyBody": "json", "jsonBody": "={{ $json.body }}",
                "options": {"batching": {"batch": {"batchSize": 1, "batchInterval": 350}}}},
             "credentials": {"notionApi": NOTION_CRED},
             "id": n(f"run{no}"), "name": run_name, "type": "n8n-nodes-base.httpRequest",
             "typeVersion": 4.2, "position": [x + 440, 160], "onError": "continueRegularOutput",
             "notes": "一筆一個 Notion 呼叫，間隔 350ms（Notion 限速約每秒 3 次）。"},
            {"parameters": {"jsCode": js(PHASE_CHECK_JS)}, "id": n(f"check{no}"), "name": check_name,
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [x + 660, 160]},
        ]
        conns[ops_name] = {"main": [[to(if_name)]]}
        conns[run_name] = {"main": [[to(check_name)]]}
        nxt = [to(f"第 {no + 1} 階段：取出操作")] if no < PHASES else []
        conns[if_name] = {"main": [[to(run_name)], nxt]}
        conns[check_name] = {"main": [nxt]}
        if i == 0:
            conns[PLAN] = {"main": [[to(ops_name)]]}
        failable.append(check_name)

    by_name = {nd["name"]: nd for nd in nodes}
    for name in failable:
        by_name[name]["onError"] = "continueErrorOutput"
        main = list(conns.get(name, {}).get("main", [])) or [[]]
        while len(main) < 2:
            main.append([])
        main[1] = [to(REASON_FAIL)]
        conns[name] = {"main": main}

    return {"name": "Synctify — OMS 功能術語（從完整表同步／推送回完整表）",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1"}}


def main():
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    base = wp_env.read_env().get(WEBHOOK_ENV, "").strip()
    if not base:
        base = WEBHOOK_PLACEHOLDER
        print(f"⚠️  .env 沒有 {WEBHOOK_ENV}，webhook path 先用佔位字串。")
    wf = build(base)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "oms-feature-glossary.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  webhook path：{'（佔位字串）' if base == WEBHOOK_PLACEHOLDER else '取自 .env'}（-oms-pull／-oms-push）")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
