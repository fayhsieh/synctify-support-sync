#!/usr/bin/env python3
"""翻譯前的術語檢查：從一篇文章撈出 UI 詞，對照術語表，列出還沒處理好的。

## 為什麼需要這支

2026-09-10 post 7622（正式站 #7889 Manage Integrated Message Codes）試跑自動翻譯，
跟心柔的人工譯文一比才發現一批詞根本不在術語表：Override、Create Override、
Preview & Test、Inbound only… 模型只好自己猜，同一篇裡「仅入库」「仅入站」並存，
Override 直接留英文。事後補詞、重跑、再比對，一篇多繞一輪。

這支把「發現新詞」提前到**翻譯之前**。判斷核心在 converter/term_check.py，
同步工作流（n8n）用的也是那一份——本機與 n8n 不會各有一套規則。

這支比 n8n 多做的是**簡中建議**（n8n 拿不到 OMS 語言檔）：

1. **OMS 語言檔 zh_CN**——產品畫面實際顯示的字。OMS 有英文沒中文（7622 的
   Override 就是）另外標出來，那是工程團隊要補的缺譯。
2. **正式站人工譯文**（status=2，全站）——心柔在別篇已經用過的譯法。
3. 都沒有就留空。**不讓模型出建議**：建議值會被當成預設答案直接勾掉。

這些只是給審詞的人參考；進術語表、打勾一律由人決定。

## 只讀不寫

不寫 Notion、不寫 WordPress。輸出清單給人看。

## 用法

    python scripts/glossary_article_terms.py --post 7622
    python scripts/glossary_article_terms.py --post 7622 --md report.md
    python scripts/glossary_article_terms.py --post 7622 --no-oms      # 不抓 OMS（較快）
    python scripts/glossary_article_terms.py --post 7622 --strict      # 有未處理的詞就 exit 1

正式站人工譯文要連 VPN；沒連上會略過那一欄並提示，不中斷。
"""
import argparse
import html as htmllib
import json
import pathlib
import sys
from collections import Counter, defaultdict

import httpx

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "converter"))
import wp_env  # noqa: E402
import tp_blocks  # noqa: E402
import translate_prompt as tp  # noqa: E402
import term_check as tc  # noqa: E402
from term_check import (NEW, EMPTY, DRAFT, CONFIRMED,  # noqa: E402,F401
                        clean_label, keep, classify)


def candidates(blocks):
    """從頁面區塊撈候選詞。規則在 term_check，這裡只是把區塊攤成文字。"""
    return tc.candidates([b.get("original") or "" for b in blocks])


def forms(label):
    """查 OMS／人工譯文用的單複數變體（那兩邊的鍵是整串小寫，沒有邊界比對）。"""
    low = label.lower()
    if low.endswith("s") and not low.endswith("ss"):
        return [low, low[:-1]]
    return [low, low + "s"]


# ── 取資料 ────────────────────────────────────────────────────────────

def fetch_page(target, post_id):
    t = wp_env.resolve(target)
    c = httpx.Client(timeout=60, follow_redirects=True)
    r = c.get(f"{t.base}/wp-json/wp/v2/docs/{post_id}", auth=t.auth,
              params={"_fields": "id,link,title"})
    if r.status_code == 202:
        sys.exit(f"✗ {t.base} 回 HTTP 202——VPN 沒連上")
    r.raise_for_status()
    meta = r.json()
    page = c.get(meta["link"], auth=t.auth).text
    got = tp_blocks.detect_post_id(page)
    if got != post_id:
        sys.exit(f"✗ 要查 post {post_id}，抓到的頁面卻是 {got}")
    return meta, page


def load_glossary():
    import glossary_sync as gs
    token = wp_env.read_env().get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")
    rows = []
    for r in gs.fetch_glossary(token):
        p = r["props"]
        rows.append({"en": r["english"].strip(),
                     "zh": (gs.current(p, "简体中文") or "").strip(),
                     "ok": bool((p.get("已確認") or {}).get("checkbox")),
                     "id": r["id"]})
    return rows


def fetch_human(target="prod"):
    """全站人工譯文 → {原文小寫: Counter(譯文)}。VPN 沒連回 None。"""
    t = wp_env.resolve(target)
    c = httpx.Client(timeout=90, follow_redirects=True)
    out, off = defaultdict(Counter), 0
    while True:
        r = c.get(f"{t.base}/wp-json/synctify/v1/tp/strings", auth=t.auth,
                  params={"language": "zh_CN", "status": 2, "limit": 500, "offset": off})
        if r.status_code == 202:
            return None
        r.raise_for_status()
        items = r.json().get("items") or []
        for x in items:
            # 原文也要去加號：字典裡存的是「+ Add Code」，不去掉就查不到 Add Code
            en, zh = clean_label(x.get("original")), clean_label(x.get("translated"))
            if en and zh and len(en) <= 60:
                out[en.lower()][zh] += 1
        if len(items) < 500:
            return out
        off += 500


# ── 報告 ──────────────────────────────────────────────────────────────

def _cell(s):
    return (s or "").replace("|", "／").replace("\n", " ")


