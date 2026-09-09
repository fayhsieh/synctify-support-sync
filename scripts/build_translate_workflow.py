#!/usr/bin/env python3
"""產生「翻譯 TranslatePress 未翻譯字串」的 n8n 工作流（Workflow 3 骨架）。

## 這支跟 build_model_compare.py 的分工

`build_model_compare.py` 是**選型工具**：拿固定樣本比較幾個模型，跑完就丟。
這支是**正式流程**：從 WP 撈未翻譯字串、翻譯、寫回去。

兩者共用 `converter/translate_prompt.py`——prompt 規則只有一份，
改了規則兩邊同時受益。這是刻意的：選型時測到的品質，上線後才算數。

## 為什麼要 Merge 節點

組 prompt 需要兩個來源：**產品術語表**（Notion）與**未翻譯字串**（WP）。
n8n 的 item 是線性流動的，一個 Code node 只看得到上游一條連線。
所以兩邊各自撈完後用 Merge（append）合流，適配層再依形狀分開：
帶 `results` 的是 Notion 回應、帶 `original` 的是 WP 字串。

## 模型是參數，不是寫死的

`參數` 節點裡的 `model` 留空白等心柔選完再填。骨架先建起來，
選型結果一出來就能跑——等待期間不必空轉。

## 術語 gate 的插入點

`n8n/translation-node-migration.md` 設計了「翻譯前先確認新術語」的關卡
（抽術語 → 比對 → 新詞發到 Notion 等人工確認 → 回寫詞彙表）。
那一段**還沒做**，插入點在「取詞彙表」與「組 prompt」之間，
節點的 notes 有標。

## 用法

    python scripts/build_translate_workflow.py --target test
    python scripts/build_translate_workflow.py --target test --model gpt-5.6-luna

產物在 `n8n/local/`（CLAUDE.md：要匯入的是 local 版）。
"""
import argparse
import json
import pathlib
import re
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "converter"
SAMPLES = ROOT / "samples" / "tp-style-samples.json"
OUT_DIR = ROOT / "n8n" / "local"

_ID_NS = uuid.UUID("7f3a1c02-5d64-4e8b-9a11-2c6d0f4b7e93")

# n8n 憑證「引用」——只有識別碼，不含任何密鑰
# （CLAUDE.md：匯出時確認憑證欄位為引用而非明文）
OPENAI_CRED = {"id": "UEtEu6Jad1QJoQvz", "name": "OpenAi account 2"}
NOTION_CRED = {"id": "xfGHH7Wx4EucMC0X", "name": "Support Center Sync"}
WP_CRED = {
    "test": {"id": "oIyDk22ZdtDbphHm", "name": "WordPress Credential (Sandbox)"},
    "prod": {"id": "7yIBiKpBdDB40C4I", "name": "WordPress Credential (Production)"},
}
WP_BASE = {"test": "https://support.synctify.io",
           "prod": "https://support.synctify.net"}

# 產品用術語表。查詢用的 database id，不是 collection id
# （後者查會回 404，而訊息是誤導性的「請與 integration 分享」）。
GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"

PREP = "組 prompt（每段一則）"
LLM = "OpenAI：翻譯"


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


