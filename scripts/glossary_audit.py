#!/usr/bin/env python3
"""詞彙對照表對帳（**唯讀**，不會改動 Notion 或 WordPress）。

## 為什麼需要這支

詞彙對照表被賦予的任務是成為術語的**單一真實來源**——不只支援文件，之後工程
團隊開發 UI 也要照它翻，這樣文件用語才不會跟產品實際介面脫鉤。

但目前它只有 38 筆，而 TranslatePress 裡人工精修過的譯文（status=2）有 1000 筆
以上。真正累積術語決定的地方是後者，而那些決定從來沒有回流到表裡。差一個數量級
的脫鉤已經存在，不是未來的風險。

這支腳本把兩邊擺在一起比對，產出**可審核的清單**：

  1. 簡繁檢查   —— 简体中文 欄裡混進繁體字（會複製到每一篇機器譯文裡）
  2. 一致性對帳 —— 詞彙表指定的譯法，站上的人工譯文有沒有照著用
  3. 候選新詞   —— 站上反覆出現、但表裡沒有的術語，附人工譯文供判讀

**只報告，不自動寫入。** 分歧不代表誰錯——校閱者可能是對的，也可能那個語境本來
就該用別的詞。要成為單一真實來源，每一筆都得有人決定過，不能是腳本塞進去的。

## 執行

    ./.venv/bin/python scripts/glossary_audit.py --target test
    ./.venv/bin/python scripts/glossary_audit.py --target test --glossary-json <檔>

詞彙表來源：.env 的 `NOTION_API_KEY`（沒填就用 --glossary-json 提供的快照）。
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

import wp_env

GLOSSARY_DB = "28d2f2ed-e27d-805b-90f3-000b73c8a2af"

# 繁體專用字（簡體寫法不同者）。**刻意保守，不求窮盡**——目的是攔下「整段簡體裡
# 混一個繁體字」這種實際發生過的錯誤（用戶、預定演示、系統设定），不是做完整的
# 簡繁轉換。漏判只是少報一筆；誤判會讓人去追不存在的問題，所以寧可列少。
# 每個字都確認過簡體寫法不同（例：收「後」不收「后」，因為「后」兩邊都用）。
TRADITIONAL_ONLY = (
    "個們這來對開關時間過說讓應該業務發現產單據數頁麼認識資訊網檔設選擇確執顯"
    "錯誤儲匯傳連結標題內編輯刪條範圍類別屬參帳號碼權訂庫倉運費價報圖檢驗證"
    "買賣員團隊專際樣種點線級統計錄態動進遠邊車東馬為與舉學覺觀規視親見語論"
    "講誰課調談請讀變質輸轉較載輪適還郵醫釋鐘鋼錢鎖鏡鐵長門閉閒陣陰陽隨隱雖"
    "雙雜雞離難電靈韓順須預領頭顆願顧風飛飯館駕體髮鬥麗黃齡戶後幹隻係繫臺裡"
    "裏麵穀萬與豐雲屬歲obsolete"
)
# 上面那段結尾混進 Latin 會讓字元類命中任何英文字母——組完就檢查一次，
# 這種錯誤靜靜地存在的話，整份報告會變成滿江紅。
TRADITIONAL_ONLY = "".join(ch for ch in TRADITIONAL_ONLY if ord(ch) > 0x2E80)
assert TRADITIONAL_ONLY and not re.search(r"[A-Za-z0-9]", TRADITIONAL_ONLY)
TRAD_RE = re.compile("[" + re.escape(TRADITIONAL_ONLY) + "]")


def _selftest_traditional():
    """實際踩過的三個錯誤要抓得到，正確的簡體不能誤判。"""
    for bad in ("用戶", "預定演示", "系統设定"):
        assert TRAD_RE.search(bad), bad
    for good in ("用户", "预定演示", "系统设定", "订单管理", "库存同步",
                 "API 接口", "面板／仪表板", "自动化", "支持中心", "合作伙伴"):
        hit = TRAD_RE.search(good)
        assert not hit, f"{good} 誤判為繁體：{hit.group()}"


_selftest_traditional()

# 候選新詞：抓 Title Case 的連續詞——UI 標籤長這樣（Release Order、On-Hold）
TITLECASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]*(?:[- ][A-Z][a-zA-Z0-9]*){0,3})\b")

# 站上到處都是、但不是術語的字
STOPWORDS = {
    "The", "This", "That", "You", "Your", "We", "It", "If", "When", "Click",
    "Select", "Enter", "Go", "For", "From", "Note", "In", "On", "To", "A", "An",
    "And", "Or", "Of", "Once", "After", "Before", "Please", "Synctify", "OMS",
    "Last", "May", "June", "July", "August", "I", "Step", "Overview",
}


def fetch_glossary_from_notion(token):
    """用 Notion REST API 取回詞彙表。回傳 [{english, zh_cn, zh_tw, category, note}]"""
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(
            f"https://api.notion.com/v1/databases/{GLOSSARY_DB}/query",
            data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
        for pg in data.get("results", []):
            p = pg.get("properties", {})
            rows.append({
                "english": _plain(p.get("English")),
                "zh_cn": _plain(p.get("简体中文")),
                "zh_tw": _plain(p.get("繁體中文")),
                "category": (p.get("類別") or {}).get("select", {}).get("name") or "",
                "note": _plain(p.get("備註")),
            })
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


def _plain(prop):
    if not prop:
        return ""
    for key in ("title", "rich_text"):
        if key in prop:
            return "".join(t.get("plain_text", "") for t in prop[key]).strip()
    return ""


def fetch_tp_corpus(wp, language, status=2):
    """撈出人工精修過的譯文。分頁取完，回傳 [{original, translated}]"""
    out, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"language": language, "status": status,
                                    "limit": 500, "offset": offset})
        req = urllib.request.Request(
            f"{wp.base}/wp-json/synctify/v1/tp/strings?{q}",
            headers={"Authorization": wp.basic_auth_header(),
                     "User-Agent": "synctify-glossary-audit/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            sys.exit(f"✗ 取字典表失敗 HTTP {e.code}：{e.read().decode()[:200]}")
        out.extend(d.get("items", []))
        offset += d.get("limit", 500)
        if offset >= d.get("total", 0):
            return out


def variants(zh):
    """詞彙表允許「甲 / 乙」並列，任一命中都算一致。"""
    return [v.strip() for v in re.split(r"[／/、]", zh or "") if v.strip()]


def check_traditional(glossary):
    bad = []
    for g in glossary:
        hits = sorted(set(TRAD_RE.findall(g["zh_cn"])))
        if hits:
            bad.append((g["english"], g["zh_cn"], hits))
    return bad


def check_consistency(glossary, corpus):
    """詞彙表指定的譯法，站上的人工譯文有沒有照著用。"""
    report = []
    for g in glossary:
        en, zh = g["english"], g["zh_cn"]
        if not en or not zh:
            continue
        # 英文詞可能寫成「Import / Export」這種並列，逐個找
        terms = [t.strip() for t in re.split(r"[／/]", en) if t.strip()]
        # 要有詞邊界，否則 Important Note 會被當成 Import、Retail Link 當成 Link。
        # 誤判比漏判傷害大——一份滿是假警報的報告沒有人會拿來當依據。
        pat = re.compile("|".join(r"\b%s\b" % re.escape(t) for t in terms), re.I)
        want = variants(zh)

        agree, differ = 0, []
        for row in corpus:
            if not pat.search(row.get("original") or ""):
                continue
            tr = row.get("translated") or ""
            if any(v and v in tr for v in want):
                agree += 1
            else:
                differ.append(row)
        if agree or differ:
            report.append({"english": en, "zh_cn": zh, "agree": agree,
                           "differ": differ, "category": g["category"]})
    return report


def find_candidates(glossary, corpus, min_count=3, top=25):
    """站上反覆出現、但表裡沒有的術語。"""
    known = set()
    for g in glossary:
        for t in re.split(r"[／/]", g["english"] or ""):
            if t.strip():
                known.add(t.strip().lower())

    counts, samples = Counter(), {}
    for row in corpus:
        orig = row.get("original") or ""
        for m in TITLECASE_RE.finditer(orig):
            term = m.group(1).strip()
            if len(term) < 3 or term in STOPWORDS or term.lower() in known:
                continue
            # 單字候選只在「非句首」時採計。句首本來就大寫，否則 Review／Use／
            # Choose 這些祈使句動詞會被當成術語，把真正的術語擠出排行。
            # 多字的 Title Case（Release Order、Frozen Period）不受此限。
            if " " not in term and "-" not in term:
                before = orig[:m.start()].rstrip()
                if not before or before[-1] in ".!?:;":
                    continue
            counts[term] += 1
            samples.setdefault(term, []).append(row)
    return [(t, n, samples[t][:3]) for t, n in counts.most_common(top) if n >= min_count]


def main():
    ap = argparse.ArgumentParser(description="詞彙對照表對帳（唯讀）")
    wp_env.add_target_arg(ap, default="test")
    ap.add_argument("--glossary-json", help="詞彙表快照（.env 沒有 NOTION_API_KEY 時用）")
    ap.add_argument("--language", default=None, help="預設取 .env 的 TP_TARGET_LANGUAGE")
    args = ap.parse_args()

    try:
        wp = wp_env.resolve(args.target)
    except wp_env.MissingCredentials as e:
        sys.exit(f"✗ {e}")
    env = wp_env.read_env()
    language = args.language or env.get("TP_TARGET_LANGUAGE", "zh_CN")

    if args.glossary_json:
        glossary = json.loads(open(args.glossary_json, encoding="utf-8").read())
    elif env.get("NOTION_API_KEY"):
        glossary = fetch_glossary_from_notion(env["NOTION_API_KEY"])
    else:
        sys.exit("✗ .env 沒有 NOTION_API_KEY，請改用 --glossary-json 提供快照")

    corpus = fetch_tp_corpus(wp, language)
    print(f"\n詞彙對照表 {len(glossary)} 筆　×　{wp.label} 人工譯文 {len(corpus)} 筆"
          f"（{language}）\n" + "=" * 70)

    # ── 1 ──
    print("\n【1】簡繁檢查：简体中文 欄裡的繁體字")
    bad = check_traditional(glossary)
    if not bad:
        print("  ✅ 沒有發現")
    for en, zh, hits in bad:
        print(f"  ❌ {en:<28} {zh!r}　繁體字：{'、'.join(hits)}")

    # ── 2 ──
    print("\n【2】一致性對帳：站上的人工譯文有沒有照詞彙表翻")
    rep = check_consistency(glossary, corpus)
    used = [r for r in rep if r["agree"] or r["differ"]]
    full = [r for r in used if not r["differ"]]
    print(f"  站上有出現的詞：{len(used)}／{len(glossary)}　"
          f"完全一致：{len(full)}　有分歧：{len(used) - len(full)}")
    for r in sorted(used, key=lambda x: -len(x["differ"])):
        if not r["differ"]:
            continue
        print(f"\n  ⚠️  {r['english']}　詞彙表：{r['zh_cn']}　"
              f"（一致 {r['agree']}／分歧 {len(r['differ'])}）")
        for row in r["differ"][:2]:
            print(f"        EN {(row.get('original') or '')[:78]}")
            print(f"        CN {(row.get('translated') or '')[:78]}")

    # ── 3 ──
    print("\n\n【3】候選新詞：站上反覆出現、詞彙表沒有的")
    cands = find_candidates(glossary, corpus)
    if not cands:
        print("  （沒有）")
    for term, n, rows in cands:
        print(f"\n  {term}　出現 {n} 次")
        for row in rows[:2]:
            print(f"        EN {(row.get('original') or '')[:76]}")
            print(f"        CN {(row.get('translated') or '')[:76]}")

    print("\n" + "=" * 70)
    print("以上只是報告。要成為單一真實來源，每一筆都得有人決定過才入表——"
          "腳本不會自動寫入 Notion。")


if __name__ == "__main__":
    sys.exit(main())
