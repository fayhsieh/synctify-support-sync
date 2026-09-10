#!/usr/bin/env python3
"""把自動翻譯的譯文，跟正式站同一段落的人工整句譯文逐段對照。

## 為什麼需要這支

測試站 post 7251 只有 11 段待翻，其餘 74 段早就人工精修過——測不出什麼。
2026-09-10 比對兩站後找到更好的測試對象：**正式站已發佈、測試站還沒有**的文章。
同步到測試站後整篇都是待翻，而正式站那篇有心柔的人工譯文可以當標準答案。

例：prod#7889 Manage Integrated Message Codes
    頁面可翻區塊 176；正式站人工譯文 172 筆，但其中
      整句（block_type=1） 21 筆，對得上頁面區塊 19 筆  ← 這些才能拿來對照
      片段（block_type=0）151 筆                        ← 舊的切碎譯文，不能直接比

## 對照的是整句，不是片段

片段是 TP 自動切的殘句（「to update the stock level.」），拿整句機器譯文去比
一定對不上，比了只會製造雜訊。所以只取 status=2 且 block_type=1 的列。

## 原文怎麼配對

測試站的頁面是從同一份 Notion 重新同步產生的，原文理論上與正式站相同，但轉換器
這段期間改過、Notion 內容也可能動過。先比「只統一換行」的原文，對不上再退一步比
「壓縮所有空白」的版本；兩者都對不上的列在報告裡列出來，不硬配。

## 用法

    python scripts/compare_with_prod.py --output 整理譯文.json --prod-post 7889
    python scripts/compare_with_prod.py --output 整理譯文.json --prod-post 7889 --md report.md

`--output` 是 n8n「整理譯文」節點的輸出（直接複製那段 JSON 存檔即可）。
正式站要連 VPN；看到 HTTP 202 就是 VPN 沒連上。
"""
import argparse
import json
import pathlib
import re
import sys

import httpx

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "converter"))
import wp_env  # noqa: E402
import tp_blocks  # noqa: E402


def load_items(path):
    """吃 n8n 輸出的幾種常見形狀：[{items:[…]}]、{items:[…]}、[…]。"""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list) and data and isinstance(data[0], dict) and "items" in data[0]:
        data = data[0]
    if isinstance(data, dict):
        data = data.get("items", [])
    return [x for x in data if isinstance(x, dict) and x.get("original")]


def key_exact(s):
    return tp_blocks.normalize(s or "").strip()


def key_loose(s):
    return re.sub(r"\s+", " ", s or "").strip()


def plain(s):
    t = tp_blocks.strip_tags(s or "")
    t = t.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def fetch_human(prod_post):
    t = wp_env.resolve("prod")
    c = httpx.Client(timeout=60, follow_redirects=True)
    rows, off = [], 0
    while True:
        r = c.get(f"{t.base}/wp-json/synctify/v1/tp/strings", auth=t.auth,
                  params={"language": "zh_CN", "post_id": prod_post, "status": 2,
                          "block_type": 1, "limit": 500, "offset": off})
        if r.status_code == 202:
            sys.exit("✗ 正式站回 HTTP 202——VPN 沒連上（WAF 白名單只放行 VPN 出口）")
        r.raise_for_status()
        got = r.json().get("items") or []
        rows += got
        if len(got) < 500:
            break
        off += 500
    # 端點若忽略 block_type 參數，這裡再過濾一次——片段混進來會製造假差異
    return [x for x in rows if int(x.get("block_type", 0)) == 1 and int(x.get("status", 0)) == 2]


def compare(items, human):
    exact = {key_exact(h["original"]): h for h in human}
    loose = {key_loose(h["original"]): h for h in human}
    same, diff, none = [], [], []
    for it in items:
        h = exact.get(key_exact(it["original"])) or loose.get(key_loose(it["original"]))
        if not h:
            none.append(it)
            continue
        rec = {"original": it["original"], "machine": it.get("translated", ""),
               "human": h.get("translated", "")}
        (same if key_loose(rec["machine"]) == key_loose(rec["human"]) else diff).append(rec)
    return same, diff, none


def render(same, diff, none, human_total, prod_post):
    out = []
    out.append(f"# 自動翻譯 vs 正式站人工譯文（prod#{prod_post}）\n")
    out.append(f"- 正式站人工整句譯文：{human_total} 筆")
    out.append(f"- 對得上的段落：{len(same) + len(diff)} 段（完全相同 {len(same)}、有差異 {len(diff)}）")
    out.append(f"- 正式站沒有人工整句譯文的段落：{len(none)} 段（無從對照）\n")
    if diff:
        out.append("## 有差異的段落\n")
        for i, d in enumerate(diff, 1):
            out.append(f"### {i}. {plain(d['original'])[:80]}\n")
            out.append(f"- **心柔**：{plain(d['human'])}")
            out.append(f"- **自動**：{plain(d['machine'])}\n")
    if same:
        out.append("## 完全相同的段落\n")
        for s in same:
            out.append(f"- {plain(s['original'])[:80]}")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", required=True, help="n8n「整理譯文」節點輸出的 JSON 檔")
    ap.add_argument("--prod-post", type=int, required=True, help="正式站上對應文章的 post id")
    ap.add_argument("--md", help="另存 Markdown 報告")
    args = ap.parse_args()

    items = load_items(args.output)
    if not items:
        sys.exit("✗ 輸入檔裡找不到 items（要貼的是「整理譯文」節點的輸出）")
    human = fetch_human(args.prod_post)
    same, diff, none = compare(items, human)
    report = render(same, diff, none, len(human), args.prod_post)
    print(report)
    if args.md:
        pathlib.Path(args.md).write_text(report, encoding="utf-8")
        print(f"✓ 已寫入 {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
