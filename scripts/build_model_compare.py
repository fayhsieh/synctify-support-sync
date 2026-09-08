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


COLLECT = '''# 把三個模型的回應併成並排表。
_rows = {}
_models = {}
_errs = []
for _it in _items:
    _j = _it['json']
    # 呼叫失敗時 n8n 會把 json 換成錯誤物件，原本的 case／model 都不見了。
    # 2026-09-08 實測：憑證沒綁時 24 筆全變成這樣，而報告只印出一行標題、
    # cases=0——看不出發生什麼事。失敗要說得出話。
    _e = _j.get('error')
    if isinstance(_e, dict) and _e.get('message'):
        _errs.append(str(_e.get('message')))
        continue
    _c = _j.get('case')
    if _c is None:
        continue
    _r = _rows.setdefault(_c, {'en': _j.get('en', ''), 'boss': _j.get('boss', ''),
                               'out': {}})
    # OpenAI 回應的位置依節點設定而異，逐一嘗試，取不到就留下原始物件方便除錯
    _txt = ''
    _ch = _j.get('choices')
    if isinstance(_ch, list) and _ch:
        _txt = (_ch[0].get('message') or {}).get('content', '')
    if not _txt:
        _txt = _j.get('content') or _j.get('text') or ''
    _r['out'][_j.get('label', '?')] = (_txt or '').strip()
    _models[_j.get('label', '?')] = _j.get('model', '?')

_lines = []

if _errs:
    _uniq = []
    for _m in _errs:
        if _m not in _uniq:
            _uniq.append(_m)
    _lines.append('=' * 70)
    _lines.append('⚠️ 有 %d 次呼叫失敗，錯誤訊息：' % len(_errs))
    for _m in _uniq[:5]:
        _lines.append('   ' + _m)
    if 'Credentials not found' in ' '.join(_uniq):
        _lines.append('')
        _lines.append('   → 「呼叫模型」節點還沒選 OpenAI 憑證。')
        _lines.append('     打開該節點，在 Credential 下拉選單挑既有的 OpenAI 憑證，')
        _lines.append('     存檔後重跑即可。（匯入的工作流不會自動綁憑證）')
    _lines.append('')

if not _rows:
    _lines.append('沒有任何成功的回應，無法產生比較表。')
    return [{'json': {'report': chr(10).join(_lines), 'cases': 0,
                      'failed': len(_errs), 'mapping': {}}}]

for _c in sorted(_rows):
    _r = _rows[_c]
    _lines.append('=' * 70)
    _lines.append('【第 %d 句】' % (_c + 1))
    _lines.append('原文｜' + _r['en'])
    _lines.append('心柔｜' + _r['boss'])
    for _k in sorted(_r['out']):
        _lines.append('  %s ｜%s' % (_k, _r['out'][_k]))
    _lines.append('')

_lines.append('=' * 70)
_lines.append('對照表（給 Fay，不要給評估的人看）：')
for _k in sorted(_models):
    _lines.append('  %s = %s' % (_k, _models[_k]))

return [{'json': {'report': chr(10).join(_lines), 'cases': len(_rows),
                  'failed': len(_errs), 'mapping': _models}}]
'''


def build(models, n, named, cred=None):
    nid = lambda *p: det("model-compare", *p)
    nodes = [
        {"parameters": {}, "id": nid("trigger"), "name": "手動執行",
         "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
         "position": [240, 300],
         "notes": "這支流程只在挑模型時跑，不接 webhook。"},

        {"parameters": {"language": "pythonNative",
                        "pythonCode": code_body(models, n, named)},
         "id": nid("prep"), "name": "組 prompt（每句 × 每個模型）",
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

        {"parameters": {"language": "pythonNative", "pythonCode": COLLECT},
         "id": nid("collect"), "name": "併成並排表",
         "type": "n8n-nodes-base.code", "typeVersion": 2,
         "position": [900, 300],
         "notes": "輸出 report 欄位，直接讀就是並排比較。\n"
                  "模型對照表印在最後——" +
                  ("目前設定為直接顯示型號（--named）。"
                   if named else
                   "評估的人看 A／B／C 即可，\n"
                   "看到型號會被名字影響（傾向選聽起來比較新的那個）。")},
    ]
    conns = {
        "手動執行": {"main": [[{"node": "組 prompt（每句 × 每個模型）",
                                 "type": "main", "index": 0}]]},
        "組 prompt（每句 × 每個模型）":
            {"main": [[{"node": "呼叫模型", "type": "main", "index": 0}]]},
        "呼叫模型": {"main": [[{"node": "併成並排表", "type": "main", "index": 0}]]},
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