def suggest(label, oms, human):
    oms_txt, human_txt, flags = "—", "—", []
    if oms is not None:
        hit = next((oms[f] for f in forms(label) if f in oms), None)
        if hit and hit["cn"]:
            oms_txt = "／".join(sorted(hit["cn"]))
        elif hit:
            oms_txt = "缺 zh_CN"
            flags.append("OMS 有英文沒中文→工程團隊")
    else:
        oms_txt = "（未查）"
    if human is None:
        human_txt = "（VPN 未連）"
    else:
        cnt = Counter()
        for f in forms(label):
            cnt.update(human.get(f, {}))
        if cnt:
            human_txt = "／".join(f"{z} ×{n}" for z, n in cnt.most_common(3))
    return oms_txt, human_txt, flags


def build_report(meta, n_blocks, found, glossary, oms, human):
    groups = defaultdict(list)
    for rec in found.values():
        st, row = classify(rec["label"], glossary)
        groups[st].append((rec, row))
    confirmed_rows = [g for g in glossary if g["ok"] and g["zh"]]

    title = htmllib.unescape(meta.get("title", {}).get("rendered", ""))
    L = [f"# 術語檢查：post {meta['id']}〈{title}〉\n",
         f"- 頁面區塊 {n_blocks}，撈到 UI 詞 {len(found)} 個",
         f"- 🔴 術語表沒有 {len(groups[NEW])}　🟠 有列但沒簡中 {len(groups[EMPTY])}　"
         f"🟡 草稿未勾 {len(groups[DRAFT])}　✅ 已確認 {len(groups[CONFIRMED])}",
         "- 🔴🟠🟡 在翻譯時都**不會**進 prompt——模型會自己猜\n"]

    if groups[NEW]:
        L += [f"## 🔴 術語表沒有（{len(groups[NEW])}）\n",
              "| 詞 | 次數 | 位置 | OMS zh_CN | 正式站人工譯文 | 備註 |",
              "|---|---|---|---|---|---|"]
        for rec, _ in sorted(groups[NEW], key=lambda x: -x[0]["n"]):
            o, h, flags = suggest(rec["label"], oms, human)
            inside = [f"{e}＝{z}" for e, z in tp.find_terms(rec["label"], confirmed_rows)]
            if inside:
                flags.append("含已確認：" + "、".join(inside))
            L.append(f"| {_cell(rec['label'])} | {rec['n']} | {'／'.join(sorted(rec['kinds']))} "
                     f"| {_cell(o)} | {_cell(h)} | {_cell('；'.join(flags))} |")
        L.append("")

    if groups[EMPTY]:
        L += [f"## 🟠 有列但沒簡中（{len(groups[EMPTY])}）\n",
              "| 詞 | 術語表列 | 次數 | OMS zh_CN | 正式站人工譯文 |", "|---|---|---|---|---|"]
        for rec, row in groups[EMPTY]:
            o, h, _ = suggest(rec["label"], oms, human)
            L.append(f"| {_cell(rec['label'])} | {_cell(row['en'])} | {rec['n']} | {_cell(o)} | {_cell(h)} |")
        L.append("")

    if groups[DRAFT]:
        L += [f"## 🟡 草稿未勾（{len(groups[DRAFT])}）——勾了才會用\n",
              "| 詞 | 術語表列 | 草稿簡中 | 次數 |", "|---|---|---|---|"]
        for rec, row in sorted(groups[DRAFT], key=lambda x: -x[0]["n"]):
            L.append(f"| {_cell(rec['label'])} | {_cell(row['en'])} | {_cell(row['zh'])} | {rec['n']} |")
        L.append("")

    if groups[CONFIRMED]:
        L += [f"## ✅ 已確認（{len(groups[CONFIRMED])}）\n",
              "、".join(f"{rec['label']}＝{row['zh']}" for rec, row in groups[CONFIRMED]), ""]
    return "\n".join(L) + "\n", groups


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--post", type=int, required=True, help="文章 post id")
    ap.add_argument("--target", choices=("test", "prod"), default="test",
                    help="文章在哪個站（預設測試站）")
    ap.add_argument("--no-oms", action="store_true", help="不抓 OMS 語言檔")
    ap.add_argument("--md", help="另存 Markdown 報告")
    ap.add_argument("--json", help="另存 JSON（給之後接 n8n／建草稿列用）")
    ap.add_argument("--strict", action="store_true",
                    help="有 🔴🟠🟡 就 exit 1（之後當翻譯前的閘門用）")
    args = ap.parse_args()

    meta, page = fetch_page(args.target, args.post)
    blocks = tp_blocks.extract_blocks(page, args.post)
    found = candidates(blocks)
    glossary = load_glossary()

    oms = None
    if not args.no_oms:
        import glossary_sync as gs
        print("抓 OMS 語言檔…", file=sys.stderr)
        oms = gs.fetch_oms_lang()
    print("抓正式站人工譯文…", file=sys.stderr)
    human = fetch_human("prod")
    if human is None:
        print("⚠️ 正式站回 202（VPN 沒連），略過人工譯文欄", file=sys.stderr)

    report, groups = build_report(meta, len(blocks), found, glossary, oms, human)
    print(report)
    if args.md:
        pathlib.Path(args.md).write_text(report, encoding="utf-8")
        print(f"✓ 已寫入 {args.md}", file=sys.stderr)
    if args.json:
        data = {st: [{"label": r["label"], "n": r["n"], "kinds": sorted(r["kinds"]),
                      "row": (row or {}).get("en"), "zh": (row or {}).get("zh")}
                     for r, row in items] for st, items in groups.items()}
        pathlib.Path(args.json).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
        print(f"✓ 已寫入 {args.json}", file=sys.stderr)

    pending = len(groups[NEW]) + len(groups[EMPTY]) + len(groups[DRAFT])
    return 1 if args.strict and pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
