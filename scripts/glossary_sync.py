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
    out = defaultdict(lambda: {"cn": set(), "keys": []})
    for k, v in flat.items():
        if k.split(".")[0] in SKIP_DOMAINS:
            continue
        en = (v.get("en_US") or "").strip()
        cn = (v.get("zh_CN") or "").strip()
        if not en or len(en) >= 50:
            continue
        out[en.lower()]["keys"].append(k)
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

    changed, unchanged, missing = [], 0, []
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
        if diff:
            changed.append((row, diff))
        else:
            unchanged += 1

    print(f"需要更新 {len(changed)} 筆，已是最新 {unchanged} 筆")
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
