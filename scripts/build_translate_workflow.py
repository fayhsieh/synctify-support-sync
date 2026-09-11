#!/usr/bin/env python3
"""產生「翻譯一篇已發佈文章」的 n8n 工作流（Workflow 3）——Notion「翻譯」按鈕觸發。

## 2026-09-11：從手動觸發改成按鈕（Fay 定的流程）

    同步 → 術語檢查欄提醒補譯文 → 補完、勾已確認 → 在 Notion 按「翻譯」
    → 翻譯狀態立刻顯示「翻譯中」（按鈕自己的 Edit property，不等 n8n）
    → 還有沒確認的詞就擋下：翻譯狀態「待術語確認」＋留言列出清單
    → 真正寫入完成才顯示「已翻譯完成」

整條鏈：

    按鈕 → 取該列／母列 → 檢查能不能翻（母列？草稿層？有 WP Post ID？）
           不能翻或前台不是最新版 → 「尚無法開始翻譯」＋留言原因
         → 參數 → 取文章網址 → 查同步草稿 → 前台是最新版？（已發佈、沒有未發佈的同步內容）
         → 抓頁面 HTML／該篇字典／全站人工譯文／術語表 → 合流
         → 抽區塊＋術語閘門＋組 prompt ─┬→ 擋下：待術語確認＋留言
                                        └→ OpenAI → 整理 → POST /tp/block
         → 彙整寫入結果 → 再讀一次該列 → 還是「翻譯中」？
              ├→ 是：已翻譯完成＋留言
              └→ 否：有人中途按了同步——不標完成，留言說明
    任何節點出錯 → ❌ 翻譯失敗＋一則留言（寫出卡在哪個節點）

### 為什麼收尾要「再讀一次該列」

翻譯一篇要十幾分鐘。這段期間若有人按了同步，同步按鈕會立刻把翻譯狀態改回「－」，
文章內容也可能改了——新內容其實還沒翻。翻譯跑完若照樣標「已翻譯完成」，
就是在說謊。所以收尾前看狀態還是不是「翻譯中」，不是就不標完成。

### 前台必須是 Notion 最新同步的版本

翻譯讀的是前台已發佈頁面。同步到已發佈文章時，新內容只寫進 Elementor 草稿（autosave），
前台維持舊版，要人到 WP 發佈才會換。2026-09-11 實測 6074：前台最後修改 06-01、同步草稿
是當天，前台只有 16 段、Notion 有 146 個區塊——這時翻譯翻的是舊內容，術語閘門也只看得到
舊內容裡的詞。所以有比前台更新的同步草稿就擋下，請人先發佈。

### 術語閘門用的是同步時同一套（converter/term_check.py）

範圍是整篇的區塊，跟同步時的「術語檢查」欄算的一致——不然同步說 OK、翻譯卻擋下
（或反過來），使用者無從判斷該信哪個。

## 整句列必須由我們產生（2026-09-09 重寫的理由，仍然成立）

TP 自動登錄的一律是片段（block_type=0，以行內元素邊界切分）；整句列只有人在 TP
編輯器上升到外層才會生成，而且一生成就已是 status=2。所以「撈 status=0 來翻」
翻到的**只可能**是殘句。整句的原文取自已發佈頁面的區塊 innerHTML（tp_blocks），
寫回 /tp/block。**人工精修過（status=2）的永不覆蓋**，端點與 pending_blocks 兩邊都擋。

過濾基準要用**全站**人工譯文：post_id 篩選只看得到有掛 post_parent_id 的列
（post 7251 有三段明明是這篇卻沒關聯），端點的比對又是全站的 WHERE original = ?。

## 只用「已確認」的詞進 prompt

術語表規則「未打勾＝草稿，不可作為翻譯依據」。閘門保證翻譯時整篇沒有待確認的詞，
prompt 仍然只放已確認的列。

## 用法

    python scripts/build_translate_workflow.py              # 測試站
    python scripts/build_translate_workflow.py --model gpt-5.6-terra

webhook path 取自 .env 的 N8N_TRANSLATE_WEBHOOK_PATH_TEST（不入庫；產物寫到已 gitignore
的 n8n/local/）。沒設會用佔位字串並提示。
"""
import argparse
import json
import pathlib
import re
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONVERTER = ROOT / "converter"
SAMPLES = ROOT / "samples" / "tp-style-samples.json"
OUT_DIR = ROOT / "n8n" / "local"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import error_codes as ec  # noqa: E402
import wp_env  # noqa: E402

_ID_NS = uuid.UUID("7f3a1c02-5d64-4e8b-9a11-2c6d0f4b7e93")

# n8n 憑證「引用」——只有識別碼，不含任何密鑰
# （CLAUDE.md：匯出時確認憑證欄位為引用而非明文）
OPENAI_CRED = {"id": "UEtEu6Jad1QJoQvz", "name": "OpenAi account 2"}
NOTION_CRED = {"id": "xfGHH7Wx4EucMC0X", "name": "Support Center Sync"}
WEBHOOK_AUTH_CRED = {"id": "8rnHKnTbrXTDCzwc", "name": "Header Auth"}   # 與同步按鈕共用
WP_CRED = {
    "test": {"id": "oIyDk22ZdtDbphHm", "name": "WordPress Credential (Sandbox)"},
    "prod": {"id": "7yIBiKpBdDB40C4I", "name": "WordPress Credential (Production)"},
}
WP_BASE = {"test": "https://support.synctify.io",
           "prod": "https://support.synctify.net"}
