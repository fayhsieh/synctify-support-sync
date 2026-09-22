"""產生「OMS 模組術語」的 n8n workflow：每個模組頁上的兩顆按鈕，**所有模組共用這一支**。

    ./.venv/bin/python scripts/build_oms_module_workflow.py
    → n8n/local/oms-module-glossary.workflow.json（帶真實 webhook path，不入庫）

跟 Marketing 的術語審核區分開（Fay 2026-09-22 的架構圖）：
- 術語審核區只服務 Support Center 上稿；OMS 模組文件是產品端的審核區，給工程師看
- **每個模組一個真正獨立的資料庫**，不是篩選過的連結檢視——檢視的篩選不是權限，
  工程師用 AI 讀那一頁時照樣查得到別的模組的詞（Fay 2026-09-22 指出）
- 模組文件收該模組**全部**的詞（含已確認），推送後列留著，所以它同時就是交付清單

**模組再多也只有這一支 workflow**：按鈕是 Synctify OMS 資料庫的按鈕屬性，
送 webhook 時會帶那一列的 page id。流程自己讀那一列的「模組」欄位，
再掃那一頁底下的資料庫當作要寫入的目標。新增模組＝加一頁、填模組、插一個資料庫，不必動 n8n。
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
MODULE_PROP = "模組"                                # Synctify OMS 每一列（每個模組頁）上的欄位

PARSE = "解析 page_id"
CONFIG = "讀模組與資料庫"
PLAN = "計算要做的事"
REASON_FAIL = "原因：節點失敗"
PHASES = 3

_ID_NS = uuid.UUID("9f1c7a54-2b83-4de6-8a07-5c19e3b6d240")


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


CONFIG_JS = """
// 模組頁那一列的「MODULE_PROP」欄位 → 要撈哪個模組；頁面底下的資料庫 → 要寫進哪裡。
// 兩者都讀不到就直接停，訊息寫清楚要補什麼（這是新增模組時最容易漏的兩步）。
const page = $('Notion：取模組頁').first().json;
const blocks = $('Notion：取模組頁區塊').all().flatMap(i => i.json.results || []);
const parsed = $('PARSE_NODE').first().json;

