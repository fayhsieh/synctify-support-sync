#!/usr/bin/env python3
"""產生「翻譯一篇已發佈文章」的 n8n 工作流（Workflow 3）。

## 2026-09-09 重寫：第一版的輸入端整個是錯的

第一版做的是「撈 TP 的 status=0 字串 → 翻 → POST /tp/update」。實跑翻到的是
Docly 佈景主題的樣板文字與示範資料（「頁腳」「論壇」「CEO, Docly」，還有主題
dummy content 裡的英式俚語）。兩個獨立的錯誤：

**沒有 post_id。** `/tp/strings` 支援 post_id 篩選（經 trp_original_meta 關聯），
不傳就是全站清單。

**更根本的：那條路徑產出的必然是「片段品質」。** TP 自動登錄的一律是片段
（block_type=0，以行內元素邊界切分）；整句列（block_type=1）只有人在 TP 編輯器
上升到外層才會生成，而且一生成就已經是 status=2。所以「撈 status=0 來翻」
翻到的**只可能**是殘句——那正是 Support Center 早期那批生硬譯文的來源，
已經被淘汰過一次。

外掛 `/tp/block` 的註解與 `converter/tp_blocks.py` 的模組說明都寫著這件事。
建第一版時沒讀到那裡。

## 正確的路徑

整句列必須**由我們產生**，原文取自已發佈頁面的區塊 innerHTML——那正是 TP 看到
的同一份來源。`converter/tp_blocks.py` 就是做這件事的（2026-08-14 在測試站
post 7251 實測通過），第一版完全沒用到它。

    取文章網址 → 抓頁面 HTML ┐
    取該篇字典現況            ├→ 合流 → 抽區塊＋組 prompt → 翻譯
    取產品術語表              ┘        → 整理 → POST /tp/block（block_type=1）

`pending_blocks()` 會扣掉字典裡已經是 status=2 的列——**人工精修過的不重送**。
status=0/1 的會重送，那是要的：重跑可以修正舊的機器翻譯。

## dry_run 交給端點自己處理

`/tp/block` 本身吃 dry_run，會回報「會做什麼」而不寫入。比在 n8n 這邊用 IF
分支好：整條路徑（含比對）都會實際走一遍，只是不落地。

## 術語 gate 的插入點

`n8n/translation-node-migration.md` 設計的「翻譯前先確認新術語」還沒做，
插入點在「取產品術語表」與「抽區塊＋組 prompt」之間。

## 用法

    python scripts/build_translate_workflow.py --target test --post 7251
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

# 心柔 2026-09-09 選定。八題裡 terra 拿 4 票（sol 2、luna 2），而且是唯一
# 沒有正確性錯誤的：sol 把「记录为备忘录 / 扣款」截斷、luna 漏掉
# 「in one of the following ways」。
#
# 成本沒有進入決定：2,058 條字串全部翻完 luna ≈ $0.49、terra ≈ $4.87
# ——「貴 10 倍」的實際差額是四塊多美金。
DEFAULT_MODEL = "gpt-5.6-terra"

# 測試站已有手工整句列可比對的文章（tp_blocks 2026-08-14 就是在這篇實測的）
DEFAULT_POST = 7251

PREP = "抽區塊＋組 prompt"
LLM = "OpenAI：翻譯"


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


def _module(name):
    """讀 converter 模組，去掉 __main__ 區塊。"""
    src = (CONVERTER / name).read_text(encoding="utf-8")
    return re.split(r'^if __name__ == "__main__":', src, flags=re.M)[0]


def prep_code(post_id):
    """抽區塊 + 組 prompt 的 Python code node。

    打包 tp_blocks 與 translate_prompt 兩個模組。兩者都只需要 `re`
    （memory：n8n 2.25.7 的 Code node 預設封鎖所有 import）。
    """
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]
    header = (
        "# " + "=" * 66 + "\n"
        "#  自動產生，請勿直接編輯\n"
        "#  來源：converter/tp_blocks.py + converter/translate_prompt.py\n"
        "#        + samples/tp-style-samples.json\n"
        "#  重新產生：./.venv/bin/python scripts/build_translate_workflow.py\n"
        "# " + "=" * 66 + "\n"
    )
    # 兩份模組的頂層名稱沒有衝突（tp_blocks 是 BLOCK_TAGS/normalize/…、
    # translate_prompt 是 build_prompt/find_terms/…）；都只 import re。
    body = _module("tp_blocks.py") + "\n\n" + _module("translate_prompt.py")

    adapter = (
        "\n\n# ─── n8n 轉接層 ───\n"
        "_SAMPLES = " + json.dumps(samples, ensure_ascii=False) + "\n"
        "_POST_ID = " + str(post_id) + "\n"
        "\n"
        "# 上游是 Merge（append），三條線：頁面 HTML、該篇字典現況、術語表。\n"
        "# **依形狀分辨**而不是依順序——Merge 的輸出順序會隨各分支回應速度\n"
        "# 改變，靠順序判斷會間歇性錯亂，而且錯得很安靜。\n"
        "_html = ''\n"
        "_existing = []\n"
        "_gloss = []\n"
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
        "    elif isinstance(_j.get('items'), list):\n"
        "        _existing.extend(_j['items'])\n"
        "    else:\n"
        "        for _k in ('data', 'body', 'html'):\n"
        "            _v = _j.get(_k)\n"
        "            if isinstance(_v, str) and '<' in _v and len(_v) > len(_html):\n"
        "                _html = _v\n"
        "\n"
        "if not _gloss:\n"
        "    raise ValueError('術語表是空的——確認 Notion 節點有回應，'\n"
        "                     '以及該資料庫已與 integration 分享')\n"
        "if not _html:\n"
        "    raise ValueError('沒有拿到頁面 HTML——確認「抓頁面 HTML」節點的 '\n"
        "                     'Response Format 設為 text（不是 JSON）')\n"
        "\n"
        "_res = pending_blocks(_html, _POST_ID, _existing)\n"
        "\n"
        "_out = []\n"
        "for _b in _res['pending']:\n"
        "    _en = _b['original']\n"
        "    _sys, _usr = build_prompt(_en, _gloss, _SAMPLES)\n"
        "    _out.append({'json': {\n"
        "        'post_id': _POST_ID,\n"
        "        'original': _en,\n"
        "        'tag': _b['tag'],\n"
        "        'has_inline': _b['has_inline'],\n"
        "        'system': _sys,\n"
        "        'user': _usr,\n"
        "        'terms': [{'en': _e, 'zh': _z}\n"
        "                  for _e, _z in find_terms(_en, _gloss)],\n"
        "        # 診斷數字每一筆都帶著，隨便點開一筆都看得到整體狀況\n"
        "        'stat_total': _res['total_blocks'],\n"
        "        'stat_already_human': _res['already_human'],\n"
        "        'stat_pending': len(_res['pending']),\n"
        "        'stat_glossary': len(_gloss),\n"
        "        'stat_notion_residue': len(_res['notion_residue']),\n"
        "    }})\n"
        "\n"
        "if not _out:\n"
        "    # 沒有待翻區塊是正常結果（整篇都已人工精修），但要說出來，\n"
        "    # 否則下游看到空輸入會以為是壞掉。\n"
        "    return [{'json': {'nothing_to_do': True,\n"
        "                      'post_id': _POST_ID,\n"
        "                      'stat_total': _res['total_blocks'],\n"
        "                      'stat_already_human': _res['already_human'],\n"
        "                      'stat_glossary': len(_gloss)}}]\n"
        "return _out\n"
    )
    return header + body + adapter


COLLECT_JS = """
// 把譯文整理成 /tp/block 吃的形狀。
//
// **回應形狀要容錯。** OpenAI 節點在不同 n8n 版本／不同「Simplify」設定下
// 回傳的欄位不一樣：content、message.content、choices[0].message.content
// 都可能。寫死一種，換個版本就整條流程安靜地產出空字串。
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