def prep_code():
    """組 prompt 的 Python code node：打包 translate_prompt 與樣本。

    n8n 的 Code node 預設封鎖所有 import（memory：n8n 2.25.7），
    translate_prompt 已經壓到只需 `re`，可以整份塞進去。
    """
    src = re.split(r'^if __name__ == "__main__":',
                   (CONVERTER / "translate_prompt.py").read_text(encoding="utf-8"),
                   flags=re.M)[0]
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]
    header = (
        "# " + "=" * 66 + "\n"
        "#  自動產生，請勿直接編輯\n"
        "#  來源：converter/translate_prompt.py + samples/tp-style-samples.json\n"
        "#  重新產生：./.venv/bin/python scripts/build_translate_workflow.py\n"
        "# " + "=" * 66 + "\n"
    )
    adapter = (
        "\n\n# ─── n8n 轉接層 ───\n"
        "_SAMPLES = " + json.dumps(samples, ensure_ascii=False) + "\n"
        "\n"
        "# 上游是 Merge（append），一條線是 Notion 的術語表、一條線是 WP 的\n"
        "# 未翻譯字串。**依形狀分辨**而不是依順序——Merge 的輸出順序會隨兩邊\n"
        "# 回應速度改變，靠順序判斷會間歇性錯亂，而且錯得很安靜。\n"
        "_gloss = []\n"
        "_strings = []\n"
        "for _it in _items:\n"
        "    _j = _it['json']\n"
        "    if isinstance(_j.get('results'), list):\n"
        "        for _pg in _j['results']:\n"
        "            _p = _pg.get('properties') or {}\n"
        "            _en = ''.join(_x.get('plain_text', '')\n"
        "                          for _x in ((_p.get('English') or {}).get('title') or []))\n"
        "            _zh = ''.join(_x.get('plain_text', '')\n"
        "                          for _x in ((_p.get('\\u7b80\\u4f53\\u4e2d\\u6587') or {})\n"
        "                                     .get('rich_text') or []))\n"
        "            if _en.strip() and _zh.strip():\n"
        "                _gloss.append({'en': _en.strip(), 'zh': _zh.strip()})\n"
        "    elif _j.get('original'):\n"
        "        _strings.append(_j)\n"
        "    elif isinstance(_j.get('items'), list):\n"
        "        # /tp/strings 若把結果包在 items 裡\n"
        "        for _s in _j['items']:\n"
        "            if _s.get('original'):\n"
        "                _strings.append(_s)\n"
        "\n"
        "_out = []\n"
        "for _s in _strings:\n"
        "    _en = _s.get('original') or ''\n"
        "    _sys, _usr = build_prompt(_en, _gloss, _SAMPLES)\n"
        "    _hits = [{'en': _e, 'zh': _z} for _e, _z in find_terms(_en, _gloss)]\n"
        "    _out.append({'json': {\n"
        "        'string_id': _s.get('id'),\n"
        "        'original': _en,\n"
        "        'system': _sys,\n"
        "        'user': _usr,\n"
        "        'terms': _hits,\n"
        "        'glossary_terms': len(_gloss),\n"
        "    }})\n"
        "\n"
        "if not _gloss:\n"
        "    # 空術語表不會讓流程失敗，但 prompt 會完全沒有術語約束。\n"
        "    # 2026-09-08 model-compare 就是這樣跑了一輪才發現，比較結果作廢。\n"
        "    raise ValueError('術語表是空的——請確認 Notion 節點有回應，'\n"
        "                     '以及該資料庫已與 integration 分享')\n"
        "return _out\n"
    )
    return header + src + adapter


COLLECT_JS = """
// 把 OpenAI 節點的回應整理成可寫回 WP 的形狀。
//
// **回應形狀要容錯。** OpenAI 節點在不同 n8n 版本／不同「Simplify」設定下
// 回傳的欄位不一樣：有時是 message.content、有時是 content、
// 有時是原始 API 形狀 choices[0].message.content。寫死一種，
// 換個版本就整條流程安靜地產出空字串——而空字串會被寫進 WP。
function pickText(j) {
  if (!j) return '';
  if (typeof j.content === 'string') return j.content;
  if (j.message && typeof j.message.content === 'string') return j.message.content;
  if (j.choices && j.choices[0] && j.choices[0].message)
    return j.choices[0].message.content || '';
  if (typeof j.text === 'string') return j.text;
  return '';
}

function tagSig(html) {
  const m = String(html || '').match(/<[^>]+>/g) || [];
  return m.map(t => t.replace(/\\s+/g, ' ').trim()).join('');
}

const prep = $('PREP_NODE_NAME').all();
const outs = $input.all();

// 位置配對。數量對不上就中止——硬配會產生一批看起來正常、
// 實際上譯文與原文錯位的資料，而那會被寫進 WP。
if (outs.length !== prep.length) {
  throw new Error('翻譯結果 ' + outs.length + ' 筆、送出 ' + prep.length +
                  ' 筆，數量對不上，不進行配對');
}

const items = [];
const warn = [];
for (let i = 0; i < prep.length; i++) {
  const p = prep[i].json;
  const text = pickText(outs[i].json).trim();
  if (!text) { warn.push('#' + p.string_id + ' 沒有譯文'); continue; }
  if (tagSig(text) !== tagSig(p.original)) {
    warn.push('#' + p.string_id + ' 標籤結構被改動');
  }
  items.push({ id: p.string_id, translated: text });
}

return [{ json: { items: items, count: items.length, warnings: warn } }];
""".strip()


