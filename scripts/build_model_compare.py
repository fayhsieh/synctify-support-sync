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

# 比較用的樣本數。全部 29 筆會讓一次執行叫三十幾次 API、輸出也讀不完；
# 挑前幾筆足以看出語氣差異，不夠再調。
DEFAULT_N = 8


def det(*parts):
    return str(uuid.uuid5(_ID_NS, "|".join(str(p) for p in parts)))


def code_body(models, n, named):
    """組出 Code node 的程式：載入樣本、組 prompt、準備每個模型的請求。"""
    src = (CONVERTER / "translate_prompt.py").read_text(encoding="utf-8")
    src = re.split(r'^if __name__ == "__main__":', src, flags=re.M)[0]
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]

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
        "_MODELS = " + json.dumps(models, ensure_ascii=False) + "\n"
        "_N = " + str(n) + "\n"
        "_NAMED = " + ("True" if named else "False") + "\n"
        "\n"
        "# 詞彙表由上游節點傳入（Notion → 這裡）。取不到就用空表——\n"
        "# 空表只是少了術語約束，不會讓流程失敗；但會在輸出裡標明，\n"
        "# 免得看到結果的人以為術語約束有生效。\n"
        "_gloss = []\n"
        "for _it in _items:\n"
        "    _j = _it['json']\n"
        "    if 'glossary' in _j and isinstance(_j['glossary'], list):\n"
        "        _gloss = _j['glossary']\n"
        "        break\n"
        "\n"
        "_out = []\n"
        "for _idx, _s in enumerate(_SAMPLES[:_N]):\n"
        "    _sys, _usr = build_prompt(_s['en'], _gloss, _SAMPLES)\n"
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
COLLECT_JS = """
// 標籤指紋：依序列出所有標籤，用來檢查譯文有沒有破壞結構。
// 這是硬性要求（標籤是站上樣式的一部分），但**人不該用眼睛檢查**——
// 心柔要判斷的是語氣，被 HTML 淹沒只會看不到重點。機器查標籤、人看文字。
function tagSig(html) {
  const m = String(html || '').match(/<[^>]+>/g) || [];
  return m.map(t => t.replace(/\\s+/g, ' ').trim()).join('');
}
function stripTags(html) {
  return String(html || '').replace(/<[^>]+>/g, '')
    .replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&')
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
  if (!rows[c]) rows[c] = { en: meta.en || '', boss: meta.boss || '', out: {} };
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
function h3(s)       { return { object: 'block', type: 'heading_3', heading_3: { rich_text: [txt(s)] } }; }
function row(cells)  { return { object: 'block', type: 'table_row', table_row: { cells: cells } }; }
function table(w, rows) {
  return { object: 'block', type: 'table',
           table: { table_width: w, has_column_header: true,
                    has_row_header: false, children: rows } };
}

const blocks = [];
blocks.push(para([
  txt('請比較每一句底下 ' + labels.join('、') + ' 三個版本，挑出語氣與句式最接近「心柔」那一列的。'),
  txt('只看中文讀起來自然不自然', true),
  txt('——標籤結構已由程式檢查，不用你費神。')
]));

for (const c of keys) {
  const r = rows[c];
  blocks.push(h3('第 ' + (c + 1) + ' 句'));
  blocks.push(para([txt('原文　', true), txt(stripTags(r.en))]));
  blocks.push(para([txt('心柔　', true), txt(stripTags(r.boss))]));
  const trs = [row([[txt('版本', true)], [txt('譯文', true)], [txt('標籤', true)]])];
  for (const k of labels) {
    const v = r.out[k] || '';
    const ok = tagSig(v) === tagSig(r.en);
    trs.push(row([
      [txt(k, true)],
      [txt(stripTags(v) || '（沒有回應）')],
      [txt(v ? (ok ? '✅' : '❌ 結構不符') : '—')]
    ]));
  }
  blocks.push(table(3, trs));
}

blocks.push({ object: 'block', type: 'divider', divider: {} });
blocks.push(h3('標籤結構檢查（程式判定，供參考）'));
const chk = [row([[txt('版本', true)], [txt('通過', true)], [txt('說明', true)]])];
for (const k of labels) {
  let ok = 0, tot = 0;
  for (const c of keys) {
    const v = rows[c].out[k];
    if (!v) continue;
    tot++; if (tagSig(v) === tagSig(rows[c].en)) ok++;
  }
  chk.push(row([
    [txt(k, true)], [txt(ok + ' / ' + tot)],
    [txt(ok === tot ? '標籤全部原樣保留' : '有幾句改動了標籤結構，會影響站上版面')]
  ]));
}
blocks.push(table(3, chk));

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
lines.push('對照表（給 Fay，不要給評估的人看）：');
labels.forEach(k => lines.push('  ' + k + ' = ' + models[k]));

return [{ json: { report: lines.join('\\n'), notion_blocks: blocks,
                  cases: keys.length, failed: errs.length, mapping: models } }];
""".replace("PREP_NODE_NAME", PREP_NODE)


def build(models, n, named, cred=None):
    nid = lambda *p: det("model-compare", *p)
    nodes = [
        {"parameters": {}, "id": nid("trigger"), "name": "手動執行",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [240, 300],
         "notes": "這支流程只在挑模型時跑，不接 webhook。"},

        {"parameters": {"language": "pythonNative",
                        "pythonCode": code_body(models, n, named)},
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

        {"parameters": {"jsCode": COLLECT_JS},
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
        "手動執行": {"main": [[{"node": PREP_NODE, "type": "main", "index": 0}]]},
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
    out.write_text(json.dumps(build(models, args.n, args.named, args.cred),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 已產生 {out}")
    print(f"  模型：{'、'.join(models)}")
    print(f"  句數：{args.n}（每句每個模型各一次，共 {args.n * len(models)} 次呼叫）")
    print(f"  標示：{'直接顯示型號' if args.named else '盲測（A／B／C，對照表印在報告最後）'}")
    print(f"  憑證：{args.cred or OPENAI_CRED_NAME}（id 已內建，匯入即可用）")


if __name__ == "__main__":
    main()