// 沒有待翻區塊——整篇都已人工精修。原樣往下傳，不要偽裝成有東西可寫。
if (prep.length === 1 && prep[0].json.nothing_to_do) {
  return [{ json: { post_id: prep[0].json.post_id, items: [], count: 0,
                    nothing_to_do: true, warnings: [] } }];
}

const outs = $input.all();

// 位置配對。數量對不上就中止——硬配會產生譯文與原文錯位的資料，
// 而 /tp/block 會照著把錯誤的對應寫進三張表。
if (outs.length !== prep.length) {
  throw new Error('翻譯結果 ' + outs.length + ' 筆、送出 ' + prep.length +
                  ' 筆，數量對不上，不進行配對');
}

const items = [];
const warn = [];
for (let i = 0; i < prep.length; i++) {
  const p = prep[i].json;
  const text = pickText(outs[i].json).trim();
  if (!text) { warn.push('「' + p.original.slice(0, 40) + '…」沒有譯文'); continue; }
  if (tagSig(text) !== tagSig(p.original)) {
    warn.push('「' + p.original.slice(0, 40) + '…」標籤結構被改動');
  }
  // block_type 固定 1：我們產生的就是「整句」列。這是這條流程存在的理由，
  // 不要讓端點依有無標籤自動判定——純文字段落也必須是整句列。
  items.push({ original: p.original, translated: text, block_type: 1 });
}

