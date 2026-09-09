#!/usr/bin/env python3
"""把「算得出來的欄位」寫回 Notion 產品用術語表。

## 這支和 glossary_audit.py 的分工

`glossary_audit.py` 是**唯讀報告**，給人看的。這一支是**寫回**，讓術語表裡那些
本來就該由機器維護的欄位保持最新。

## 只寫衍生欄位，絕不碰人的決定

    會寫：文件現況、OMS v0 現況、i18n key、一致性、文件出現次數、OMS 使用處數
    不碰：简体中文、繁體中文、已確認、備註、類型

這條界線是整支腳本最重要的設計。術語表要成為單一真實來源，靠的是「每一筆都有人
決定過」；腳本一旦能覆蓋 `简体中文` 或 `已確認`，那個保證就沒了。與外掛
`/tp/update` 永不覆蓋 `status=2` 是同一個原則。

## 為什麼「OMS 使用處數」比「文件出現次數」重要

前者是該英文字串對應幾個 i18n key ＝ 改動會影響產品幾個地方；後者是它在
Support Center 人工譯文裡出現幾次。2026-08-14 實測：文件次數 97 筆都是 1，
幾乎沒有鑑別度；OMS 處數分布在 1–14。而且 `Active` 在產品裡用了 10 處、
文件裡 0 次——只看文件次數會把影響面最大的詞排到最底。

兩個都留，因為它們量的是不同的事：產品改動的影響面 vs 文件翻譯的工作量。

## 執行

    ./.venv/bin/python scripts/glossary_sync.py --target test            # 預設 dry-run
    ./.venv/bin/python scripts/glossary_sync.py --target test --write

需要 .env 的 `NOTION_API_KEY`（整合要能存取產品用術語表），以及 `gh` 已登入
（用來抓 OMS 的 resources/lang）。
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict

import wp_env
from glossary_audit import fetch_tp_corpus

# 產品用術語表。⚠️ 這裡要的是 **database** ID，不是 collection／data source ID
# （後者是 aed72de7-d753-403d-b9c0-2d362c357205，丟給公開 API 會回 404
# object_not_found，而錯誤訊息只會說「請確認有分享給整合」，很容易誤判成權限問題）。
# 與 .env.example 對 NOTION_CONTENT_HUB_DB_ID 記下的是同一個坑。
GLOSSARY_DB = "1ab2891d5ddd48db97d1f1c1afeefcf5"
OMS_REPO = "Synct1fy/v0"
OMS_LANG_PATH = "resources/lang"

# 這些領域是框架與驗證訊息，不是產品 UI
SKIP_DOMAINS = {"admin", "pagination", "passwords", "validation", "auth"}

# 腳本可以寫的欄位。**不在這份清單裡的一律不碰**——尤其是人的決定。
DERIVED_FIELDS = ("文件現況", "OMS v0 現況", "i18n key",
                  "一致性", "文件出現次數", "OMS 使用處數")


def gh_json(path):
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"✗ gh api {path} 失敗：{out.stderr.strip()[:200]}")
    return json.loads(out.stdout)


def fetch_oms_lang():
    """從 repo 抓語言檔並攤平成 {en 小寫: {cn: set, keys: list}}。

    用 PHP 解析 return array()，比自己寫 parser 可靠——語言檔就是 PHP 陣列，
    交給 PHP 讀不會有引號、跳脫、巢狀的邊界問題。
    """
    import base64
    import tempfile
    import pathlib

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="oms-lang-"))
    for lang in ("en_US", "zh_CN"):
        (tmp / lang).mkdir(parents=True, exist_ok=True)
        for item in gh_json(f"repos/{OMS_REPO}/contents/{OMS_LANG_PATH}/{lang}"):
            if item.get("type") != "file" or not item["name"].endswith(".php"):
                continue
            blob = gh_json(f"repos/{OMS_REPO}/contents/{OMS_LANG_PATH}/{lang}/{item['name']}")
            (tmp / lang / item["name"]).write_bytes(base64.b64decode(blob["content"]))

    php = r'''<?php
function flat($a,$p=''){ $o=[]; foreach($a as $k=>$v){ $key=$p===''?(string)$k:"$p.$k";
  if(is_array($v)) $o+=flat($v,$key); elseif(is_string($v)) $o[$key]=$v; } return $o; }
$out=[]; foreach(['en_US','zh_CN'] as $L){ foreach(glob("$argv[1]/$L/*.php") as $f){
  $arr=include $f; if(!is_array($arr)) continue;
  foreach(flat($arr, basename($f,'.php')) as $k=>$v) $out[$k][$L]=$v; } }
echo json_encode($out, JSON_UNESCAPED_UNICODE);'''
    script = tmp / "flatten.php"
    script.write_text(php, encoding="utf-8")
    for php_bin in ("/Applications/XAMPP/xamppfiles/bin/php", "php"):
        r = subprocess.run([php_bin, str(script), str(tmp)], capture_output=True, text=True)
        if r.returncode == 0:
            break
    else:
        sys.exit("✗ 找不到可用的 php，無法解析語言檔")

    flat = json.loads(r.stdout)
    # 鍵維持小寫（寫入路徑靠它比對），另存一份原始大小寫供 --propose 顯示——
    # 候選清單是要給人直接貼進術語表的，全小寫還得手改。
    out = defaultdict(lambda: {"cn": set(), "keys": [], "en": ""})
    for k, v in flat.items():
        if k.split(".")[0] in SKIP_DOMAINS:
            continue
        en = (v.get("en_US") or "").strip()
        cn = (v.get("zh_CN") or "").strip()
        if not en or len(en) >= 50:
            continue
        out[en.lower()]["keys"].append(k)
        if not out[en.lower()]["en"]:
            out[en.lower()]["en"] = en
        if cn:
            out[en.lower()]["cn"].add(cn)
    return out


def plain(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def fetch_docs(wp, language):
    """Support Center 人工精修過的譯文 → {en 小寫: {cn: set, n: int}}"""
    out = defaultdict(lambda: {"cn": set(), "n": 0})
    for row in fetch_tp_corpus(wp, language):
        en = plain(row.get("original")).rstrip(":：").strip()
        cn = plain(row.get("translated")).rstrip(":：").strip()
        if en and cn and len(en) < 50:
            out[en.lower()]["cn"].add(cn)
            out[en.lower()]["n"] += 1
    return out


def notion(path, token, method="GET", body=None):
    req = urllib.request.Request(
        "https://api.notion.com/v1" + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"✗ Notion {method} {path} → HTTP {e.code}：{e.read().decode()[:300]}")


def fetch_glossary(token):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = notion(f"/databases/{GLOSSARY_DB}/query", token, "POST", body)
        for pg in data.get("results", []):
            title = pg["properties"].get("English", {}).get("title", [])
            rows.append({"id": pg["id"],
                         "english": "".join(t.get("plain_text", "") for t in title).strip(),
                         "props": pg["properties"]})
        if not data.get("has_more"):
            return rows
        cursor = data["next_cursor"]


# 明顯不是術語的值：帶佔位符、看起來是句子、或純標點數字。
# **刻意保守**——這份清單是給人讀的，混進雜訊就沒人會看完，
# 那跟沒有這份清單是一樣的結果（glossary_audit 指錯表就是這樣被忽略的）。
_PLACEHOLDER = re.compile(r":[a-zA-Z_]+")
_SENTENCE_END = re.compile(r"[.?!。？！]\s*$")


def looks_like_label(en):
    """判斷這個英文值像不像 UI 標籤（值得進術語表），而不是一句話。"""
    if not en or len(en) > 40:
        return False
    if _PLACEHOLDER.search(en) or _SENTENCE_END.search(en):
        return False
    if len(en.split()) > 4:
        return False
    if not re.search(r"[A-Za-z]", en):
        return False
    # 全小寫的多半是內部識別字（success、pending_review），不是畫面上的標籤
    return en[0].isupper()


def _prop(name, value):
    """把值包成 Notion 的屬性形狀。欄位型別是這張表的既有設計，不在這裡發明。"""
    if name == "English":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if name in ("OMS 使用處數", "文件出現次數"):
        return {"number": value}
    # 已確認是 **checkbox**，不是 select。2026-09-09 批次建列前先查了資料庫
    # schema 才發現——照 select 送會 123 筆全部失敗。
    if name == "已確認":
        return {"checkbox": bool(value)}
    if name in ("類型", "一致性"):
        return {"select": {"name": value}}
    return {"rich_text": [{"text": {"content": value[:2000]}}] if value else []}


def create_proposed(cands, token, dry=True):
    """把候選詞建成新列。**只新增，永不改動既有列。**

    ## 為什麼這不違反「絕不碰人的決定」

    模組說明那條界線講的是不能覆寫 `简体中文`／`已確認`／`備註` 等人填的欄位。
    這裡建的是**全新的、已確認=NO 的列**，沒有覆寫任何既有決定，反而是把
    「有人得決定這個詞」這件事登記下來。

    ## OMS 自己不一致的詞，簡繁兩欄留空

    留空而不是先挑一個填：填了就會進 prompt 去約束翻譯，等於讓腳本替人做了決定。
    空的話這個詞不影響翻譯，但已登記在表上，下次 --propose 不會重複提出。
    """
    created, skipped = [], []
    for c in cands:
        props = {"English": _prop("English", c["en"])}
        note = ("2026-09 起由 scripts/glossary_sync.py --propose 從 OMS 反向提出。\n"
                "先前的流程只替「已在表裡的詞」補欄位、從不提新詞，"
                "所以常用標籤會一直漏收。\n\n")
        if len(c["cn"]) == 1:
            props["简体中文"] = _prop("简体中文", c["cn"][0])
            note += "譯文取自 OMS 現況，**尚未經人審定**。\n\n"
        else:
            note += ("**簡繁兩欄刻意留空——OMS 自己就用了不只一種說法，需要有人拍板。**\n"
                     "OMS 現況：" + "／".join(c["cn"]) + "\n\n"
                     "留空而不是先填一個：填了就會進 prompt 去約束翻譯，"
                     "等於讓腳本替人做了決定。\n\n")
        note += ("⚠️ 繁體中文欄留空。這一批是批次補進來的，繁體是機械轉換、"
                 "無法逐筆驗證，填錯比留空糟——要用繁體時再逐筆補。")
        keys = c["keys"]
        props["i18n key"] = _prop("i18n key", "、".join(keys[:3])
                                  + (f" 等 {len(keys)} 個" if len(keys) > 3 else ""))
        props["OMS v0 現況"] = _prop("OMS v0 現況", "／".join(c["cn"]))
        props["OMS 使用處數"] = _prop("OMS 使用處數", len(keys))
        props["文件出現次數"] = _prop("文件出現次數", c.get("doc_hits", 0))
        props["一致性"] = _prop("一致性",
                                "OMS 自己不一致" if len(c["cn"]) > 1
                                else classify(c["cn"], c.get("doc_cn") or []))
        props["類型"] = _prop("類型", "UI 標籤")
        props["已確認"] = _prop("已確認", False)
        props["備註"] = _prop("備註", note)
        if c.get("doc_cn"):
            props["文件現況"] = _prop("文件現況", "／".join(c["doc_cn"][:2]))
        if dry:
            skipped.append(c["en"])
            continue
        notion("/pages", token, "POST",
               {"parent": {"database_id": GLOSSARY_DB}, "properties": props})
        created.append(c["en"])
    return created, skipped


def propose_from_oms(oms, glossary, docs, top=40, min_keys=1):
    """列出「OMS 裡有、術語表沒有」的候選詞。

    ## 為什麼需要這個方向

    2026-09-09 追查「為什麼 Integration、Preferences、Release Order 這些常用詞
    一直沒進表」時發現的空缺：這支腳本原本**只補既有列的欄位，從不提新詞**
    （設計如此，見模組說明的「只寫衍生欄位」）。所以一個詞若沒有人先手動輸入，
    不管它在 OMS 裡用了幾處都不會被提到。Integration 在人工譯文裡出現 50 次、
    OMS 裡 105 次，照樣漏掉。

    而負責「發現」的 glossary_audit 當時指向行銷用術語表，清單全是雜訊。
    兩支腳本各有缺口，交集就是「常用詞持續漏收」。

    ## 排序用 OMS 使用處數

    跟模組說明同一個理由：那是「改動會影響產品幾個地方」，比文件出現次數
    有鑑別度得多。

    **只報告，不寫入。** 譯法要由人決定，這裡給的是 OMS 現況供判讀。
    """
    known = {r["english"].strip().lower() for r in glossary if r["english"].strip()}
    out = []
    for en_lower, info in oms.items():
        if en_lower in known:
            continue
        keys = info["keys"]
        if len(keys) < min_keys or not info["cn"]:
            continue
        # 全部 key 都在 placeholders 底下 → 那是輸入框的**範例文字**，不是術語。
        # 2026-09-09 第一次跑 --create 前抓到：San Francisco、Suite 201 這種
        # 地址範例混進候選，進了表會被當成必須遵守的術語去約束翻譯。
        if all(".placeholders." in k or k.endswith(".placeholder") for k in keys):
            continue
        # 極短的英文字串不可靠。2026-09-09 實測：W 有三個 key，兩個是
        # shipping_width（宽）、一個是 unit_weight_short（重）——**兩種譯法
        # 各自都是對的**，是英文那邊用同一個縮寫表示寬度與重量。
        #
        # 這個索引以英文字串為鍵，一兩個字母的縮寫必然把不同概念撞在一起，
        # 看起來像「OMS 自己不一致」，其實不是。當時我就這樣誤報成產品 bug。
        #
        # 縮寫本來就依語境而定，填任何一個譯法都會讓另一個語境被譯錯，
        # 所以這種詞根本不該進術語表。
        if len(en_disp.strip()) <= 2:
            continue
        # 取原始大小寫：索引是小寫的，從 key 找不回來，用 cn 判斷不了，
        # 所以這裡只能用小寫比對、顯示時還原成 Title Case 的近似值。
        en_disp = info.get("en") or en_lower
        if not looks_like_label(en_disp):
            continue
        out.append({
            "en": en_disp,
            "cn": sorted(info["cn"]),
            "keys": keys,
            # fetch_docs 回的是 {en: {"cn": set, "n": int}}，不是計數——
            # 直接拿去排序會炸（TypeError: bad operand type for unary -）。
            "doc_hits": (docs.get(en_lower) or {}).get("n", 0),
            "doc_cn": sorted((docs.get(en_lower) or {}).get("cn", [])),
        })
    out.sort(key=lambda r: (-len(r["keys"]), -r["doc_hits"], r["en"].lower()))
    return out[:top]


def classify(oms_cn, doc_cn):
    if oms_cn and doc_cn:
        if len(oms_cn) > 1:
            return "OMS 自己不一致"
        return "一致" if set(oms_cn) == set(doc_cn) else "文件與 OMS 不一致"
    if oms_cn:
        return "OMS 自己不一致" if len(oms_cn) > 1 else "僅 OMS 有"
    if doc_cn:
        return "僅文件有"
    return "待比對"


def is_confirmed(props):
    """該列是否已被人工確認過。已確認＝人做過決定，腳本不得覆蓋。"""
    return bool((props.get("已確認") or {}).get("checkbox"))


def current(props, name):
    """讀出 Notion 現值，用來判斷有沒有變動（沒變就不送 request）。"""
    p = props.get(name) or {}
    if p.get("type") == "number":
        return p.get("number")
    if p.get("type") == "select":
        return (p.get("select") or {}).get("name")
    if p.get("type") == "rich_text":
        return "".join(t.get("plain_text", "") for t in p.get("rich_text", []))
    return None


def main():
    ap = argparse.ArgumentParser(description="把衍生欄位寫回 Notion 產品用術語表")
    wp_env.add_target_arg(ap, default="test")
    ap.add_argument("--write", action="store_true", help="實際寫入（預設只報告）")
    ap.add_argument("--propose", action="store_true",
                    help="只列出「OMS 裡有、術語表沒有」的候選詞，不寫入任何欄位")
    ap.add_argument("--top", type=int, default=40, help="--propose 列幾筆（預設 40）")
    ap.add_argument("--create", action="store_true",
                    help="把 --propose 的候選建成新列（已確認=NO）。"
                         "只新增，永不改動既有列")
    ap.add_argument("--only", choices=["labels", "undecided", "all"],
                    default="labels",
                    help="--create 要建哪一類：labels＝多字專有標籤（預設）、"
                         "undecided＝OMS 自己不一致的、all＝兩者。"
                         "**單字通用詞一律不建**——Connect／All／Add 這類詞"
                         "在散文裡會被誤套，稽核報告已看到 19–34 筆分歧")
    ap.add_argument("--language", default=None)
    args = ap.parse_args()

    env = wp_env.read_env()
    token = env.get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY。到 notion.so/my-integrations 建一個內部整合，\n"
                 "  把它加進『產品用術語表』的連線，再把密鑰填進 .env。")
    try:
        wp = wp_env.resolve(args.target)
    except wp_env.MissingCredentials as e:
        sys.exit(f"✗ {e}")

    print("讀取 OMS 語言檔…")
    oms = fetch_oms_lang()
    print(f"  {len(oms)} 個英文字串")
    print(f"讀取 {wp.label} 人工譯文…")
    docs = fetch_docs(wp, args.language or env.get("TP_TARGET_LANGUAGE", "zh_CN"))
    print(f"  {len(docs)} 個英文字串")
    print("讀取 Notion 術語表…")
    glossary = fetch_glossary(token)
    print(f"  {len(glossary)} 筆\n")

    if args.propose:
        cands = propose_from_oms(oms, glossary, docs, top=args.top)
        if args.create:
            # 單字通用詞一律排除，不管 --only 選什麼：Status／Cancel／Close
            # 這種詞進了表會強制套用到散文，稽核報告顯示既有的 Connect→开始对接
            # 已經造成 19 筆分歧（「connect with our community」被套成「开始对接」）。
            multi = [c for c in cands if len(c["en"].split()) > 1]
            pick = ([c for c in multi if len(c["cn"]) == 1] if args.only == "labels"
                    else [c for c in cands if len(c["cn"]) > 1] if args.only == "undecided"
                    else [c for c in multi if len(c["cn"]) == 1]
                         + [c for c in cands if len(c["cn"]) > 1])
            created, _ = create_proposed(pick, token, dry=not args.write)
            if args.write:
                print(f"✓ 已建立 {len(created)} 筆（已確認=NO）")
            else:
                print(f"（dry-run）會建立 {len(pick)} 筆——加 --write 才實際寫入")
                for c in pick[:10]:
                    print(f"    {c['en']}　→　{'／'.join(c['cn']) if len(c['cn'])==1 else '（留空，OMS 不一致）'}")
                if len(pick) > 10:
                    print(f"    …其餘 {len(pick)-10} 筆")
            return 0
        print("=" * 70)
        print(f"OMS 裡有、術語表沒有的候選詞（依 OMS 使用處數排序，前 {args.top}）")
        print("=" * 70)
        if not cands:
            print("  ✅ 沒有候選——OMS 的標籤都已收錄")
        for c in cands:
            cn = "／".join(c["cn"])
            print(f"\n  {c['en']}　→　{cn}")
            if c["doc_cn"]:
                print(f"    文件現況：{'／'.join(c['doc_cn'][:2])}")
            print(f"    OMS {len(c['keys'])} 處"
                  + (f"　文件 {c['doc_hits']} 次" if c["doc_hits"] else "")
                  + f"　{c['keys'][0]}"
                  + (f" 等 {len(c['keys'])} 個 key" if len(c["keys"]) > 1 else ""))
        print("\n" + "-" * 70)
        print("**只報告，不寫入。** 譯法要由人決定——上面給的是 OMS 現況供判讀，")
        print("同一個詞若列出多個中文，代表 OMS 自己就不一致，那更需要有人拍板。")
        return 0

    changed, unchanged, missing, locked = [], 0, [], []
    for row in glossary:
        key = row["english"].lower()
        o, d = oms.get(key), docs.get(key)
        if not o and not d:
            # 兩邊都比對不到就**整筆跳過**，不要把欄位清空。
            # 比對是「完全相符的字串」，而像 SSCC、ASIN 這種只出現在句子裡面、
            # 不是獨立詞條的詞，腳本本來就找不到——那不代表資訊不存在，
            # 只代表這支腳本無從驗證。擦掉人手寫的內容比留著舊值糟得多。
            missing.append(row["english"])
            continue
        oms_cn = sorted(o["cn"]) if o else []
        doc_cn = sorted(d["cn"]) if d else []

        want = {
            "文件現況": "／".join(doc_cn),
            "OMS v0 現況": "／".join(oms_cn),
            "i18n key": "、".join(o["keys"][:3]) if o else "",
            "一致性": classify(oms_cn, doc_cn),
            "文件出現次數": d["n"] if d else 0,
            "OMS 使用處數": len(o["keys"]) if o else 0,
        }
        diff = {k: v for k, v in want.items() if current(row["props"], k) != v}

        # 已確認的列：**回報差異但不寫入**。
        #
        # 起因（2026-09-08）：Cartons 一列底下混了三個語意不同的 i18n key
        # （carton_quantity=箱数、cartons_count=纸箱数 兩個是「數量」，
        # shipment_unit_carton=纸箱 是「物件」），Fay 決定拆成兩列。
        # 但這支腳本是用 English 當 key 去對 OMS 的——拆完之後兩列的 English
        # 都是 Cartons，會拿到同一份合併資料，**把拆分直接蓋回去**。
        #
        # 更一般地說：老闆已經審過這張表了，人工決定過的列不該被腳本重算。
        # 但也不能完全不看——OMS 之後改了字串，我們要知道。所以折衷成
        # 「照樣比對、照樣回報，就是不寫」。看得到漂移，也不會被覆蓋。
        if diff and is_confirmed(row["props"]):
            locked.append((row, diff))
            continue

        if diff:
            changed.append((row, diff))
        else:
            unchanged += 1

    print(f"需要更新 {len(changed)} 筆，已是最新 {unchanged} 筆")
    if locked:
        print(f"\n🔒 有 {len(locked)} 筆已確認、但與 OMS 現況不同——**只回報，不寫入**：")
        for row, d in locked[:10]:
            欄 = "、".join(f"{k}：{current(row['props'], k)!r} → {v!r}"
                           for k, v in list(d.items())[:2])
            print(f"   {row['english']}｜{欄}")
        if len(locked) > 10:
            print(f"   …另外 {len(locked) - 10} 筆")
        print("   （要重新採用 OMS 的值，把該列的「已確認」取消勾選再跑一次）")
    if missing:
        print(f"ℹ️ 有 {len(missing)} 筆兩邊都比對不到，**整筆跳過、原值保留**")
        print("   （多是只出現在句子裡、不是獨立詞條的詞，腳本無從驗證）：")
        print("   " + "、".join(missing[:12]) + ("…" if len(missing) > 12 else ""))

    if not args.write:
        print("\n這是 dry-run。以下是前 10 筆會有的改動：\n")
        for row, diff in changed[:10]:
            print(f"  {row['english']}")
            for k, v in diff.items():
                print(f"      {k}: {current(row['props'], k)!r} → {v!r}")
        print(f"\n確認後加 --write 實際寫入。**不會動到 简体中文／繁體中文／已確認／備註／類型**。")
        return 0

    for i, (row, diff) in enumerate(changed, 1):
        props = {}
        for k, v in diff.items():
            if k in ("文件出現次數", "OMS 使用處數"):
                props[k] = {"number": v}
            elif k == "一致性":
                props[k] = {"select": {"name": v}}
            else:
                props[k] = {"rich_text": [{"text": {"content": v[:2000]}}] if v else []}
        notion(f"/pages/{row['id']}", token, "PATCH", {"properties": props})
        if i % 20 == 0 or i == len(changed):
            print(f"  已更新 {i}／{len(changed)}")
    print("✓ 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