POST_ID_PROP = {"test": "WP Post ID (Test)", "prod": "WP Post ID"}
WEBHOOK_ENV = {"test": "N8N_TRANSLATE_WEBHOOK_PATH_TEST", "prod": "N8N_TRANSLATE_WEBHOOK_PATH"}
WEBHOOK_PLACEHOLDER = "synctify-translate-CHANGE-ME-TO-A-RANDOM-STRING"

TRANSLATE_STATUS_PROP = "翻譯狀態"
TERM_CHECK_PROP = "術語檢查"

# 產品用術語表。查詢用的 database id，不是 collection id
# （後者查會回 404，而訊息是誤導性的「請與 integration 分享」）。
GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"
GLOSSARY_URL = "https://app.notion.com/p/3bc2f2ede27d81238c4fd63c958ac9fc"

# 心柔 2026-09-09 選定。八題裡 terra 拿 4 票（sol 2、luna 2），而且是唯一
# 沒有正確性錯誤的。成本沒有進入決定：2,058 條字串全部翻完 terra ≈ $4.87。
DEFAULT_MODEL = "gpt-5.6-terra"

PREP = "抽區塊＋術語閘門＋組 prompt"
LLM = "OpenAI：翻譯"
COLLECT = "整理譯文"
SUMMARY = "彙整寫入結果"
REASON_FAIL = "原因：節點失敗"
PAGE = "$('解析 page_id').first().json.page_id"   # 失敗路徑也要用得到，所以取最早的節點


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


def _module(name):
    """讀 converter 模組，去掉 __main__ 區塊。"""
    src = (CONVERTER / name).read_text(encoding="utf-8")
    return re.split(r'^if __name__ == "__main__":', src, flags=re.M)[0]


PREP_ADAPTER = r'''

# ─── n8n 轉接層 ───
_SAMPLES = __SAMPLES__
_GLOSSARY_URL = __GLOSSARY_URL__

# 上游是 Merge（append），四條線：頁面 HTML、該篇字典現況、全站人工譯文、術語表。
# **依形狀分辨**而不是依順序——Merge 的輸出順序會隨各分支回應速度改變。
_html = ''
_existing = []
_pages = []
for _it in _items:
    _j = _it['json']
    if isinstance(_j.get('results'), list):
        _pages.extend(_j['results'])
    elif isinstance(_j.get('items'), list):
        _existing.extend(_j['items'])
    else:
        for _k in ('data', 'body', 'html'):
            _v = _j.get(_k)
            if isinstance(_v, str) and '<' in _v and len(_v) > len(_html):
                _html = _v

# 術語表全部列（含草稿、沒簡中的）給閘門用；prompt 只放已確認且有簡中的
_rows = glossary_from_notion(_pages)
_gloss = [{'en': _r['en'], 'zh': _r['zh']} for _r in _rows if _r['ok'] and _r['zh']]

if not _gloss:
    raise ValueError('已確認的術語是 0 筆——確認「Notion：取產品術語表」有回應、分頁有生效，'
                     '以及該資料庫已與 integration 分享')
if not _html:
    raise ValueError('沒有拿到頁面 HTML——確認「抓頁面 HTML」節點的 Response Format 設為 text')

# 文章 ID 從頁面自己認；寫入前「整理譯文」會跟「參數」的 post_id 比對
_POST_ID = detect_post_id(_html)
if _POST_ID is None:
    raise ValueError('頁面上找不到文章內容容器（data-elementor-id）。分類首頁沒有這個容器，'
                     '抽取範圍會退回整頁，翻到的是側邊欄與頁首頁尾')

# ── 術語閘門 ──
# 跟同步時的「術語檢查」同一套（term_check），範圍也同樣是整篇——兩邊算法一致，
# 使用者才不會遇到「同步說可以翻、按翻譯卻被擋」。
_tc = check([_b['original'] for _b in extract_blocks(_html, _POST_ID)], _rows, when='翻譯前')
if _tc['pending']:
    return [{'json': {
        'gate_blocked': True,
        'post_id': _POST_ID,
        'term_check': _tc,
        'term_summary': _tc['summary'],
        'term_comment': [
            {'type': 'text', 'text': {'content': '翻譯沒有開始'}, 'annotations': {'bold': True}},
            {'type': 'text', 'text': {'content': '：還有詞沒確認，處理完再按一次「翻譯」。\n\n'}},
        ] + comment_rich_text(_tc, _GLOSSARY_URL),
    }}]

_res = pending_blocks(_html, _POST_ID, _existing)

if not _res['pending']:
    # 整篇都已人工精修是正常結果，但要說出來，否則下游看到空輸入會以為壞掉
    return [{'json': {'nothing_to_do': True,
                      'post_id': _POST_ID,
                      'term_summary': _tc['summary'],
                      'stat_total': _res['total_blocks'],
                      'stat_already_human': _res['already_human'],
                      'stat_glossary': len(_gloss)}}]

_out = []
for _b in _res['pending']:
    _en = _b['original']
    _sys, _usr = build_prompt(_en, _gloss, _SAMPLES)
    _out.append({'json': {
        'post_id': _POST_ID,
        'original': _en,
        'tag': _b['tag'],
        'has_inline': _b['has_inline'],
        'system': _sys,
        'user': _usr,
        'terms': [{'en': _e, 'zh': _z} for _e, _z in find_terms(_en, _gloss)],
        'term_summary': _tc['summary'],
        # 診斷數字每一筆都帶著，隨便點開一筆都看得到整體狀況
        'stat_total': _res['total_blocks'],
        'stat_already_human': _res['already_human'],
        'stat_pending': len(_res['pending']),
        'stat_glossary': len(_gloss),
        'stat_notion_residue': len(_res['notion_residue']),
    }})
return _out
'''