const s = prep[0].json;
return [{ json: {
  post_id: s.post_id,
  items: items,
  count: items.length,
  warnings: warn,
  stats: {
    total_blocks: s.stat_total,
    already_human: s.stat_already_human,
    pending: s.stat_pending,
    glossary_terms: s.stat_glossary,
    notion_residue: s.stat_notion_residue,
  },
} }];
""".strip()


def build(target, model, post_id):
    wp = WP_BASE[target]
    cred = WP_CRED[target]
    n = lambda k: det(target, "v2", k)

    nodes = [
        {"parameters": {}, "id": n("trigger"), "name": "手動執行",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [0, 400],
         "notes": "骨架階段用手動觸發。之後換成 Notion 按鈕的 webhook。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": det("a2", "post"), "name": "post_id",
             "value": post_id, "type": "number"},
            {"id": det("a2", "lang"), "name": "language",
             "value": "zh_CN", "type": "string"},
            {"id": det("a2", "model"), "name": "model",
             "value": model, "type": "string"},
            {"id": det("a2", "dry"), "name": "dry_run", "value": True,
             "type": "boolean"},
        ]}, "options": {}},
         "id": n("params"), "name": "參數",
         "type": "n8n-nodes-base.set", "typeVersion": 3.4,
         "position": [200, 400],
         "notes": "**model 必須真的驅動 OpenAI 節點。**\n"
                  "那個節點的模型欄若用下拉選單（mode=list）挑，值就寫死在節點裡，\n"
                  "這裡改了不會有任何效果——一個安靜失效的參數比沒有參數更糟。\n\n"
                  "dry_run 會直接傳給 /tp/block：整條路徑都會走一遍（含比對），\n"
                  "只是不落地，並回報「會做什麼」。"},

        {"parameters": {
            "url": "=" + wp + "/wp-json/wp/v2/docs/"
                   "{{ $json.post_id }}?_fields=id,link,title",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("url"), "name": "WP：取文章網址",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [420, 260],
         "notes": "要抓的是**已發佈頁面的渲染 HTML**，不是 REST 的 content.rendered\n"
                  "（memory：content.rendered 會漂移）。所以先問網址、再抓頁面。"},

        {"parameters": {
            "url": "={{ $json.link }}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "options": {"response": {"response": {"responseFormat": "text"}}}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("html"), "name": "抓頁面 HTML",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [640, 260],
         "notes": "**Response Format 必須是 text。** 設成 JSON 會拿到解析失敗或空值，\n"
                  "而下游只會看到「沒有 HTML」。\n\n"
                  "tp_blocks 會把範圍限縮在 Elementor 內容容器：整頁 143k、\n"
                  "內容區只有 44k。不限縮會撈到側邊欄、頁首頁尾，\n"
                  "甚至 Google Tag Manager 的 iframe。"},

        {"parameters": {
            "url": wp + "/wp-json/synctify/v1/tp/strings",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "language",
                 "value": "={{ $('參數').first().json.language }}"},
                {"name": "post_id",
                 "value": "={{ $('參數').first().json.post_id }}"},
                {"name": "limit", "value": "500"},
            ]},
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("existing"), "name": "WP：取該篇字典現況",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [420, 400],
         "notes": "**不篩 status**：全部撈回來，由 pending_blocks 決定哪些要重送。\n"
                  "規則是「只有 status=2（人工精修）才算完成、不必再送」，\n"
                  "status=0/1 會重送——重跑可以修正舊的機器翻譯。\n\n"
                  "**post_id 一定要傳。** 不傳就是全站清單：2026-09-09 第一版\n"
                  "沒傳，翻到的是 Docly 主題的樣板文字與示範資料。"},

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
         "position": [420, 540],
         "notes": "**要分頁**：術語表 161 筆、單次上限 100。\n"
                  "驗證方式：看下游輸出的 glossary_terms——2026-09-09 實測是 161，\n"
                  "掉到 100 以下就是分頁沒生效、只拿到第一頁，\n"
                  "而流程會照常跑完不報錯。\n\n"
                  "── 術語 gate 的插入點 ──\n"
                  "「翻譯前先確認新術語」那一段還沒做，要做的話接在這裡之後。"},

        {"parameters": {"mode": "append", "numberInputs": 3},
         "id": n("merge"), "name": "合流",
         "type": "n8n-nodes-base.merge", "typeVersion": 3,
         "position": [880, 400],
         "notes": "三條線：頁面 HTML、該篇字典現況、術語表。\n"
                  "下游**依形狀分辨**而不是依順序——Merge 的輸出順序會隨各分支\n"
                  "回應速度改變，靠順序判斷會間歇性錯亂，而且錯得很安靜。"},

        {"parameters": {"language": "pythonNative",
                        "pythonCode": prep_code(post_id)},
         "id": n("prep"), "name": PREP,
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1100, 400],
         "notes": "自動產生，勿直接編輯——改 converter/tp_blocks.py 或\n"
                  "converter/translate_prompt.py 後重新跑產生器。\n\n"
                  "**這裡抽的是「整句」，不是 TP 的片段。** TP 自動登錄的一律是\n"
                  "片段（以行內元素邊界切分），翻片段就是 Support Center 早期\n"
                  "那批生硬譯文的來源。整句列必須由我們產生，原文取自已發佈頁面\n"
                  "的區塊 innerHTML——那正是 TP 看到的同一份來源。"},

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
         "position": [1320, 400],
         "notes": "模型欄用 By ID＋運算式，值取自「參數」節點。\n"
                  "用下拉選單（mode=list）會把型號寫死在這裡，\n"
                  "「參數」那個欄位就變成騙人的擺設。\n\n"
                  "（By ID 模式下不會出現 Tools 接口——本來就用不到，\n"
                  "這是純翻譯不是 agent。）"},

        {"parameters": {"jsCode": COLLECT_JS.replace("PREP_NODE_NAME", PREP)},
         "id": n("collect"), "name": "整理譯文",
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [1540, 400],
         "notes": "**block_type 固定 1**：我們產生的就是整句列。不要讓端點依\n"
                  "有無標籤自動判定——純文字段落也必須是整句列。\n\n"
                  "配對靠位置，數量對不上就中止：硬配會讓 /tp/block 把錯誤的\n"
                  "對應寫進三張表。"},

        {"parameters": {
            "method": "POST",
            "url": wp + "/wp-json/synctify/v1/tp/block",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpBasicAuth",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { \"language\": $('參數').first().json.language, "
                        "\"post_id\": $('參數').first().json.post_id, "
                        "\"dry_run\": $('參數').first().json.dry_run, "
                        "\"items\": $json.items } }}",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("write"), "name": "WP：寫回整句列",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [1760, 400],
         "notes": "寫的是「整句」列（block_type=1），需要同時寫三張表：\n"
                  "  trp_original_strings → trp_original_meta（掛 post_parent_id）\n"
                  "  → trp_dictionary_*（譯文本體，original_id 指回去）\n"
                  "端點已經處理這件事。\n\n"
                  "status 一律寫 1（機器翻譯），**已是 status=2 的列永不覆蓋**\n"
                  "（與 /tp/update 同規則；2026-09-09 實測過那條保護）。\n\n"
                  "dry_run=true 時端點只回報會做什麼，不寫入。"},
    ]

    conns = {
        "手動執行": {"main": [[{"node": "參數", "type": "main", "index": 0}]]},
        "參數": {"main": [[
            {"node": "WP：取文章網址", "type": "main", "index": 0},
            {"node": "WP：取該篇字典現況", "type": "main", "index": 0},
            {"node": "Notion：取產品術語表", "type": "main", "index": 0}]]},
        "WP：取文章網址": {"main": [[
            {"node": "抓頁面 HTML", "type": "main", "index": 0}]]},
        "抓頁面 HTML": {"main": [[{"node": "合流", "type": "main", "index": 0}]]},
        "WP：取該篇字典現況": {"main": [[
            {"node": "合流", "type": "main", "index": 1}]]},
        "Notion：取產品術語表": {"main": [[
            {"node": "合流", "type": "main", "index": 2}]]},
        "合流": {"main": [[{"node": PREP, "type": "main", "index": 0}]]},
        PREP: {"main": [[{"node": LLM, "type": "main", "index": 0}]]},
        LLM: {"main": [[{"node": "整理譯文", "type": "main", "index": 0}]]},
        "整理譯文": {"main": [[
            {"node": "WP：寫回整句列", "type": "main", "index": 0}]]},
    }

    return {"name": "Synctify — 翻譯文章整句列（" +
                    ("測試站" if target == "test" else "正式站") + "；手動觸發）",
            "nodes": nodes, "connections": conns,
            "settings": {"executionOrder": "v1"}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=["test", "prod"], default="test")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"模型 id（預設 {DEFAULT_MODEL}，心柔 2026-09-09 選定）")
    ap.add_argument("--post", type=int, default=DEFAULT_POST,
                    help=f"要翻的文章 post id（預設 {DEFAULT_POST}）")
    args = ap.parse_args()

    wf = build(args.target, args.model, args.post)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"translate-article.{args.target}.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  站台：{WP_BASE[args.target]}")
    print(f"  文章：post {args.post}")
    print(f"  模型：{args.model}")
    print(f"  dry_run：預設 true（端點只回報，不寫入）")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
