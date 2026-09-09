#!/usr/bin/env python3
"""產生「翻譯模型比較」的 n8n 工作流。

## 為什麼要在 n8n 裡跑

OpenAI 的 key 綁在 n8n 的憑證裡，不在 .env（刻意的：多一個地方存密鑰就多一個
外洩點）。所以模型比較也在 n8n 跑，key 完全不用離開。

## 這支流程做什麼

拿 samples/tp-style-samples.json 裡心柔校對過的整句樣本，對每個候選模型各翻一次，
最後輸出一張並排表：

    原文 ／ 心柔的版本 ／ 模型 A ／ 模型 B ／ 模型 C

**待譯的那一句會被排除在 few-shot 範例之外**（translate_prompt.pick_samples
負責），否則模型直接抄答案，比較就沒有意義。

## 盲測

輸出預設把模型標成 A／B／C，對照表另外印在最後。理由：評估的人看到模型名稱會
被名字影響（傾向選「聽起來比較新」的那個）。要直接顯示型號的話加 --named。

用法：
    ./.venv/bin/python scripts/build_model_compare.py \
        --models gpt-5.6-sol,gpt-5.4,gpt-5-mini
    ./.venv/bin/python scripts/build_model_compare.py --models ... --named

產出：n8n/local/translate-model-compare.workflow.json（已 gitignore）
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
_ID_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

# n8n 裡的 OpenAI 憑證。**id 不是密鑰**——它只是 n8n 內部的參照編號，
# 實際的 API key 留在 n8n 的憑證庫裡，所以進版控是安全的
# （NOTION_CRED_ID 在 build_n8n_code_node.py 已經這樣做了一個月）。
#
# 只給名稱不夠：n8n 是靠 id 綁定的，2026-09-08 實測匯入後節點仍是紅色驚嘆號。
OPENAI_CRED_ID = "UEtEu6Jad1QJoQvz"
OPENAI_CRED_NAME = "OpenAi account 2"

# 比較結果的落點：Marketing Wiki 底下的「翻譯模型評選」頁，每跑一次多一個子頁。
# 建成子頁而不是覆蓋同一頁——換模型、改 prompt 之後還會再跑，
# 留著歷次結果才看得出「改了 prompt 之後真的有變好嗎」。
NOTION_CRED_ID = "xfGHH7Wx4EucMC0X"
NOTION_CRED_NAME = "Support Center Sync"
COMPARE_PARENT = "3d52f2ede27d8158aa71f8e9d874662d"

# 產品用術語表（查詢用的 database id，不是 collection id——後者查會回 404，
# 而訊息是誤導性的「請與 integration 分享」）。
GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"

# 比較用的樣本數。全部 29 筆會讓一次執行叫三十幾次 API、輸出也讀不完；
# 挑前幾筆足以看出語氣差異，不夠再調。
DEFAULT_N = 8


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


def code_body(models, n, named, pool):
    """組出 Code node 的程式：載入樣本、組 prompt、準備每個模型的請求。"""
    src = (CONVERTER / "translate_prompt.py").read_text(encoding="utf-8")
    src = re.split(r'^if __name__ == "__main__":', src, flags=re.M)[0]
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]
    # 依「純文字長度」由長到短排序。
    #
    # Fay 2026-09-08 觀察：短句沒有鑑別度——「Select a Channel」三個模型
    # 一定譯得一樣。實測目前前 8 筆是 17–50 字，最長的 8 筆是 98–160 字。
    # 要看出模型差異，得給它們有語序、有從屬子句、有連接詞的句子。
    #
    # 全部樣本仍然當 few-shot 範例，只有「拿來比較的那幾句」挑長的。
    _plain = lambda h: re.sub(r"<[^>]+>", "", h or "").strip()
    ranked = sorted(samples, key=lambda x: -len(_plain(x.get("en"))))

    header = (
        "# " + "=" * 66 + "\n"
        "#  自動產生，請勿直接編輯\n"
        "#  來源：converter/translate_prompt.py + samples/tp-style-samples.json\n"
        "#  重新產生：./.venv/bin/python scripts/build_model_compare.py --models …\n"
        "# " + "=" * 66 + "\n"
    )
    adapter = (
        "\n\n# ─── n8n 轉接層 ───\n"
        "_SAMPLES = " + json.dumps(samples, ensure_ascii=False) + "\n"
        "_CASES = " + json.dumps(ranked[:pool], ensure_ascii=False) + "\n"
        "_MODELS = " + json.dumps(models, ensure_ascii=False) + "\n"
        "_N = " + str(n) + "\n"
        "_NAMED = " + ("True" if named else "False") + "\n"
        "\n"
        "# 詞彙表直接吃上游 Notion 查詢節點的原始回應。\n"
        "# 開了分頁時 n8n 會輸出多個 item（每頁一個），所以全部都要掃。\n"
        "#\n"
        "# 取不到就用空表——空表不會讓流程失敗，但 prompt 會變成完全沒有術語\n"
        "# 約束。2026-09-08 就是這樣：沒接詞彙表卻以為有接，比對出來的結果\n"
        "# 看不出術語問題。所以輸出帶 glossary_terms 計數，讓它無所遁形。\n"
        "_gloss = []\n"
        "for _it in _items:\n"
        "    for _pg in (_it['json'].get('results') or []):\n"
        "        _p = _pg.get('properties') or {}\n"
        "        _en = ''.join(_x.get('plain_text', '')\n"
        "                      for _x in ((_p.get('English') or {}).get('title') or []))\n"
        "        _zh = ''.join(_x.get('plain_text', '')\n"
        "                      for _x in ((_p.get('\u7b80\u4f53\u4e2d\u6587') or {}).get('rich_text') or []))\n"
        "        if _en.strip() and _zh.strip():\n"
        "            _gloss.append({'en': _en.strip(), 'zh': _zh.strip()})\n"
        "\n"
        "_out = []\n"
        "for _idx, _s in enumerate(_CASES):\n"
        "    _sys, _usr = build_prompt(_s['en'], _gloss, _SAMPLES)\n"
        "    # 這一句命中的術語，併表時用來把譯文裡的對應中文標粗體。\n"
        "    # find_terms 已經處理過詞邊界與長詞優先，不要在下游重做。\n"
        "    _hits = [{'en': _e, 'zh': _z} for _e, _z in find_terms(_s['en'], _gloss)]\n"
        "    for _mi, _m in enumerate(_MODELS):\n"
        "        _out.append({'json': {\n"
        "            'case': _idx,\n"
        "            'model': _m,\n"
        "            'label': _m if _NAMED else chr(65 + _mi),\n"
        "            'system': _sys,\n"
        "            'user': _usr,\n"
        "            'en': _s['en'],\n"
        "            'boss': _s['zh_cn'],\n"
        "            'glossary_terms': len(_gloss),\n"
        "            'terms': _hits,\n"
        "        }})\n"
        "return _out\n"
    )
    return header + "\n" + src.rstrip() + adapter


PREP_NODE = "組 prompt（每句 × 每個模型）"

# 併表節點用 **JavaScript**，不是 Python。
#
# 2026-09-08 實測踩到：HTTP 節點會把 item 的 json **整個換成 OpenAI 的回應**，
# 上游放的 case／label／en／boss 全部不見，於是併表一筆都認不得，
# 印出「沒有任何成功的回應」——但呼叫其實成功了。
#
# n8n 的 Python Code node **讀不到其他節點**（節點畫面自己寫著：
# "The Python option does not support _ syntax and helpers, except for _items"），
# 所以沒辦法回頭去拿上游欄位。JS 模式可以用 $('節點名').all()，故改寫成 JS。
#
# 配對靠**位置**：HTTP 節點逐筆處理、輸出順序與輸入一致，失敗的項也會佔一個位置
# （onError=continueRegularOutput）。若兩邊筆數對不上就直接報錯而不是硬配——
# 配錯的表看起來完全正常，那比報錯危險得多。
COLLECT_JS = ("""
// 標籤指紋：依序列出所有標籤，用來檢查譯文有沒有破壞結構。
// 這是硬性要求（標籤是站上樣式的一部分），但**人不該用眼睛檢查**——
// 心柔要判斷的是語氣，被 HTML 淹沒只會看不到重點。機器查標籤、人看文字。
function tagSig(html) {
  const m = String(html || '').match(/<[^>]+>/g) || [];
  return m.map(t => t.replace(/\\s+/g, ' ').trim()).join('');
}
// HTML 實體解碼。
//
// 2026-09-08 實測：第 5、8 題在 Notion 上印出 &nbsp;——原本只解 &gt;／&lt;／&amp;，
// 樣本裡卻有 3 處 &nbsp;。**只解常見的那幾個是不夠的**，漏掉的會原樣印在
// 受測者眼前，看起來像系統壞掉，還會干擾她判斷譯文品質。
//
// 迴圈最多三輪：處理雙重編碼（&amp;gt; 解一次只會變成 &gt;）。
// &amp; 一定要放在每一輪的最後解，否則 &amp;lt; 會被提前拆成 &lt; 再變成 <。
function decodeEntities(sv) {
  let t = String(sv == null ? '' : sv);
  for (let i = 0; i < 3; i++) {
    const before = t;
    t = t.replace(/&nbsp;/gi, ' ')
         .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
         .replace(/&quot;/gi, '\"').replace(/&apos;/gi, "'")
         .replace(/&#x27;/gi, "'").replace(/&#0*39;/g, "'")
         .replace(/&#0*34;/g, '\"')
         .replace(/&amp;/gi, '&');
    if (t === before) break;
  }
  return t;
}
function stripTags(html) {
  return decodeEntities(String(html || '').replace(/<[^>]+>/g, ''))
    .replace(/\\s+/g, ' ').trim();
}

const calls = $input.all();
const prep  = $('PREP_NODE_NAME').all();

if (calls.length !== prep.length) {
  return [{ json: {
    report: '⚠️ 呼叫結果 ' + calls.length + ' 筆，但上游送出 ' + prep.length +
            ' 筆，數量對不上，無法安全配對。\\n' +
            '（併表靠位置配對，硬配會產生一張看起來正常但內容錯亂的表。）',
    notion_blocks: [], cases: 0, failed: 0, mapping: {} } }];
}

const rows = {}, models = {}, errs = [];
for (let i = 0; i < calls.length; i++) {
  const meta = prep[i].json, res = calls[i].json;
  const err = res && res.error;
  if (err && err.message) { errs.push(String(err.message)); continue; }
  let txt = '';
  if (res && Array.isArray(res.choices) && res.choices.length) {
    txt = (res.choices[0].message || {}).content || '';
  }
  if (!txt) txt = res.content || res.text || '';
  const c = meta.case;
  if (!rows[c]) rows[c] = { en: meta.en || '', boss: meta.boss || '',
                            terms: meta.terms || [], out: {} };
  rows[c].out[meta.label] = String(txt || '').trim();
  models[meta.label] = res.model || meta.model || '?';
}

const keys = Object.keys(rows).map(Number).sort((a, b) => a - b);
const labels = Object.keys(models).sort();

// ── 給 Notion 的版本：組成**真正的 Notion 區塊**，不是 markdown 字串 ──
// 2026-09-08 實測踩到：把 markdown 當純文字段落送過去，Notion API 不解析，
// 頁面上就原樣印出 ### 和 | --- |。要表格就得送 table 區塊物件。
//
// 區塊在這裡組好、HTTP 節點只負責送——在 n8n 的運算式裡拼這種巢狀 JSON
// 既難讀也沒辦法測。
function txt(s, bold) {
  const o = { type: 'text', text: { content: String(s == null ? '' : s).slice(0, 1900) } };
  if (bold) o.annotations = { bold: true };
  return o;
}
function para(rich)  { return { object: 'block', type: 'paragraph', paragraph: { rich_text: rich } }; }
function code(sv) {
  return { type: 'text', text: { content: String(sv || '').slice(0, 1900) },
           annotations: { code: true } };
}
// direction_step 包住的是**可點擊的 UI 路徑**，在 Notion 用 inline code 呈現，
// 跟站上與 Notion 寫作慣例一致（Fay 2026-09-08）。
//
// ⚠️ 用 direction_step(?!s) 而不是 direction_step：外層是 direction_steps
// （複數），純子字串比對會先命中外層、把整包吃掉，內層就抓不到了。
const STEP_RE = /<span[^>]*class="[^"]*direction_step(?!s)[^"]*"[^>]*>([\\s\\S]*?)<\\/span>/g;
// 句中出現的首字大寫詞＝UI 標籤或專有名詞，兩邊都標粗體（Fay 2026-09-09）。
//
// 這些正是術語表該管的詞。標出來之後，「某個模型有沒有照表翻」一眼就看得到，
// 不用逐字比對。
//
// **句首的大寫不算**——那是英文的句法，不是專有名詞。判斷方式是看前面
// 是不是字串開頭或句末標點，不是看有沒有大寫。
//
// 單字用 [A-Z][A-Za-z0-9]* 而不是 [A-Z][a-z]*：SSCC／ASIN／UPC 這種全大寫
// 縮寫要當成一個詞，用後者會被拆成 S、S、C、C 四個。
function capTerms(plain) {
  const found = [];
  const re = /([A-Z][A-Za-z0-9]*(?:\\s+[A-Z][A-Za-z0-9]*)*)/g;
  let m;
  while ((m = re.exec(plain)) !== null) {
    const before = plain.slice(0, m.index).replace(/\\s+$/, '');
    const atStart = !before || /[.!?:;]$/.test(before);
    // 句首的大寫是英文句法，不是專有名詞。但**只能丟掉句首那一個字**——
    // 「Click Create in the…」會被抓成一組 "Click Create"，整組丟掉的話
    // Create 就跟著不見了，而那正是要標的詞。
    const words = atStart ? m[1].split(/\\s+/).slice(1) : [m[1].trim()];
    const term = words.join(' ').trim();
    if (term.length < 2) continue;
    found.push(term);
  }
  return found;
}

// 把一段純文字依 needles 切開，命中的標粗體。長的優先，否則
// 「Shipment Routing Requests」會先被「Shipment」吃掉，剩下兩個字散在外面。
function splitBold(text, needles) {
  if (!needles.length) return [{ t: text, b: false }];
  const esc = needles.slice().sort((a, b) => b.length - a.length)
    .map(n => n.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&'));
  const re = new RegExp('(' + esc.join('|') + ')', 'g');
  const out = [];
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ t: text.slice(last, m.index), b: false });
    out.push({ t: m[0], b: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ t: text.slice(last), b: false });
  return out;
}

function richFrom(html, needles) {
  const src = String(html == null ? '' : html);
  const nds = needles || [];
  const out = [];
  // 一段文字可能同時是 UI 路徑（code）與專有名詞（bold），Notion 允許並存。
  const push = (text, isCode) => {
    for (const part of splitBold(text, nds)) {
      if (!part.t) continue;
      const o = { type: 'text', text: { content: part.t.slice(0, 1900) } };
      const ann = {};
      if (isCode) ann.code = true;
      if (part.b) ann.bold = true;
      if (isCode || part.b) o.annotations = ann;
      out.push(o);
    }
  };
  let last = 0, m;
  STEP_RE.lastIndex = 0;
  while ((m = STEP_RE.exec(src)) !== null) {
    const before = stripTags(src.slice(last, m.index));
    if (before) push(before, false);
    const inner = stripTags(m[1]);
    if (inner) push(inner, true);
    last = m.index + m[0].length;
  }
  const tail = stripTags(src.slice(last));
  if (tail) push(tail, false);
  return out.length ? out : [txt('')];
}
function h3(s)       { return { object: 'block', type: 'heading_3', heading_3: { rich_text: [txt(s)] } }; }
function row(cells)  { return { object: 'block', type: 'table_row', table_row: { cells: cells } }; }
function table(w, rows) {
  return { object: 'block', type: 'table',
           table: { table_width: w, has_column_header: true,
                    has_row_header: false, children: rows } };
}

// ── 三個模型並排，直接標明型號 ──
//
// Fay 2026-09-09 改設計，兩個理由：
//
// 1. **心柔的譯文早於詞彙表定案**，帶著過時術語（商品／产品那類）。
//    把它當成選項之一，會讓「詞彙表到底有沒有生效」看不出來——
//    她的版本用舊術語卻讀起來最順，模型用新術語反而像是譯錯了。
//
// 2. 目標變了。原本問「模型追上人工了嗎」，現在問「三個模型你偏好哪個」，
//    因為**不可能再請心柔逐句翻**，她的版本不是可持續的標準。
//    選出來的那個之後要調校，所以要知道的是偏好，不是及格與否。
//
// 既然不再跟人比，匿名也就沒有意義了：她不會認得任何一個版本的字跡，
// 而標明型號讓 Fay 讀結果時不必再對照表。
//
// 版面是每題一張橫向表：型號當欄、譯文並排一列。並排比堆疊容易看出差異，
// 差別往往只在兩三個字。
const blocks = [];
blocks.push(para([
  txt('每一題有三個模型的譯文並排。請在最底下那一列標出'),
  txt('你偏好哪一個', true),
  txt('（打勾、○ 都可以）。')
]));
blocks.push(para([
  txt('只看中文讀起來自然不自然、術語用得對不對。', true),
  txt('HTML 標籤有沒有被保留是硬性要求，已由程式另外檢查，不用你費神。')
]));
blocks.push(para([
  txt('這一輪不含人工譯文——術語表定案後很多詞改過，舊譯文放進來會干擾判斷。')
]));

// 可疑字元檢查：譯文出現了原文沒有的成對符號。
//
// 2026-09-08 實測抓到：某個模型把第 1 句譯成「…然后点击`确认`】【。」——
// 原文結尾沒有任何括號，那個 】【 是模型自己吐的贅字。
//
// 標籤結構檢查抓不到這種：】【 不是標籤，span 指紋完全一致，檢查照樣通過。
// 也就是說模型可以在保持標籤完整的前提下吐出垃圾字元，程式卻看不見。
//
// 只看**成對符號**（括號、引號類）而不是所有標點：句號、逗號本來就會隨著
// 語言轉換而增減，全部列進來會滿螢幕假警報，跳久了就沒人看了。
const PAIRED = '【】〖〗《》「」『』（）()[]{}';
function oddChars(src, out) {
  const bad = [];
  for (const ch of PAIRED) {
    const a = (stripTags(src).split(ch).length - 1);
    const b = (stripTags(out).split(ch).length - 1);
    if (b > a) bad.push(ch + '×' + (b - a));
  }
  return bad;
}

// 三個模型全部譯得一樣的題目沒有東西好選，剔除。
// （不再合併重複選項：型號既然公開，兩個模型剛好一樣本身就是資訊，
//   並排看得到，不需要程式幫忙併欄。）
const built = [];
for (const c of keys) {
  const r = rows[c];
  const texts = labels.map(k => r.out[k] || '');
  if (!texts.some(t => t)) continue;
  const uniq = {};
  texts.forEach(t => { if (t) uniq[stripTags(t)] = 1; });
  if (Object.keys(uniq).length >= 2) {
    built.push({ c: c, r: r, texts: texts, n: Object.keys(uniq).length });
  }
}

// 差異越大越有鑑別度，優先出題
built.sort((a, b) => b.n - a.n || a.c - b.c);
const chosen = built.slice(0, __N_SHOW__).sort((a, b) => a.c - b.c);
const dropped = keys.length - built.length;

let qn = 0;
for (const it of chosen) {
  const r = it.r;
  qn++;
  // 原文標粗體的是「句中出現的大寫詞」；譯文標的是它們的中文對應。
  //
  // 中文那邊的詞從術語表來（prep 節點已經算好這一句命中哪些），不是自己猜。
  // 英文詞本身也一起找——UI 標籤常常整個保留英文不譯，那時候要標的是英文。
  const capped = capTerms(stripTags(r.en));
  const zhTerms = [];
  for (const t of (r.terms || [])) {
    if (t.zh) zhTerms.push(t.zh);
    if (t.en) zhTerms.push(t.en);
  }
  const outNeedles = zhTerms.concat(capped);

  blocks.push(h3(qn + '. 原文'));
  blocks.push(para(richFrom(r.en, capped)));

  const w = labels.length + 1;
  const head = [[txt('Model', true)]];
  const body = [[txt('翻譯', true)]];
  const pick = [[txt('我選這個', true)]];
  labels.forEach((k, idx) => {
    head.push([txt(models[k], true)]);
    body.push(it.texts[idx] ? richFrom(it.texts[idx], outNeedles)
                            : [txt('（沒有回應）')]);
    pick.push([txt('')]);
  });
  blocks.push(table(w, [row(head), row(body), row(pick)]));
}

blocks.push({ object: 'block', type: 'divider', divider: {} });
if (!chosen.length) {
  // 全部題目都被剔除＝每一句所有版本都相同。頁面留白會讓人以為壞了，
  // 但這其實是個結論：模型的產出已經跟人工一致，沒有東西好選。
  blocks.push(para([
    txt('這批句子裡，三個模型譯出來完全相同，沒有需要選擇的題目。', true)
  ]));
  blocks.push(para([txt('不是出錯，是沒有差異可比——術語與句式都被約束住了。'
                        + '請告訴 Fay。')]));
} else {
  blocks.push(para([txt('選好之後把這頁給 Fay 就可以了。')]));
}

// ── 給 Fay 的終端版本（含標籤原文，方便除錯）──
const lines = [];
if (errs.length) {
  const uniq = [...new Set(errs)];
  lines.push('⚠️ 有 ' + errs.length + ' 次呼叫失敗：');
  uniq.slice(0, 5).forEach(m => lines.push('   ' + m));
  lines.push('');
}
if (!keys.length) {
  lines.push('沒有任何成功的回應，無法產生比較表。');
  return [{ json: { report: lines.join('\\n'), notion_blocks: [], cases: 0,
                    failed: errs.length, mapping: {} } }];
}
for (const c of keys) {
  const r = rows[c];
  lines.push('='.repeat(70));
  lines.push('【第 ' + (c + 1) + ' 句】');
  lines.push('原文｜' + r.en);
  lines.push('心柔｜' + r.boss);
  labels.forEach(k => lines.push('  ' + k + ' ｜' + (r.out[k] || '（無）')));
  lines.push('');
}
lines.push('='.repeat(70));
lines.push('出題：' + chosen.length + ' 題'
           + (dropped ? '（另有 ' + dropped + ' 句所有版本完全相同，已剔除）' : '')
           + '；題庫共 ' + keys.length + ' 句');
lines.push('');
const odd = [];
for (const c of keys) {
  for (const k of labels) {
    const v = rows[c].out[k];
    if (!v) continue;
    const bad = oddChars(rows[c].en, v);
    if (bad.length) {
      odd.push('  原第 ' + (c + 1) + ' 句　' + models[k] + '　多出：' + bad.join(' ')
               + '　→ ' + stripTags(v).slice(-40));
    }
  }
}
if (odd.length) {
  lines.push('⚠️ 譯文出現原文沒有的成對符號（模型吐出的贅字）：');
  odd.slice(0, 12).forEach(l => lines.push(l));
  lines.push('');
}
lines.push('標籤結構檢查（程式判定，心柔看不到這段）：');
for (const k of labels) {
  let ok = 0, tot = 0;
  for (const c of keys) {
    const v = rows[c].out[k];
    if (!v) continue;
    tot++; if (tagSig(v) === tagSig(rows[c].en)) ok++;
  }
  lines.push('  ' + models[k] + '　' + ok + ' / ' + tot
             + (ok === tot ? '　標籤全部原樣保留' : '　有幾句改動了標籤結構'));
}
lines.push('');
lines.push('='.repeat(70));
lines.push('人工譯文（僅供 Fay 對照，沒有放進 Notion）：');
lines.push('心柔的版本早於術語表定案，術語可能過時——列在這裡是為了');
lines.push('看出「模型改用新術語」與「模型譯錯」的差別。');
for (const it of chosen) {
  lines.push('  原第 ' + (it.c + 1) + ' 句　' + stripTags(it.r.boss || '（無）'));
}

return [{ json: { report: lines.join('\\n'), notion_blocks: blocks,
                  cases: chosen.length, pool: keys.length,
                  dropped: dropped, failed: errs.length,
                  mapping: models } }];
""".replace("PREP_NODE_NAME", PREP_NODE))


def build(models, n, named, cred=None, pool=None):
    pool = pool or n * 2
    nid = lambda *p: det("model-compare", *p)
    nodes = [
        {"parameters": {}, "id": nid("trigger"), "name": "手動執行",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [240, 300],
         "notes": "這支流程只在挑模型時跑，不接 webhook。"},

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
         "credentials": {"notionApi": {"id": NOTION_CRED_ID,
                                       "name": NOTION_CRED_NAME}},
         "id": nid("gloss"), "name": "Notion：取產品術語表",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [400, 300],
         "notes": "術語約束的來源。沒有這一步，prompt 裡就完全沒有術語規則——\n"
                  "2026-09-08 第一次比較就是這樣跑的，模型各自發揮。\n\n"
                  "**要分頁**：術語表 150+ 筆，單次上限 100。漏掉的那些若剛好\n"
                  "出現在原文裡，就會變成「有詞彙表卻沒約束到」，比沒有更難察覺。"},

        {"parameters": {"language": "pythonNative",
                        "pythonCode": code_body(models, n, named, pool)},
         "id": nid("prep"), "name": PREP_NODE,
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [460, 300],
         "notes": "自動產生，請勿直接編輯。\n"
                  "改 converter/translate_prompt.py 後重新產生。\n\n"
                  "待譯的那一句會被排除在 few-shot 範例之外，\n"
                  "否則模型直接抄答案，比較沒有意義。"},

        {"parameters": {
            "method": "POST",
            "url": "https://api.openai.com/v1/chat/completions",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "openAiApi",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ { \"model\": $json.model, \"messages\": ["
                        "{ \"role\": \"system\", \"content\": $json.system }, "
                        "{ \"role\": \"user\", \"content\": $json.user } ] } }}",
            "options": {}},
         "credentials": {"openAiApi": {"id": OPENAI_CRED_ID,
                                        "name": cred or OPENAI_CRED_NAME}},
         "id": nid("call"), "name": "呼叫模型",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [680, 300],
         "alwaysOutputData": True,
         "onError": "continueRegularOutput",
         "notes": "用 n8n 既有的 OpenAI 憑證，key 不離開 n8n。\n\n"
                  "onError=continue：某個模型不支援或名稱打錯時，\n"
                  "不該讓整批比較失敗——其他模型的結果仍然有價值。\n"
                  "那一格會是空的，看報告時就知道是哪個模型有問題。"},

        {"parameters": {"jsCode": COLLECT_JS.replace("__N_SHOW__", str(n))},
         "id": nid("collect"), "name": "併成並排表",
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [900, 300],
         "notes": "輸出 report 欄位，直接讀就是並排比較。\n"
                  "模型對照表印在最後——" +
                  ("目前設定為直接顯示型號（--named）。"
                   if named else
                   "評估的人看 A／B／C 即可，\n"
                   "看到型號會被名字影響（傾向選聽起來比較新的那個）。")},

        {"parameters": {
            "method": "POST",
            "url": "https://api.notion.com/v1/pages",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "notionApi",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Notion-Version", "value": "2022-06-28"}]},
            "sendBody": True, "specifyBody": "json",
            # Notion 的 rich_text 單段上限 2000 字，整份報告一定超過，
            # 所以切成多個 paragraph block 送。切點取換行，不會切在句中。
            "jsonBody": "={{ { \"parent\": { \"page_id\": \"" + COMPARE_PARENT + "\" }, "
                        "\"icon\": { \"emoji\": \"\\u2696\\ufe0f\" }, "
                        "\"properties\": { \"title\": [ { \"text\": { \"content\": "
                        "\"模型比較 \" + $now.toFormat('yyyy-MM-dd HH:mm') } } ] }, "
                        "\"children\": $json.notion_blocks.slice(0, 95) } }}",
            "options": {}},
         "credentials": {"notionApi": {"id": NOTION_CRED_ID,
                                       "name": NOTION_CRED_NAME}},
         "id": nid("notion"), "name": "Notion：建立比較頁",
         "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
         "position": [1120, 300],
         "notes": "把可讀版寫成「翻譯模型評選」底下的子頁，給心柔看。\n\n"
                  "送的是 notion 欄位（標籤已剝除、只留中文），不是 report——\n"
                  "report 帶著 HTML 給 Fay 除錯用，心柔看那個只會被淹沒。\n\n"
                  "切成多個 paragraph block：Notion 單段 rich_text 上限 2000 字，\n"
                  "整份報告必定超過。切點取空行，不會切在句子中間。"},
    ]
    conns = {
        "手動執行": {"main": [[{"node": "Notion：取產品術語表",
                                 "type": "main", "index": 0}]]},
        "Notion：取產品術語表": {"main": [[{"node": PREP_NODE,
                                            "type": "main", "index": 0}]]},
        PREP_NODE:
            {"main": [[{"node": "呼叫模型", "type": "main", "index": 0}]]},
        "呼叫模型": {"main": [[{"node": "併成並排表", "type": "main", "index": 0}]]},
        "併成並排表": {"main": [[{"node": "Notion：建立比較頁",
                                   "type": "main", "index": 0}]]},
    }
    return {"name": "Synctify — 翻譯模型比較", "nodes": nodes,
            "connections": conns, "settings": {"executionOrder": "v1"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True,
                    help="候選模型，逗號分隔（例：gpt-5.6-sol,gpt-5.4,gpt-5-mini）")
    ap.add_argument("--n", type=int, default=DEFAULT_N,
                    help=f"比較幾句（預設 {DEFAULT_N}）")
    ap.add_argument("--pool", type=int,
                    help="要翻譯的題庫句數（預設 n×2）。多譯一些，"
                         "才能從中挑出模型差異最大的題目出給心柔")
    ap.add_argument("--cred",
                    help=f"覆寫 OpenAI 憑證顯示名稱（預設 {OPENAI_CRED_NAME}，"
                         f"id 已內建）")
    ap.add_argument("--named", action="store_true",
                    help="直接顯示型號，不做盲測")
    args = ap.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        print("✗ 至少要兩個模型才有得比")
        sys.exit(1)
    if not SAMPLES.exists():
        print(f"✗ 找不到樣本檔 {SAMPLES}")
        sys.exit(1)

    out_dir = ROOT / "n8n" / "local"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "translate-model-compare.workflow.json"
    out.write_text(json.dumps(build(models, args.n, args.named, args.cred, args.pool),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  模型：{'、'.join(models)}")
    _pool = args.pool or args.n * 2
    print(f"  題庫：{_pool} 句（共 {_pool * len(models)} 次呼叫）")
    print(f"  出題：最多 {args.n} 題——挑選項最多（模型差異最大）的，"
          f"全部版本相同的題目會剔除")
    print(f"  版面：每題一張橫向表，型號當欄、三個模型的譯文並排")
    print(f"  人工譯文：不進 Notion（術語過時會干擾判斷），只印在報告供對照")
    print(f"  憑證：{args.cred or OPENAI_CRED_NAME}（id 已內建，匯入即可用）")


if __name__ == "__main__":
    main()