def prep_code():
    """抽區塊＋術語閘門＋組 prompt 的 Python code node。

    打包 tp_blocks、translate_prompt、term_check 三個模組，都只需要 `re`。
    ⚠️ 三者共用同一個命名空間，頂層名稱不可重複（2026-09-11 查到 tp_blocks 與
    term_check 都有 _TAG_RE，已改名）——converter/test_term_check.py 有測試把關。
    term_check 的 `from translate_prompt import` 要拿掉：同一個檔案裡已經有定義。
    """
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]
    header = (
        "# " + "=" * 66 + "\n"
        "#  自動產生，請勿直接編輯\n"
        "#  來源：converter/tp_blocks.py + translate_prompt.py + term_check.py\n"
        "#        + samples/tp-style-samples.json\n"
        "#  重新產生：./.venv/bin/python scripts/build_translate_workflow.py\n"
        "# " + "=" * 66 + "\n"
    )
    tc_src = re.sub(r"^from translate_prompt import .*$", "", _module("term_check.py"), flags=re.M)
    body = (_module("tp_blocks.py") + "\n\n" + _module("translate_prompt.py")
            + "\n\n" + tc_src)
    adapter = (PREP_ADAPTER
               .replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False))
               .replace("__GLOSSARY_URL__", json.dumps(GLOSSARY_URL)))
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