const module = page.properties?.['MODULE_PROP']?.select?.name || '';
if (!module) {
  throw new Error('這一頁的「MODULE_PROP」欄位是空的。請先填上它對應完整術語表的哪個模組（例如 order）');
}
const db = blocks.find(b => b.type === 'child_database');
if (!db) {
  throw new Error('這一頁底下沒有資料庫。請先插入一個術語資料庫（可從別的模組頁複製），再按一次');
}
return [{ json: {
  action: parsed.action,
  now: parsed.now,
  page_id: parsed.page_id,
  module,
  module_db: db.id.replace(/-/g, ''),
  module_title: (db.child_database || {}).title || '',
  children: blocks,
} }];
"""

ADAPTER = r'''

# ─── n8n 轉接層 ───
# 上游是 Merge（append）：完整表查詢、模組資料庫查詢、以及「讀模組與資料庫」的設定。
# **依形狀分辨**而不是依順序：設定那筆有 module_db，頁面依 parent.database_id 分到兩邊。
_GLOSSARY_DB = __GLOSSARY_DB__

_cfg = None
_pages = []
for _it in _items:
    _j = _it['json']
    if 'module_db' in _j:
        _cfg = _j
        continue
    _res = _j.get('results')
    if isinstance(_res, list):
        _pages.extend([_r for _r in _res if _r.get('object') == 'page'])

if _cfg is None:
    raise ValueError('沒有拿到模組設定——確認「讀模組與資料庫」有執行')

_module_db = review_norm_id(_cfg['module_db'])
_full, _review = [], []
for _r in _pages:
    _db = review_norm_id((_r.get('parent') or {}).get('database_id') or '')
    if _db == _module_db:
        _review.append(_r)
    elif _db == review_norm_id(_GLOSSARY_DB):
        _full.append(_r)

if not _full:
    raise ValueError('完整術語表讀到 0 列——確認 Support Center Sync 已連到術語表頁面')

if _cfg['action'] == 'pull':
    # 模組文件收該模組全部的詞（含已確認）：它同時是交付給工程的清單（Fay 2026-09-22）
    _plan = review_pull_plan(_full, _review, _cfg.get('children') or [], _cfg['module_db'],
                             _cfg['now'], module=_cfg['module'], pending_only=False)
elif _cfg['action'] == 'push':
    # 推送後列留著，紀錄寫在模組頁自己底下
    _plan = review_push_plan(_full, _review, _cfg.get('children') or [], _cfg['page_id'],
                             _cfg['now'], archive_confirmed=False)
else:
    raise ValueError('不知道要同步待確認還是推送（action=' + repr(_cfg['action']) + '）')

_plan['module'] = _cfg['module']
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
              "#  重新產生：./.venv/bin/python scripts/build_oms_module_workflow.py\n"
              "# " + "=" * 66 + "\n")
    return header + src + ADAPTER.replace("__GLOSSARY_DB__", json.dumps(GLOSSARY_DB))


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
    n = lambda k: det("v1", k)
    notion_headers = {"parameters": [{"name": "Notion-Version", "value": "2022-06-28"}]}
    page_expr = "$('" + PARSE + "').first().json.page_id"

    def notion_get(key, name, url, pos, notes=None, **extra):
        node = {"parameters": {"url": url,
                               "authentication": "predefinedCredentialType",
                               "nodeCredentialType": "notionApi",
                               "sendHeaders": True, "headerParameters": notion_headers,
                               "options": {}},
                "credentials": {"notionApi": NOTION_CRED},
                "id": n(key), "name": name, "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2, "position": pos}
        if notes:
            node["notes"] = notes
        node.update(extra)
        return node

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
        return {"parameters": {"assignments": {"assignments": [
            {"id": n(key + "-a"), "name": "action", "value": action, "type": "string"},
        ]}, "options": {}},
            "id": n(key), "name": name, "type": "n8n-nodes-base.set",
            "typeVersion": 3.4, "position": pos}

    button_notes = ("Synctify OMS 資料庫的「{label}」按鈕屬性 → Send webhook（不是頁面上的按鈕區塊：\n"
                    "按鈕屬性送出時會帶那一列的 page id，流程才知道是哪個模組）。\n"
                    "網址用這個節點的 Production URL；Add custom header 填與「同步到 WP」按鈕同一組。\n\n"
                    "⚠️ Support Center Sync 要連到 Synctify OMS 這個 wiki（或各模組頁），\n"
                    "否則讀模組頁與模組資料庫都會回 404（錯誤代碼 C8）。\n\n"
                    "path 取自 .env 的 " + WEBHOOK_ENV + "（不入庫），後綴 -oms-{suffix}。")

    nodes = [
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-oms-pull",
                        "responseMode": "onReceived", "authentication": "headerAuth", "options": {}},
         "id": n("wh-pull"), "name": "按鈕：同步待確認（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 200], "webhookId": n("wh-pull-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="同步待確認", suffix="pull")},
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-oms-push",
                        "responseMode": "onReceived", "authentication": "headerAuth", "options": {}},
         "id": n("wh-push"), "name": "按鈕：推送回完整表（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 520], "webhookId": n("wh-push-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="推送回完整表", suffix="push")},

        action_node("act-pull", "動作：同步待確認", "pull", [220, 200]),
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

        {"parameters": {}, "id": n("no-page"), "name": "payload 無 page_id（結束）",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [880, 560],
         "notes": "按鈕若是頁面上的按鈕區塊就會走到這裡——要用資料庫的按鈕屬性才會帶 page id。"},

        notion_get("page", "Notion：取模組頁",
                   "=https://api.notion.com/v1/pages/{{ " + page_expr + " }}", [880, 260],
                   "讀那一列的「" + MODULE_PROP + "」欄位，決定要撈完整表的哪個模組。"),

        notion_get("kids", "Notion：取模組頁區塊",
                   "=https://api.notion.com/v1/blocks/{{ " + page_expr + " }}/children?page_size=100",
                   [1100, 260],
                   "找頁面底下的術語資料庫（child_database）當寫入目標，\n"
                   "以及頁首「最後動作：」那一段的 id（寫狀態列）。"),

        {"parameters": {"jsCode": CONFIG_JS.replace("MODULE_PROP", MODULE_PROP)
                                           .replace("PARSE_NODE", PARSE)},
         "id": n("cfg"), "name": CONFIG, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1320, 260],
         "notes": "模組與目標資料庫都是從那一頁讀出來的——新增模組不必改這支流程。"},

        query_node("q-full", "Notion：取完整術語表",
                   "https://api.notion.com/v1/databases/" + GLOSSARY_DB + "/query", [1540, 160],
                   "全部列都要：模組文件收該模組全部的詞（含已確認）。**要分頁**（上限 100）。"),

        query_node("q-module", "Notion：取模組資料庫",
                   "=https://api.notion.com/v1/databases/{{ $json.module_db }}/query", [1540, 360],
                   "資料庫 id 來自上一個節點掃到的 child_database，**不寫死**。**要分頁**。"),

        {"parameters": {"mode": "append", "numberInputs": 3},
         "id": n("merge"), "name": "合流", "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "position": [1760, 260],
         "notes": "三條線：完整表、模組資料庫、模組設定。下游依形狀分辨，不依順序。"},

        {"parameters": {"language": "pythonNative", "pythonCode": plan_code()},
         "id": n("plan"), "name": PLAN, "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1980, 260],
         "notes": "自動產生，勿直接編輯——改 converter/glossary_review.py 後重新跑產生器。\n"
                  "與術語審核區共用同一份判斷，只是收全部的詞、推送後不移出。"},

        # ── 失敗路徑：在該模組頁留言 ────────────────────────────────────
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
                        " { text: { content: '模組術語操作失敗' }, annotations: { bold: true } },"
                        " { text: { content: ($json.fail_node ? '（卡在「' + $json.fail_node + '」）' : '')"
                        " + '：' + ($json.fail_reason || '') + '\\n\\n處理後可以直接再按一次：同步待確認不會重複建立已有的列；'"
                        " + '推送時已寫進完整表的內容不會重寫，也不會被當成衝突。' } } ] } }}",
            "options": {}},
         "credentials": {"notionApi": NOTION_CRED},
         "id": n("fail-comment"), "name": "Notion：模組頁留言失敗", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [2860, 900], "executeOnce": True},

        {"parameters": {"errorMessage": "=模組術語未完成：{{ $('" + REASON_FAIL + "').first().json.fail_reason }}"},
         "id": n("stop"), "name": "標記本次執行失敗", "type": "n8n-nodes-base.stopAndError",
         "typeVersion": 1, "position": [3080, 900]},
    ]

    def to(name, index=0):
        return {"node": name, "type": "main", "index": index}

    conns = {
        "按鈕：同步待確認（Webhook）": {"main": [[to("動作：同步待確認")]]},
        "按鈕：推送回完整表（Webhook）": {"main": [[to("動作：推送回完整表")]]},
        "動作：同步待確認": {"main": [[to(PARSE)]]},
        "動作：推送回完整表": {"main": [[to(PARSE)]]},
        PARSE: {"main": [[to("取得到 page_id？")]]},
        "取得到 page_id？": {"main": [[to("Notion：取模組頁")], [to("payload 無 page_id（結束）")]]},
        "Notion：取模組頁": {"main": [[to("Notion：取模組頁區塊")]]},
        "Notion：取模組頁區塊": {"main": [[to(CONFIG)]]},
        CONFIG: {"main": [[to("Notion：取完整術語表"), to("Notion：取模組資料庫"), to("合流", 2)]]},
        "Notion：取完整術語表": {"main": [[to("合流", 0)]]},
        "Notion：取模組資料庫": {"main": [[to("合流", 1)]]},
        "合流": {"main": [[to(PLAN)]]},
        REASON_FAIL: {"main": [[to("Notion：模組頁留言失敗")]]},
        "Notion：模組頁留言失敗": {"main": [[to("標記本次執行失敗")]]},
    }

    failable = ["Notion：取模組頁", "Notion：取模組頁區塊", CONFIG,
                "Notion：取完整術語表", "Notion：取模組資料庫", PLAN]
    previous = PLAN
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
        if previous == PLAN:
            conns[PLAN] = {"main": [[to(ops_name)]]}
        failable.append(check_name)
        previous = check_name

    by_name = {nd["name"]: nd for nd in nodes}
    for name in failable:
        by_name[name]["onError"] = "continueErrorOutput"
        main = list(conns.get(name, {}).get("main", [])) or [[]]
        while len(main) < 2:
            main.append([])
        main[1] = [to(REASON_FAIL)]
        conns[name] = {"main": main}

    return {"name": "Synctify — OMS 模組術語（同步待確認／推送回完整表）",
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
    out = OUT_DIR / "oms-module-glossary.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  webhook path：{'（佔位字串）' if base == WEBHOOK_PLACEHOLDER else '取自 .env'}（-oms-pull／-oms-push）")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
