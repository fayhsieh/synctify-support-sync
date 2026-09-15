"""產生「術語審核區」的 n8n workflow：Notion 審核頁上的兩個按鈕。

    ./.venv/bin/python scripts/build_glossary_review_workflow.py
    → n8n/local/glossary-review.workflow.json（帶真實 webhook path，不入庫）

- 取出待確認：完整術語表還沒勾「已確認」的列 → 複製到審核區（已在審核區的不覆蓋）
- 推送回完整表：審核區有改動的列寫回完整表；勾了「已確認」的寫回後移出審核區

判斷全部在 converter/glossary_review.py（與本機腳本 scripts/glossary_review.py 共用、有測試），
n8n 只負責：讀三份資料 → 算出分階段的 Notion API 呼叫 → 依序執行。
**前一階段有任何一個呼叫失敗就停**：推送時完整表還沒寫成功，不能先把審核區的列移出。

webhook path 取自 .env 的 N8N_GLOSSARY_REVIEW_WEBHOOK_PATH，兩個按鈕分別加 -pull／-push。
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
WEBHOOK_AUTH_CRED = {"id": "8rnHKnTbrXTDCzwc", "name": "Header Auth"}   # 與同步、翻譯按鈕共用
WEBHOOK_ENV = "N8N_GLOSSARY_REVIEW_WEBHOOK_PATH"
WEBHOOK_PLACEHOLDER = "synctify-glossary-review-CHANGE-ME-TO-A-RANDOM-STRING"

GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"    # 完整術語表
REVIEW_DB = "0caf57e29f4a4831b93b7c5766a97fa4"      # 待確認詞彙（審核區）
REVIEW_PAGE = "3dc2f2ede27d81609ffae4e44ee1d02e"    # 審核頁：狀態列、推送紀錄、失敗留言都在這

PLAN = "計算要做的事"
REASON_FAIL = "原因：節點失敗"
PHASES = 3          # 取出用前 2 階段（建立＋標註、狀態列），推送用 3 階段（完整表、審核區、紀錄＋狀態列）

_ID_NS = uuid.UUID("2b8e61d4-3f0a-4c55-9d7e-8a1f3c6b0e27")


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


ADAPTER = r'''

# ─── n8n 轉接層 ───
# 上游是 Merge（append），四條線：完整表查詢、審核區查詢、審核頁區塊、動作與時間。
# **依形狀分辨**而不是依順序：頁面依 parent.database_id 分到完整表或審核區，區塊看 object。
_GLOSSARY_DB = __GLOSSARY_DB__
_REVIEW_DB = __REVIEW_DB__
_REVIEW_PAGE = __REVIEW_PAGE__

_full, _review, _children = [], [], []
_action, _now = '', ''
for _it in _items:
    _j = _it['json']
    if 'action' in _j and 'now' in _j:
        _action, _now = _j['action'], _j['now']
        continue
    _res = _j.get('results')
    if not isinstance(_res, list):
        continue
    for _r in _res:
        if _r.get('object') == 'block':
            _children.append(_r)
        elif _r.get('object') == 'page':
            _db = review_norm_id((_r.get('parent') or {}).get('database_id') or '')
            if _db == _REVIEW_DB:
                _review.append(_r)
            elif _db == _GLOSSARY_DB:
                _full.append(_r)

if not _full:
    raise ValueError('完整術語表讀到 0 列——確認「Notion：取完整術語表」有回應，'
                     '以及 Support Center Sync 已連到術語表頁面')
if _action == 'pull':
    _plan = review_pull_plan(_full, _review, _children, _REVIEW_DB, _now)
elif _action == 'push':
    _plan = review_push_plan(_full, _review, _children, _REVIEW_PAGE, _now)
else:
    raise ValueError('不知道要取出還是推送（action=' + repr(_action) + '）')
_plan['stat_full'] = len(_full)
_plan['stat_review'] = len(_review)
return [{'json': _plan}]
'''


def plan_code():
    src = re.split(r'^if __name__ == "__main__":', (CONVERTER / "glossary_review.py").read_text(encoding="utf-8"),
                   flags=re.M)[0]
    header = ("# " + "=" * 66 + "\n"
              "#  自動產生，請勿直接編輯\n"
              "#  來源：converter/glossary_review.py\n"
              "#  重新產生：./.venv/bin/python scripts/build_glossary_review_workflow.py\n"
              "# " + "=" * 66 + "\n")
    adapter = (ADAPTER.replace("__GLOSSARY_DB__", json.dumps(GLOSSARY_DB))
               .replace("__REVIEW_DB__", json.dumps(REVIEW_DB))
               .replace("__REVIEW_PAGE__", json.dumps(REVIEW_PAGE)))
    return header + src + adapter


PHASE_OPS_JS = """
// 取出這一階段要打的 Notion API。沒有事做也要輸出一筆 skip，下游的 IF 才會往下走
// （Code node 輸出 0 筆，後面整條都不會執行）。
const plan = $('PLAN_NODE').first().json;
const ops = (plan.phases || [])[PHASE_INDEX] || [];
return ops.length ? ops.map(op => ({ json: op })) : [{ json: { skip: true } }];
"""

PHASE_CHECK_JS = """
// 「Notion：執行第 N 階段」設成 continueRegularOutput：失敗的呼叫會變成帶 error 的 item，
// 同一階段其他呼叫照樣做完。這裡一次檢查全部：有任何失敗或數量不對就丟錯（只丟一次），
// 走失敗路徑，後面的階段不會執行。
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

    def query_node(key, name, pos, database_id, notes):
        return {"parameters": {
            "method": "POST",
            "url": "https://api.notion.com/v1/databases/" + database_id + "/query",
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
            {"id": n(key + "-t"), "name": "now", "type": "string",
             "value": "={{ $now.setZone('Asia/Taipei').toFormat('yyyy-MM-dd HH:mm') }}"},
        ]}, "options": {}},
            "id": n(key), "name": name, "type": "n8n-nodes-base.set", "typeVersion": 3.4, "position": pos}

    button_notes = ("Notion「產品用術語表（審核區）」頁上的「{label}」按鈕 → Send webhook。\n"
                    "網址用這個節點的 Production URL；Add custom header 填與「同步到 WP」按鈕同一組\n"
                    "（共用 Header Auth 憑證）。\n\n"
                    "⚠️ Support Center Sync 這個 Notion integration 要連到審核頁（頁面 … → Connections），\n"
                    "否則讀審核區會回 404。\n\n"
                    "path 取自 .env 的 " + WEBHOOK_ENV + "（不入庫）。")

    nodes = [
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-pull", "responseMode": "onReceived",
                        "authentication": "headerAuth", "options": {}},
         "id": n("wh-pull"), "name": "按鈕：取出待確認（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 200], "webhookId": n("wh-pull-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="取出待確認")},
        {"parameters": {"httpMethod": "POST", "path": webhook_base + "-push", "responseMode": "onReceived",
                        "authentication": "headerAuth", "options": {}},
         "id": n("wh-push"), "name": "按鈕：推送回完整表（Webhook）", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 500], "webhookId": n("wh-push-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": button_notes.format(label="推送回完整表")},

        action_node("act-pull", "動作：取出", "pull", [220, 200]),
        action_node("act-push", "動作：推送", "push", [220, 500]),

        query_node("q-full", "Notion：取完整術語表", [460, 160], GLOSSARY_DB,
                   "全部列都要（含已確認）：推送時要比對來源列、取出時要知道審核區的列在完整表是否已確認。\n"
                   "**要分頁**（上限 100）。設定與翻譯工作流的「Notion：取產品術語表」相同。"),
        query_node("q-review", "Notion：取審核區", [460, 340], REVIEW_DB, "審核區全部列，**要分頁**。"),
        {"parameters": {"url": "https://api.notion.com/v1/blocks/" + REVIEW_PAGE + "/children?page_size=100",
                        "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
                        "sendHeaders": True, "headerParameters": notion_headers, "options": {}},
         "credentials": {"notionApi": NOTION_CRED},
         "id": n("q-kids"), "name": "Notion：取審核頁區塊", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [460, 520],
         "notes": "只為了找到頁首「最後動作：」那一段的 id（寫狀態列）。它在最上面，第一頁就拿得到。"},

        {"parameters": {"mode": "append", "numberInputs": 4},
         "id": n("merge"), "name": "合流", "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "position": [700, 340],
         "notes": "四條線：完整表、審核區、審核頁區塊、動作與時間。下游依形狀分辨，不依順序。"},

        {"parameters": {"language": "pythonNative", "pythonCode": plan_code()},
         "id": n("plan"), "name": PLAN, "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [920, 340],
         "notes": "自動產生，勿直接編輯——改 converter/glossary_review.py 後重新跑產生器。\n"
                  "輸出一筆：summary、counts、phases（分階段的 Notion API 呼叫）。"},

        # ── 失敗路徑：審核頁留言＋標記失敗 ──────────────────────────────
        {"parameters": {"assignments": {"assignments": [
            {"id": n("f-reason"), "name": "fail_reason", "type": "string",
             "value": "={{ " + ec.to_reason_js() + "("
                      "typeof $json.error === 'string' ? $json.error"
                      " : ($json.error && $json.error.message ? $json.error.message"
                      " : JSON.stringify($json).slice(0, 800))) }}"},
            {"id": n("f-node"), "name": "fail_node", "type": "string", "value": "={{ $prevNode.name }}"},
        ]}, "options": {}},
         "id": n("fail"), "name": REASON_FAIL, "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [1600, 900], "executeOnce": True,
         "notes": "executeOnce：錯誤是逐筆的，不設會一筆留一則言（同步工作流 2026-09-11 一次留了 146 則）。"},

        {"parameters": {
            "method": "POST", "url": "https://api.notion.com/v1/comments",
            "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
            "sendHeaders": True, "headerParameters": notion_headers,
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { parent: { page_id: '" + REVIEW_PAGE + "' }, rich_text: ["
                        " { text: { content: '術語審核區操作失敗' }, annotations: { bold: true } },"
                        " { text: { content: ($json.fail_node ? '（卡在「' + $json.fail_node + '」）' : '')"
                        " + '：' + ($json.fail_reason || '') + '\\n\\n處理後可以直接再按一次：取出不會重複建立已在審核區的列；'"
                        " + '推送時已寫進完整表的內容不會重寫，也不會被當成衝突。' } } ] } }}",
            "options": {}},
         "credentials": {"notionApi": NOTION_CRED},
         "id": n("fail-comment"), "name": "Notion：審核頁留言失敗", "type": "n8n-nodes-base.httpRequest",
         "typeVersion": 4.2, "position": [1820, 900], "executeOnce": True},

        {"parameters": {"errorMessage": "=術語審核區未完成：{{ $('" + REASON_FAIL + "').first().json.fail_reason }}"},
         "id": n("stop"), "name": "標記本次執行失敗", "type": "n8n-nodes-base.stopAndError",
         "typeVersion": 1, "position": [2040, 900]},
    ]

    def to(name, index=0):
        return {"node": name, "type": "main", "index": index}

    fetches = [to("Notion：取完整術語表"), to("Notion：取審核區"), to("Notion：取審核頁區塊"), to("合流", 3)]
    conns = {
        "按鈕：取出待確認（Webhook）": {"main": [[to("動作：取出")]]},
        "按鈕：推送回完整表（Webhook）": {"main": [[to("動作：推送")]]},
        "動作：取出": {"main": [fetches]},
        "動作：推送": {"main": [fetches]},
        "Notion：取完整術語表": {"main": [[to("合流", 0)]]},
        "Notion：取審核區": {"main": [[to("合流", 1)]]},
        "Notion：取審核頁區塊": {"main": [[to("合流", 2)]]},
        "合流": {"main": [[to(PLAN)]]},
        REASON_FAIL: {"main": [[to("Notion：審核頁留言失敗")]]},
        "Notion：審核頁留言失敗": {"main": [[to("標記本次執行失敗")]]},
    }

    failable = ["Notion：取完整術語表", "Notion：取審核區", "Notion：取審核頁區塊", PLAN]
    previous = PLAN
    for i in range(PHASES):
        no = i + 1
        ops_name, if_name = f"第 {no} 階段：取出操作", f"第 {no} 階段有事要做？"
        run_name, check_name = f"Notion：執行第 {no} 階段", f"第 {no} 階段全部成功？"
        x = 1140 + i * 900
        js = lambda s: (s.replace("PLAN_NODE", PLAN).replace("PHASE_INDEX", str(i))
                        .replace("PHASE_NO", str(no)))
        nodes += [
            {"parameters": {"jsCode": js(PHASE_OPS_JS)}, "id": n(f"ops{no}"), "name": ops_name,
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [x, 340]},
            {"parameters": {"conditions": {
                "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
                "conditions": [{"id": n(f"if{no}-c"), "leftValue": "={{ $json.skip !== true }}",
                                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                "combinator": "and"}, "options": {}},
             "id": n(f"if{no}"), "name": if_name, "type": "n8n-nodes-base.if", "typeVersion": 2.2,
             "position": [x + 220, 340]},
            {"parameters": {
                "method": "={{ $json.method }}",
                "url": "=https://api.notion.com/v1{{ $json.path }}",
                "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
                "sendHeaders": True, "headerParameters": notion_headers,
                "sendBody": True, "specifyBody": "json", "jsonBody": "={{ $json.body }}",
                "options": {"batching": {"batch": {"batchSize": 1, "batchInterval": 350}}}},
             "credentials": {"notionApi": NOTION_CRED},
             "id": n(f"run{no}"), "name": run_name, "type": "n8n-nodes-base.httpRequest",
             "typeVersion": 4.2, "position": [x + 440, 240], "onError": "continueRegularOutput",
             "notes": "一筆一個 Notion 呼叫，間隔 350ms（Notion 限速約每秒 3 次）。\n"
                      "continueRegularOutput：失敗的呼叫變成帶 error 的 item，由下一個節點統一檢查。"},
            {"parameters": {"jsCode": js(PHASE_CHECK_JS)}, "id": n(f"check{no}"), "name": check_name,
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [x + 660, 240]},
        ]
        conns[previous] = {"main": [[to(ops_name)]]} if previous == PLAN else conns.get(previous, {"main": [[]]})
        conns[ops_name] = {"main": [[to(if_name)]]}
        conns[run_name] = {"main": [[to(check_name)]]}
        nxt = [to(f"第 {no + 1} 階段：取出操作")] if no < PHASES else []
        conns[if_name] = {"main": [[to(run_name)], nxt]}
        conns[check_name] = {"main": [nxt]}
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

    return {"name": "Synctify — 術語審核區（取出待確認／推送回完整表）",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1"}}


def main():
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    base = wp_env.read_env().get(WEBHOOK_ENV, "").strip()
    if not base:
        base = WEBHOOK_PLACEHOLDER
        print(f"⚠️  .env 沒有 {WEBHOOK_ENV}，webhook path 先用佔位字串。設定方式：")
        print(f'    echo "{WEBHOOK_ENV}=synctify-glossary-review-$(openssl rand -hex 16)" >> .env')
    wf = build(base)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "glossary-review.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  webhook path：{'（佔位字串）' if base == WEBHOOK_PLACEHOLDER else '取自 .env'}（-pull／-push）")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