// 抽取範圍用的是「頁面自己認出的文章 ID」，寫入用的是「參數」節點的 post_id（取自
// Notion 母列）。兩者不一致＝抓到的頁面不是要翻的那篇，硬寫會把 A 篇的譯文掛到 B 篇。
const wantId = Number($('參數').first().json.post_id);
const gotId = Number(prep[0].json.post_id);
if (wantId !== gotId) {
  throw new Error('Notion 母列的 WP Post ID 是 ' + wantId + '，但抓到的頁面是文章 ' + gotId +
                  '，不寫入');
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
  if (!text) { warn.push('「' + p.original.replace(/<[^>]+>/g, '').slice(0, 40) + '…」沒有譯文'); continue; }
  if (tagSig(text) !== tagSig(p.original)) {
    warn.push('「' + p.original.replace(/<[^>]+>/g, '').slice(0, 40) + '…」標籤結構被改動');
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


SUMMARY_JS = """
// 把 /tp/block 的逐段結果收成一句話，並做最後的核對。
const prep = $('PREP_NODE_NAME').first().json;
const bold = t => ({ type: 'text', text: { content: t }, annotations: { bold: true } });
const plain = t => ({ type: 'text', text: { content: t } });

if (prep.nothing_to_do) {
  return [{ json: { written: 0, comment: [bold('翻譯完成'),
    plain('：整篇都已有人工譯文（' + prep.stat_already_human + ' 段），沒有需要機器翻譯的段落。')] } }];
}

const collected = $('COLLECT_NODE_NAME').first().json;
const results = $input.first().json.results || [];
const count = a => results.filter(r => r.action === a).length;

// 端點回 200 不代表每段都寫進去了——逐段的 failed 要自己看
const failed = results.filter(r => r.action === 'failed');
if (failed.length) {
  throw new Error('寫入時有 ' + failed.length + ' 段失敗：' + JSON.stringify(failed.slice(0, 3)));
}
if (results.length !== collected.items.length) {
  throw new Error('送出 ' + collected.items.length + ' 段、端點回報 ' + results.length + ' 段，數量對不上');
}

const created = count('created'), updated = count('updated');
// 抽取時就扣掉的人工譯文，加上寫入瞬間端點才發現已是人工的
const human = (collected.stats.already_human || 0) + count('skipped_human');
let text = '：寫入 ' + (created + updated) + ' 段（新增 ' + created + '、更新 ' + updated +
           '）；已有人工譯文、沒有重翻 ' + human + ' 段。';
const warn = collected.warnings || [];
if (warn.length) {
  text += '\\n\\n⚠️ 以下段落請到測試站確認：\\n' +
          warn.slice(0, 10).map(w => '• ' + w).join('\\n') +
          (warn.length > 10 ? '\\n…等 ' + warn.length + ' 段' : '');
}
return [{ json: { written: created + updated, created, updated, human, warnings: warn.length,
                  comment: [bold('翻譯完成'), plain(text.slice(0, 1900))] } }];
""".strip()


FRESH_JS = """
// 前台是不是 Notion 最新同步的版本？翻譯讀的是前台已發佈頁面。
// 同步到已發佈文章時，新內容只寫進 Elementor 草稿（autosave），前台維持舊版——
// 2026-09-11 實測 6074：前台最後修改 06-01、同步草稿是當天，前台 16 段、Notion 146 個區塊。
const post = $('WP：取文章網址').first().json;
const autos = $input.all().map(i => i.json).filter(a => a && a.modified_gmt);
const newer = autos.filter(a => a.modified_gmt > (post.modified_gmt || ''))
                   .sort((a, b) => (a.modified_gmt < b.modified_gmt ? 1 : -1));
const fmt = t => String(t || '').replace('T', ' ').slice(0, 16) + ' UTC';
let reason = '';
if (post.status !== 'publish') {
  reason = '文章在SITE還不是「已發佈」（目前是「' + (post.status || '不明') + '」）。' +
           '翻譯要讀已發佈頁面，請先在 WP 發佈再按「翻譯」。';
} else if (newer.length) {
  reason = '最新同步的內容還沒在 WP 發佈（同步草稿 ' + fmt(newer[0].modified_gmt) +
           '，前台版本 ' + fmt(post.modified_gmt) + '），現在翻譯會翻到舊內容。' +
           '請先在 WP 發佈（Elementor → History → Revisions → Apply → Publish），再按「翻譯」。';
}
return [{ json: { ok: !reason, reason: reason, id: post.id, link: post.link } }];
""".strip()


GUARD_JS = """
// 能不能翻：按對列了嗎？母列有沒有測試站的 WP Post ID？
// 跟同步的防呆同一套判斷——用「深度」而不是命名或 Status（見 notion-content-hub-schema）。
const row = $('取出本列資訊').first().json;
const mother = $input.first().json;
const props = mother.properties || {};
const pidText = (((props['POST_ID_PROP'] || {}).rich_text || [])[0] || {}).plain_text || '';
const pid = pidText.trim();
let reason = '';
if (row.is_mother) {
  reason = '這是母列。請在版本子列（例如「- v2」那一列）上按「翻譯」。';
} else if (((props['Parent item'] || {}).relation || []).length) {
  reason = '這一列在草稿層（(Draft)），不能翻譯。請在版本子列上按。';
} else if (!/^\\d+$/.test(pid)) {
  reason = '母列還沒有「POST_ID_PROP」——請先按「同步到 WP」並在 WP 發佈後再翻譯。';
}
return [{ json: { ok: !reason, reason: reason, post_id: pid ? Number(pid) : null } }];
""".strip()


def build(target, model, webhook_path):
    wp = WP_BASE[target]
    cred = WP_CRED[target]
    post_prop = POST_ID_PROP[target]
    site = "測試站" if target == "test" else "正式站"
    n = lambda k: det(target, "v3", k)

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

    def notion_node(key, name, pos, params, **extra):
        node = {"parameters": params, "id": n(key), "name": name,
                "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": pos,
                "credentials": {"notionApi": NOTION_CRED}}
        node.update(extra)
        return node

    def if_node(key, name, pos, left, op, right=None, notes=None):
        cond = {"id": n(key + "-c"), "leftValue": left, "operator": op}
        if right is not None:
            cond["rightValue"] = right
        node = {"parameters": {"conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
                    "conditions": [cond], "combinator": "and"}, "options": {}},
                "id": n(key), "name": name, "type": "n8n-nodes-base.if",
                "typeVersion": 2.2, "position": pos}
        if notes:
            node["notes"] = notes
        return node

    TRUE = {"type": "boolean", "operation": "true", "singleValue": True}

    def comment_body(rich_expr):
        return "={{ { parent: { page_id: " + PAGE + " }, rich_text: " + rich_expr + " } }}"

    def status_body(status, term_expr=None):
        props = "'" + TRANSLATE_STATUS_PROP + "': { select: { name: '" + status + "' } }"
        if term_expr is not None:
            props += (", '" + TERM_CHECK_PROP + "': { rich_text: [ { text: { content: String("
                      + term_expr + " || '') } } ] }")
        return "={{ { properties: { " + props + " } } }}"

    nodes = [
        # ── 觸發與取列 ────────────────────────────────────────────────
        {"parameters": {"httpMethod": "POST", "path": webhook_path,
                        "responseMode": "onReceived", "authentication": "headerAuth",
                        "options": {}},
         "id": n("webhook"), "name": "Notion 翻譯按鈕（Webhook）",
         "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 400],
         "webhookId": n("webhook-id"),
         "credentials": {"httpHeaderAuth": WEBHOOK_AUTH_CRED},
         "notes": "Notion Content Hub 的「翻譯" + ("" if target == "prod" else " (Test)") + "」按鈕 → Send webhook。\n\n"
                  "【按鈕的動作順序】（Fay 2026-09-11）\n"
                  "1. Edit property：翻譯狀態 → 翻譯中\n"
                  "   ⚠️ 不要順便清空「術語檢查」：被擋在前面（尚無法開始翻譯）時還沒跑術語檢查，\n"
                  "   沒有結果可以寫回，欄位就一直是空的（Fay 2026-09-11 實際遇到）。\n"
                  "   術語檢查欄由同步產生；翻譯只在跑過檢查時（待術語確認／已翻譯完成）更新它。\n"
                  "2. Send webhook：網址用這個節點的 Production URL，\n"
                  "   Add custom header 填與「同步到 WP」按鈕同一組（共用 Header Auth 憑證）\n\n"
                  "先改屬性，按下的瞬間就看得到「翻譯中」——翻一篇要十幾分鐘，\n"
                  "不這樣做，按的人會以為沒反應而重按（重按會多花一次 API 費用）。\n\n"
                  "path 取自 .env 的 " + WEBHOOK_ENV[target] + "（不入庫）。"},

        {"parameters": {"assignments": {"assignments": [
            {"id": n("pid"), "name": "page_id",
             "value": "={{ ($json.body?.data?.id ?? $json.body?.page?.id ?? "
                      "$json.body?.id ?? $json.query?.page_id ?? '')"
                      ".toString().replace(/-/g, '') }}",
             "type": "string"},
        ]}, "options": {}},
         "id": n("parse"), "name": "解析 page_id", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [220, 400],
         "notes": "與同步按鈕同一套取值路徑，也支援 ?page_id= 手動測試。"},

        if_node("has-pid", "取得到 page_id？", [440, 400], "={{ $json.page_id }}",
                {"type": "string", "operation": "notEmpty", "singleValue": True}),

        {"parameters": {}, "id": n("no-pid"), "name": "payload 無 page_id（結束）",
         "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [660, 560],
         "notes": "webhook payload 結構與預期不符。打開這次執行的 webhook 節點輸出，\n"
                  "照實際結構修正「解析 page_id」。（此時翻譯狀態會停在「翻譯中」，重按即可。）"},

        notion_node("row", "Notion：取得該列", [660, 400],
                    notion_http("GET", "=https://api.notion.com/v1/pages/{{ $json.page_id }}")),

        {"parameters": {"assignments": {"assignments": [
            {"id": n("r-page"), "name": "page_id",
             "value": "={{ $json.id.replace(/-/g, '') }}", "type": "string"},
            {"id": n("r-name"), "name": "doc_name",
             "value": "={{ $json.properties['Doc name']?.title?.[0]?.plain_text ?? '' }}",
             "type": "string"},
            {"id": n("r-mother"), "name": "mother_id",
             "value": "={{ ($json.properties['Parent item']?.relation?.[0]?.id ?? $json.id)"
                      ".replace(/-/g, '') }}", "type": "string"},
            {"id": n("r-is-mother"), "name": "is_mother",
             "value": "={{ !($json.properties['Parent item']?.relation?.length) }}",
             "type": "boolean"},
        ]}, "options": {}},
         "id": n("pick"), "name": "取出本列資訊", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [880, 400]},

        notion_node("mother", "Notion：取得母列", [1100, 400],
                    notion_http("GET", "=https://api.notion.com/v1/pages/"
                                "{{ $('取出本列資訊').first().json.mother_id }}"),
                    notes="WP Post ID 記在母列（整篇文章的穩定身分）。"),

        {"parameters": {"jsCode": GUARD_JS.replace("POST_ID_PROP", post_prop)},
         "id": n("guard"), "name": "檢查能不能翻", "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [1320, 400]},

        if_node("can", "可以翻？", [1540, 400], "={{ $json.ok }}", TRUE),

        # ── 不能翻：「尚無法開始翻譯」＋留言原因（不是失敗，不標 ❌）───────
        # Fay 2026-09-11：原本改回「－」，看起來像回到原點、不知道發生什麼事。
        # 也不用 ❌ 翻譯失敗：那是程式出錯要查原因；這裡是還差一步（沒發佈、按錯列），
        # 照留言做完再按就好。兩者混用，看到 ❌ 會以為壞掉。

        notion_node("guard-comment", "Notion：留言翻譯未開始", [2640, 780],
                    notion_http("POST", "https://api.notion.com/v1/comments", comment_body(
                        "[ { text: { content: '翻譯沒有開始' }, annotations: { bold: true } },"
                        " { text: { content: '：' + $json.reason } } ]")),
                    executeOnce=True,
                    notes="留言要直接接在原因節點後面：$json 指上一個節點的輸出，\n"
                          "隔著 PATCH 就讀不到 reason（同步工作流 2026-08-11 踩過）。"),

        notion_node("guard-status", "Notion：回寫翻譯未開始", [2860, 780],
                    notion_http("PATCH", "=https://api.notion.com/v1/pages/{{ " + PAGE + " }}",
                                status_body("尚無法開始翻譯")),
                    executeOnce=True,
                    notes="按鈕已先把狀態改成「翻譯中」，沒開始就要改掉，不然會一直顯示翻譯中。\n"
                          "寫「尚無法開始翻譯」而不是「－」：回到「－」看不出剛剛被擋下過。"),

        # ── 取資料 ────────────────────────────────────────────────────
        {"parameters": {"assignments": {"assignments": [
            {"id": n("p-post"), "name": "post_id", "value": "={{ $json.post_id }}", "type": "number"},
            {"id": n("p-lang"), "name": "language", "value": "zh_CN", "type": "string"},
            {"id": n("p-model"), "name": "model", "value": model, "type": "string"},
            {"id": n("p-dry"), "name": "dry_run", "value": False, "type": "boolean"},
        ]}, "options": {}},
         "id": n("params"), "name": "參數", "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [1760, 300],
         "notes": "post_id 取自 Notion 母列。\n\n"
                  "**model 必須真的驅動 OpenAI 節點**：那個節點的模型欄用 By ID＋運算式。\n\n"
                  "dry_run 直接傳給 /tp/block。按鈕版一律真的寫入；要試跑可暫時改成 true，\n"
                  "端點會只回報會做什麼（收尾統計會是 0 段）。"},

        {"parameters": {
            "url": "=" + wp + "/wp-json/wp/v2/docs/{{ $json.post_id }}?_fields=id,link,title,status,modified_gmt",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("url"), "name": "WP：取文章網址",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1980, 300],
         "notes": "要抓的是已發佈頁面的渲染 HTML（content.rendered 會漂移），所以先問網址。\n"
                  "status 一起取：草稿的 link 是預覽網址，抓不到渲染結果。"},

        {"parameters": {
            "url": "=" + wp + "/wp-json/wp/v2/docs/{{ $json.id }}/autosaves?_fields=id,modified_gmt",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("autosaves"), "name": "WP：查同步草稿",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2200, 160],
         "alwaysOutputData": True,
         "notes": "同步到已發佈文章時，新內容只寫進 Elementor 草稿（autosave），前台不變。\n"
                  "autosave 掛在同步用的 WP 帳號名下，這裡用同一組憑證查得到。\n"
                  "alwaysOutputData：沒有草稿時 API 回空陣列，不開的話下游整條不會執行。"},

        {"parameters": {"jsCode": FRESH_JS.replace("SITE", site)},
         "id": n("fresh"), "name": "檢查前台是不是最新版", "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [2200, 300]},

        if_node("fresh-ok", "前台是最新版？", [2200, 440], "={{ $json.ok }}", TRUE),

        {"parameters": {
            "url": "={{ $json.link }}",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "options": {"response": {"response": {"responseFormat": "text"}}}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("html"), "name": "抓頁面 HTML",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2420, 100],
         "notes": "**Response Format 必須是 text。** tp_blocks 會把範圍限縮在 Elementor\n"
                  "內容容器：整頁 143k、內容區 44k，不限縮會撈到側邊欄與頁首頁尾。"},

        {"parameters": {
            "url": wp + "/wp-json/synctify/v1/tp/strings",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "language", "value": "={{ $('參數').first().json.language }}"},
                {"name": "post_id", "value": "={{ $('參數').first().json.post_id }}"},
                {"name": "limit", "value": "500"},
            ]},
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("existing"), "name": "WP：取該篇字典現況",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2420, 240],
         "notes": "不篩 status，由 pending_blocks 決定：只有 status=2 才算完成，0/1 會重送。\n"
                  "**post_id 一定要傳**，不傳就是全站清單。"},

        {"parameters": {
            "url": wp + "/wp-json/synctify/v1/tp/strings",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "sendQuery": True,
            "queryParameters": {"parameters": [
                {"name": "language", "value": "={{ $('參數').first().json.language }}"},
                {"name": "status", "value": "2"},
                {"name": "limit", "value": "500"},
            ]},
            "options": {"pagination": {"pagination": {
                "paginationMode": "updateAParameterInEachRequest",
                "parameters": {"parameters": [
                    {"type": "qs", "name": "offset", "value": "={{ $pageCount * 500 }}"}]},
                "paginationCompleteWhen": "other",
                "completeExpression": "={{ ($response.body.items || []).length < 500 }}",
                "limitPagesFetched": True, "maxRequestsF": 10}}}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("human"), "name": "WP：取全站人工譯文",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2420, 380],
         "notes": "過濾基準要用全站：post_id 篩選只看得到有掛 post_parent_id 的列，\n"
                  "端點的比對又是全站的 WHERE original = ?。**要分頁**（上限 500）。"},

        {"parameters": {
            "method": "POST",
            "url": "https://api.notion.com/v1/databases/" + GLOSSARY_DB + "/query",
            "authentication": "predefinedCredentialType", "nodeCredentialType": "notionApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Notion-Version", "value": "2022-06-28"}]},
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
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2420, 520],
         "notes": "**要分頁**（上限 100）。這個 HTTP 分頁設定在翻譯工作流實跑正常。\n"
                  "（同步工作流 2026-09-11 同一份設定加上 executeOnce 後回「JSON Body 不是\n"
                  "合法 JSON」，那邊改用 Notion 節點——這裡沒有 executeOnce，照舊。）"},

        {"parameters": {"mode": "append", "numberInputs": 4},
         "id": n("merge"), "name": "合流", "type": "n8n-nodes-base.merge",
         "typeVersion": 3, "position": [2640, 300],
         "notes": "四條線：頁面 HTML、該篇字典現況、全站人工譯文、術語表。\n"
                  "下游**依形狀分辨**而不是依順序。"},

        {"parameters": {"language": "pythonNative", "pythonCode": prep_code()},
         "id": n("prep"), "name": PREP, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [2860, 300],
         "notes": "自動產生，勿直接編輯——改 converter/ 的模組後重新跑產生器。\n\n"
                  "術語閘門：整篇有待確認的詞就只輸出一筆 gate_blocked，不翻。\n"
                  "抽的是「整句」，不是 TP 的片段。"},

        # ── 術語閘門擋下：待術語確認＋留言清單（不是失敗）────────────────
        if_node("gate", "術語沒確認？（術語閘門）", [3080, 300], "={{ $json.gate_blocked === true }}", TRUE),

        notion_node("gate-status", "Notion：回寫待術語確認", [3300, 120],
                    notion_http("PATCH", "=https://api.notion.com/v1/pages/{{ " + PAGE + " }}",
                                status_body("待術語確認", "$json.term_summary"))),

        notion_node("gate-comment", "Notion：留言術語未確認", [3520, 120],
                    notion_http("POST", "https://api.notion.com/v1/comments",
                                comment_body("$('" + PREP + "').first().json.term_comment")),
                    notes="清單格式與同步時的術語檢查留言相同（term_check.comment_rich_text），\n"
                          "前面多一句「翻譯沒有開始」。"),

        # ── 翻譯與寫入 ────────────────────────────────────────────────
        if_node("todo", "有要翻的段落？", [3300, 400], "={{ $json.nothing_to_do !== true }}", TRUE,
                notes="整篇都已人工精修時，直接走收尾（照樣標已翻譯完成）。"),

        {"parameters": {
            "modelId": {"__rl": True, "mode": "id", "value": "={{ $('參數').first().json.model }}"},
            "messages": {"values": [
                {"role": "system", "content": "={{ $json.system }}"},
                {"content": "={{ $json.user }}"},
            ]},
            "options": {}},
         "credentials": {"openAiApi": OPENAI_CRED},
         "id": n("llm"), "name": LLM, "type": "@n8n/n8n-nodes-langchain.openAi",
         "typeVersion": 1.8, "position": [3520, 400],
         "notes": "模型欄用 By ID＋運算式，值取自「參數」。用下拉選單會把型號寫死在這裡。\n"
                  "逐段送出，一篇 150 段約十幾分鐘——這就是按鈕要先顯示「翻譯中」的原因。"},

        {"parameters": {"jsCode": COLLECT_JS.replace("PREP_NODE_NAME", PREP)},
         "id": n("collect"), "name": COLLECT, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [3740, 400],
         "notes": "block_type 固定 1。配對靠位置，數量對不上就中止。"},

        {"parameters": {
            "method": "POST", "url": wp + "/wp-json/synctify/v1/tp/block",
            "authentication": "genericCredentialType", "genericAuthType": "httpBasicAuth",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { \"language\": $('參數').first().json.language, "
                        "\"post_id\": $('參數').first().json.post_id, "
                        "\"dry_run\": $('參數').first().json.dry_run, "
                        "\"items\": $json.items } }}",
            "options": {}},
         "credentials": {"httpBasicAuth": cred},
         "id": n("write"), "name": "WP：寫回整句列",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [3960, 400],
         "notes": "寫整句列（block_type=1），端點同時處理三張表。\n"
                  "status 一律寫 1（機器翻譯），**已是 status=2 的列永不覆蓋**。"},

        {"parameters": {"jsCode": SUMMARY_JS.replace("PREP_NODE_NAME", PREP)
                                            .replace("COLLECT_NODE_NAME", COLLECT)},
         "id": n("summary"), "name": SUMMARY, "type": "n8n-nodes-base.code",
         "typeVersion": 2, "position": [4180, 400],
         "notes": "端點回 200 不代表每段都寫進去——逐段的 failed 與數量都在這裡核對，\n"
                  "不對就丟錯走失敗路徑，不會標成已翻譯完成。"},

        # ── 收尾：確認沒被同步打斷 ─────────────────────────────────────
        notion_node("reread", "Notion：再讀一次該列", [4400, 400],
                    notion_http("GET", "=https://api.notion.com/v1/pages/{{ " + PAGE + " }}"),
                    notes="翻譯要十幾分鐘。期間若有人按了同步，同步按鈕會把翻譯狀態改回「－」，\n"
                          "文章內容也可能改了——這時標「已翻譯完成」就是在說謊。"),

        if_node("still", "還在翻譯中？", [4620, 400],
                "={{ $json.properties['" + TRANSLATE_STATUS_PROP + "']?.select?.name }}",
                {"type": "string", "operation": "equals"}, right="翻譯中"),

        notion_node("done-status", "Notion：回寫已翻譯完成", [4840, 300],
                    notion_http("PATCH", "=https://api.notion.com/v1/pages/{{ " + PAGE + " }}",
                                status_body("已翻譯完成", "$('" + PREP + "').first().json.term_summary"))),

        notion_node("done-comment", "Notion：留言翻譯完成", [5060, 300],
                    notion_http("POST", "https://api.notion.com/v1/comments",
                                comment_body("$('" + SUMMARY + "').first().json.comment"))),

        notion_node("interrupted", "Notion：留言翻譯被打斷", [4840, 520],
                    notion_http("POST", "https://api.notion.com/v1/comments", comment_body(
                        "[ { text: { content: '譯文已寫入，但沒有標成完成' }, annotations: { bold: true } },"
                        " { text: { content: '：翻譯期間這一列的翻譯狀態被改成「' + "
                        "($json.properties['" + TRANSLATE_STATUS_PROP + "']?.select?.name || '空白') + "
                        "'」（多半是有人按了同步）。文章可能已經改過，請確認術語檢查後再按一次「翻譯」。\\n\\n' } } ]"
                        ".concat([ { text: { content: '寫入結果' }, annotations: { bold: true } } ],"
                        " $('" + SUMMARY + "').first().json.comment.slice(1))")),
                    notes="不動翻譯狀態：同步已經把它設成正確的值（－ 或待術語確認）。"),

        # ── 失敗路徑：❌ 翻譯失敗＋一則留言 ──────────────────────────────
        {"parameters": {"assignments": {"assignments": [
            {"id": n("f-raw"), "name": "fail_raw", "type": "string",
             "value": "={{ typeof $json.error === 'string' ? $json.error : JSON.stringify($json.error ?? $json).slice(0, 800) }}"},
            {"id": n("f-reason"), "name": "fail_reason", "type": "string",
             "value": "={{ " + ec.to_reason_js() + "("
                      "typeof $json.error === 'string' ? $json.error"
                      " : ($json.error && $json.error.message ? $json.error.message"
                      " : JSON.stringify($json).slice(0, 800))) }}"},
            {"id": n("f-node"), "name": "fail_node", "type": "string",
             "value": "={{ $prevNode.name }}"},
        ]}, "options": {}},
         "id": n("fail"), "name": REASON_FAIL, "type": "n8n-nodes-base.set",
         "typeVersion": 3.4, "position": [2860, 900], "executeOnce": True,
         "notes": "各節點的錯誤輸出都接到這裡。executeOnce：OpenAI 逐段失敗時錯誤也是逐段的，\n"
                  "不設會一段留一則言（同步工作流 2026-09-11 一次留了 146 則）。"},

        notion_node("fail-comment", "Notion：留言翻譯失敗", [3080, 900],
                    notion_http("POST", "https://api.notion.com/v1/comments", comment_body(
                        "[ { text: { content: '翻譯失敗' }, annotations: { bold: true } },"
                        " { text: { content: ($json.fail_node ? '（卡在「' + $json.fail_node + '」）' : '')"
                        " + '：' + ($json.fail_reason ?? $json.fail_raw ?? '') + '\\n\\n處理後可以直接再按一次「翻譯」'"
                        " + '——已寫入的段落會被更新，不會重複新增。' } } ]")),
                    executeOnce=True),

        notion_node("fail-status", "回寫：翻譯失敗", [3300, 900],
                    notion_http("PATCH", "=https://api.notion.com/v1/pages/{{ " + PAGE + " }}",
                                status_body("❌ 翻譯失敗")),
                    executeOnce=True),

        {"parameters": {"errorMessage": "=翻譯未完成：{{ $('" + REASON_FAIL + "').first().json.fail_reason }}"},
         "id": n("stop"), "name": "標記本次執行失敗", "type": "n8n-nodes-base.stopAndError",
         "typeVersion": 1, "position": [3520, 900],
         "notes": "讓 Executions 顯示為失敗。Notion 的狀態與留言在這之前已經寫完。"},
    ]

    def to(name, index=0):
        return {"node": name, "type": "main", "index": index}

    conns = {
        "Notion 翻譯按鈕（Webhook）": {"main": [[to("解析 page_id")]]},
        "解析 page_id": {"main": [[to("取得到 page_id？")]]},
        "取得到 page_id？": {"main": [[to("Notion：取得該列")], [to("payload 無 page_id（結束）")]]},
        "Notion：取得該列": {"main": [[to("取出本列資訊")]]},
        "取出本列資訊": {"main": [[to("Notion：取得母列")]]},
        "Notion：取得母列": {"main": [[to("檢查能不能翻")]]},
        "檢查能不能翻": {"main": [[to("可以翻？")]]},
        "可以翻？": {"main": [[to("參數")], [to("Notion：留言翻譯未開始")]]},
        "Notion：留言翻譯未開始": {"main": [[to("Notion：回寫翻譯未開始")]]},
        "參數": {"main": [[to("WP：取文章網址")]]},
        "WP：取文章網址": {"main": [[to("WP：查同步草稿")]]},
        "WP：查同步草稿": {"main": [[to("檢查前台是不是最新版")]]},
        "檢查前台是不是最新版": {"main": [[to("前台是最新版？")]]},
        "前台是最新版？": {"main": [[to("抓頁面 HTML"), to("WP：取該篇字典現況"),
                                      to("WP：取全站人工譯文"), to("Notion：取產品術語表")],
                                     [to("Notion：留言翻譯未開始")]]},
        "抓頁面 HTML": {"main": [[to("合流", 0)]]},
        "WP：取該篇字典現況": {"main": [[to("合流", 1)]]},
        "WP：取全站人工譯文": {"main": [[to("合流", 2)]]},
        "Notion：取產品術語表": {"main": [[to("合流", 3)]]},
        "合流": {"main": [[to(PREP)]]},
        PREP: {"main": [[to("術語沒確認？（術語閘門）")]]},
        "術語沒確認？（術語閘門）": {"main": [[to("Notion：回寫待術語確認")], [to("有要翻的段落？")]]},
        "Notion：回寫待術語確認": {"main": [[to("Notion：留言術語未確認")]]},
        "有要翻的段落？": {"main": [[to(LLM)], [to(SUMMARY)]]},
        LLM: {"main": [[to(COLLECT)]]},
        COLLECT: {"main": [[to("WP：寫回整句列")]]},
        "WP：寫回整句列": {"main": [[to(SUMMARY)]]},
        SUMMARY: {"main": [[to("Notion：再讀一次該列")]]},
        "Notion：再讀一次該列": {"main": [[to("還在翻譯中？")]]},
        "還在翻譯中？": {"main": [[to("Notion：回寫已翻譯完成")], [to("Notion：留言翻譯被打斷")]]},
        "Notion：回寫已翻譯完成": {"main": [[to("Notion：留言翻譯完成")]]},
        REASON_FAIL: {"main": [[to("Notion：留言翻譯失敗")]]},
        "Notion：留言翻譯失敗": {"main": [[to("回寫：翻譯失敗")]]},
        "回寫：翻譯失敗": {"main": [[to("標記本次執行失敗")]]},
    }

    # 每個可能失敗的節點都開錯誤輸出，統一接到「原因：節點失敗」。
    # 不含 IF（第二個輸出是正常分支）與失敗路徑本身（會造成迴圈）。
    # 也不放任何 continueRegularOutput 的節點在它們上游：錯誤 item 流進有錯誤輸出的
    # 節點會被展開成大量錯誤（見 memory n8n-per-item-payload）。
    FAILABLE = [
        "Notion：取得該列", "Notion：取得母列", "檢查能不能翻",
        "WP：取文章網址", "WP：查同步草稿", "檢查前台是不是最新版",
        "抓頁面 HTML", "WP：取該篇字典現況", "WP：取全站人工譯文",
        "Notion：取產品術語表", PREP, "Notion：回寫待術語確認", "Notion：留言術語未確認",
        LLM, COLLECT, "WP：寫回整句列", SUMMARY, "Notion：再讀一次該列",
        "Notion：回寫已翻譯完成", "Notion：留言翻譯完成", "Notion：留言翻譯被打斷",
    ]
    by_name = {nd["name"]: nd for nd in nodes}
    for name in FAILABLE:
        by_name[name]["onError"] = "continueErrorOutput"
        main = list(conns.get(name, {}).get("main", [])) or [[]]
        while len(main) < 2:
            main.append([])
        main[1] = [to(REASON_FAIL)]
        conns[name] = {"main": main}

    return {"name": f"Synctify — 翻譯文章（{site}；Notion 按鈕觸發）",
            "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1"}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", choices=["test"], default="test",
                    help="目前只做測試站（Fay 2026-09-10：先做測試站翻譯按鈕）")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"模型 id（預設 {DEFAULT_MODEL}，心柔 2026-09-09 選定）")
    args = ap.parse_args()

    env_key = WEBHOOK_ENV[args.target]
    path = wp_env.read_env().get(env_key, "").strip()
    if not path:
        path = WEBHOOK_PLACEHOLDER
        print(f"⚠️  .env 沒有 {env_key}，webhook path 先用佔位字串。設定方式：")
        print(f'    echo "{env_key}=synctify-translate-$(openssl rand -hex 16)" >> .env')

    wf = build(args.target, args.model, path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"translate-article.{args.target}.workflow.json"
    out.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  站台：{WP_BASE[args.target]}（寫入真的會落地）")
    print(f"  模型：{args.model}")
    print(f"  webhook path：{'（佔位字串）' if path == WEBHOOK_PLACEHOLDER else '取自 .env'}")
    print(f"  節點：{len(wf['nodes'])} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