def build(target, model):
    wp = WP_BASE[target]
    cred = WP_CRED[target]
    n = lambda k: det(target, k)

    nodes = [
        {"parameters": {}, "id": n("trigger"), "name": "手動執行",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [0, 300],
         "notes": "骨架階段用手動觸發。之後換成 Notion 按鈕的 webhook——\n"
                  "設計已定（memory：按鈕 webhook 為主、輪詢待命）。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": det("a", "lang"), "name": "language",
             "value": "zh_CN", "type": "string"},
            {"id": det("a", "model"), "name": "model",
             "value": model or "", "type": "string"},
            {"id": det("a", "limit"), "name": "limit", "value": 20, "type": "number"},
            {"id": det("a", "dry"), "name": "dry_run", "value": True,
             "type": "boolean"},
        ]}, "options": {}},
         "id": n("params"), "name": "參數",
         "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [200, 300],
         "notes": "**model 等心柔選完再填。** 骨架先建起來，"
                  "選型一出來就能跑。\n\n"
                  "dry_run 預設 true：跑完只回報會寫什麼，不真的寫進 WP。\n"
                  "第一次接上正式流程時務必先用 dry_run 看一遍。\n\n"
                  "limit 是每次處理幾條字串，先小量驗證再放大。"},

        {"parameters": {
            "method": "POST",
            "url": "https://api.notion.com/v1/databases/" + GLOSSARY_DB + "/query",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "notionApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Notion-Version", "value": "2022-06-28"}]},
            "sendBody": True, "specifyBody": "json",
            "jsonBody": '={{ { "page_size": 100 } }}',
            "options": {"pagination": {"pagination": {
                "paginationMode": "updateAParameterInEachRequest",
                "parameters": {"parameters": [
                    {"type": "body", "name": "start_cursor",
                     "value": "={{ $response.body.next_cursor }}"}]},
                "paginationCompleteWhen": "other",
                "completeExpression": "={{ $response.body.has_more === false }}",
                "limitPagesFetched": True, "maxRequestsF": 10}}}},
         "credentials": {"notionApi": NOTION_CRED},
         "id": n("gloss"), "name": "Notion：取產品術語表",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [420, 180],
         "notes": "**要分頁**：術語表 150+ 筆、單次上限 100。漏掉的詞若剛好出現在\n"
                  "原文裡，就變成「有詞彙表卻沒約束到」——比沒有更難察覺。\n\n"
                  "── 術語 gate 的插入點 ──\n"
                  "translation-node-migration.md 設計了「翻譯前先確認新術語」：\n"
                  "抽術語 → 比對詞彙表 → 新詞發到 Notion 等人工確認 → 回寫。\n"
                  "那一段還沒做，要做的話接在這個節點之後、組 prompt 之前。"},

        {"parameters": {
            "url": wp + "/wp-json/synctify/v1/tp/strings",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "language", "value": "={{ $('參數').first().json.language }}"},
                {"name": "status", "value": "0"},
                {"name": "limit", "value": "={{ $('參數').first().json.limit }}"},
            ]},
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("strings"), "name": "WP：取未翻譯字串",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [420, 420],
         "notes": "status=0 是未翻譯。**不要撈 status=2**——那是人工精修的譯文，\n"
                  "端點雖然有保護不會被覆蓋（2026-09-09 實測），\n"
                  "但撈進來只會浪費 API 呼叫。"},

        {"parameters": {"mode": "append", "numberInputs": 2},
         "id": n("merge"), "name": "合流",
         "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "position": [640, 300],
         "notes": "組 prompt 需要術語表與字串兩個來源，但 Code node 只看得到\n"
                  "上游一條連線，所以在這裡合流。\n\n"
                  "下游**依形狀分辨**而不是依順序——Merge 的輸出順序會隨兩邊\n"
                  "回應速度改變，靠順序判斷會間歇性錯亂，而且錯得很安靜。"},

        {"parameters": {"language": "pythonNative", "pythonCode": prep_code()},
         "id": n("prep"), "name": PREP,
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [860, 300],
         "notes": "自動產生，勿直接編輯——改 converter/translate_prompt.py 後\n"
                  "重新跑 scripts/build_translate_workflow.py。\n\n"
                  "術語表為空會直接拋錯而不是安靜跑完：2026-09-08 model-compare\n"
                  "就是在沒接上術語表的情況下跑了一輪，結果整份作廢。"},

        {"parameters": {
            "modelId": {"__rl": True, "mode": "id",
                        "value": "={{ $('參數').first().json.model }}"},
            "messages": {"values": [
                {"role": "system", "content": "={{ $json.system }}"},
                {"content": "={{ $json.user }}"},
            ]},
            "options": {}},
         "credentials": {"openAiApi": OPENAI_CRED},
         "id": n("llm"), "name": LLM,
         "type": "@n8n/n8n-nodes-langchain.openAi", "typeVersion": 1.8,
         "position": [1080, 300],
         "notes": "⚠️ 這是本專案第一個 OpenAI 節點（其餘都用 HTTP Request）。\n"
                  "參數形狀會隨 n8n 版本變動——匯入後請確認：\n"
                  "  1. 模型欄是「By ID」模式，值取自 參數.model\n"
                  "  2. 憑證是 OpenAi account 2\n"
                  "  3. system／user 兩則訊息都在\n\n"
                  "下游對回應形狀已做容錯（content／message.content／\n"
                  "choices[0].message.content 都吃），不必為此改設定。"},

        {"parameters": {"jsCode": COLLECT_JS.replace("PREP_NODE_NAME", PREP)},
         "id": n("collect"), "name": "整理譯文",
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1300, 300],
         "notes": "配對靠位置，數量對不上就中止——硬配會產生一批看起來正常、\n"
                  "實際上譯文與原文錯位的資料，而那會被寫進 WP。\n\n"
                  "標籤結構被改動的會列進 warnings，但**不擋下寫入**：\n"
                  "骨架階段先看得到問題，要不要擋等實際跑過再決定。"},

        {"parameters": {"conditions": {"options": {
            "caseSensitive": True, "version": 2, "typeValidation": "strict"},
            "conditions": [{"id": det("c", "dry"),
                            "leftValue": "={{ $('參數').first().json.dry_run }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "false"}}],
            "combinator": "and"}, "options": {}},
         "id": n("if"), "name": "要真的寫入嗎",
         "type": "n8n-nodes-base.if", "typeVersion": 2.2,
         "position": [1520, 300],
         "notes": "dry_run=true 走 false 分支（不寫入），只留下整理好的結果。\n"
                  "第一次接上正式流程時務必先這樣看一遍。"},

        {"parameters": {
            "method": "POST",
            "url": wp + "/wp-json/synctify/v1/tp/update",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { \"language\": $('參數').first().json.language, "
                        "\"items\": $json.items } }}",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("write"), "name": "WP：寫回譯文",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [1740, 220],
         "notes": "端點一律寫 status=1（機器翻譯），**已是 status=2 的列跳過不覆蓋**。\n"
                  "那個保護 2026-09-09 在測試站實測過（scripts/verify_tp_guard.py），\n"
                  "正反兩面都驗：人工譯文不會被蓋、機器譯文寫得進去。\n\n"
                  "回應會帶 updated／skipped_human／not_found／failed 四個計數，\n"
                  "**not_found 不是 0 就要查**——代表 id 已失效，那批譯文沒寫進去。"},

        {"parameters": {}, "id": n("noop"), "name": "dry run：不寫入",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1,
         "position": [1740, 380]},
    ]

    conns = {
        "手動執行": {"main": [[{"node": "參數", "type": "main", "index": 0}]]},
        "參數": {"main": [[
            {"node": "Notion：取產品術語表", "type": "main", "index": 0},
            {"node": "WP：取未翻譯字串", "type": "main", "index": 0}]]},
        "Notion：取產品術語表": {"main": [[
            {"node": "合流", "type": "main", "index": 0}]]},
        "WP：取未翻譯字串": {"main": [[
            {"node": "合流", "type": "main", "index": 1}]]},
        "合流": {"main": [[{"node": PREP, "type": "main", "index": 0}]]},
        PREP: {"main": [[{"node": LLM, "type": "main", "index": 0}]]},
        LLM: {"main": [[{"node": "整理譯文", "type": "main", "index": 0}]]},
        "整理譯文": {"main": [[{"node": "要真的寫入嗎", "type": "main", "index": 0}]]},
        "要真的寫入嗎": {"main": [
            [{"node": "WP：寫回譯文", "type": "main", "index": 0}],
            [{"node": "dry run：不寫入", "type": "main", "index": 0}]]},
    }

    return {"name": "Synctify｜翻譯 TP 未翻譯字串（" +
                    ("測試站" if target == "test" else "正式站") + "）",
            "nodes": nodes, "connections": conns,
            "settings": {"executionOrder": "v1"}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=["test", "prod"], default="test")
    ap.add_argument("--model", default="",
                    help="模型 id。留空表示還沒決定，匯入後在「參數」節點填")
    args = ap.parse_args()

    wf = build(args.target, args.model)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"translate-tp-strings.{args.target}.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  站台：{WP_BASE[args.target]}")
    print(f"  模型：{args.model or '（未填——等心柔選完在「參數」節點補上）'}")
    print(f"  dry_run：預設 true，先看會寫什麼再放行")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
