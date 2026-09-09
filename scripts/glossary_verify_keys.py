#!/usr/bin/env python3
"""檢查術語表裡的詞，OMS 畫面到底有沒有真的用到那個字串。

## 為什麼需要這支

2026-09-09 兩次踩到同一個坑：語言檔裡有翻譯，就以為那是使用者看得到的字。

  `remittance_advice.labels.trace_number` = '追踪号'
      → 程式碼零引用，畫面上從來沒出現過

  `order.on_hold.actions.release` = '解除挂起'
      → 同樣零引用。真正渲染按鈕的是 order.labels.release_on_hold_order，
        而**那個 key 的 zh_CN 根本不存在**，所以畫面顯示英文 'Release Order'

第二個尤其危險：我據此建了術語「Release Order = 解除挂起」，還替 Fay 擬了
要問老闆的問題（「保留」與「挂起」為何不一致）——整個前提是錯的。
Fay 打開實際畫面才發現按鈕根本不是那幾個字。

**語言檔有值 ≠ 使用者看得到。** 這支就是把這條界線畫出來。

## 五種引用方式都要算

漏掉任何一種都會產生假的「死字串」報告，而假警報比不檢查更糟——
它會讓人去改一個沒壞的東西。實測踩過的三種：

1. 完整字面 key    trans('order.labels.x')
2. 動態前綴        trans('order.statuses.' . $s)      → 前綴比對
3. 選單標題        admin_menu.title 存資料庫，渲染時組出
                   menu.titles.<lower_snake(title)>   → 視為一律有用
4. **部分 key**    oms_trans('labels.lineItem')       → 尾綴比對
                   全庫 734 處，漏掉這種會誤報 11 筆
5. **葉節點 key**  store.t('detail_about', 'About')   → 尾綴比對
                   Vue 元件連 domain 前綴都不給。漏掉這種會誤報 13 筆
                   ——2026-09-09 Fay 用瀏覽器逐一查證才發現

## 這份報告是「候選」不是「結論」

掃描器看不到所有可能的動態組法。列出來的每一筆仍然要人打開畫面確認過
才算數——就像 Fay 對 Release Order 做的那樣。

## 用法

    python scripts/glossary_verify_keys.py --src ~/somewhere/oms-src

`--src` 指向 OMS 原始碼（gh repo clone Synct1fy/v0 -- --depth 1）。
需要 .env 的 NOTION_API_KEY。
"""
import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import wp_env  # noqa: E402
import glossary_sync as gs  # noqa: E402

CODE_EXT = {".php", ".js", ".vue", ".ts"}
SKIP = ("/resources/lang/", "/vendor/", "/node_modules/", "/.git/")

FULL_KEY = re.compile(
    r"""['"]([a-z][a-z0-9_]*(?:\.[a-zA-Z0-9_:\-]+)+\.?)['"]""")
PART_KEY = re.compile(
    r"""(?:oms_trans|tenant_trans)\(\s*['"]([A-Za-z0-9_]+(?:\.[A-Za-z0-9_:\-]+)+)['"]""")
# Vue 元件用 store 自帶的翻譯器，key 只給**葉節點**、連 domain 前綴都沒有：
#     store.t('detail_about', 'About')
#     store.t('labels.applies_to', 'Applies to')
# 2026-09-09 漏掉這種，17 筆「死字串」裡有 13 筆其實畫面上看得到中文
# （Fay 用瀏覽器逐一查證才發現）。第二個參數是英文 fallback，不是 key。
STORE_T = re.compile(r"""\bstore\.t\(\s*['"]([A-Za-z0-9_][A-Za-z0-9_.:\-]*)['"]""")


def scan_code(root):
    """回傳 (完整 key, 動態前綴, 部分 key) 三個集合。"""
    full, prefix, partial = set(), set(), set()
    n = 0
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in CODE_EXT:
            continue
        p = str(f)
        if any(s in p for s in SKIP):
            continue
        n += 1
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in FULL_KEY.finditer(txt):
            k = m.group(1)
            (prefix if k.endswith(".") else full).add(k)
        for m in PART_KEY.finditer(txt):
            partial.add(m.group(1))
        for m in STORE_T.finditer(txt):
            partial.add(m.group(1))
    # 選單標題存在 admin_menu.title，渲染時才組出 menu.titles.<snake>，
    # 程式碼裡永遠找不到字面 key（見 tenant migration 2026_08_26 的註解）。
    prefix.add("menu.titles.")
    return full, prefix, partial, n


def make_checker(full, prefix, partial):
    def used(key):
        if key in full:
            return True
        if any(key.startswith(p) for p in prefix):
            return True
        # oms_trans('labels.lineItem') 命中 order.labels.lineItem
        return any(key.endswith("." + s) or key == s for s in partial)
    return used


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True,
                    help="OMS 原始碼目錄（gh repo clone Synct1fy/v0 -- --depth 1）")
    ap.add_argument("--json", help="把死字串清單寫成 JSON")
    args = ap.parse_args()

    root = pathlib.Path(args.src).expanduser()
    if not root.is_dir():
        sys.exit(f"✗ 找不到 {root}")

    env = wp_env.read_env()
    token = env.get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")

    print(f"掃描 {root}…")
    full, prefix, partial, n = scan_code(root)
    used = make_checker(full, prefix, partial)
    print(f"  {n:,} 個檔案　完整 key {len(full):,}　"
          f"動態前綴 {len(prefix)}　部分 key {len(partial):,}\n")

    oms = gs.fetch_oms_lang()
    glossary = gs.fetch_glossary(token)
    dead, live, unknown = [], 0, []
    for row in glossary:
        en = row["english"].strip()
        if not en:
            continue
        info = oms.get(en.lower())
        if not info:
            unknown.append(en)
            continue
        if any(used(k) for k in info["keys"]):
            live += 1
        else:
            dead.append((en, gs.current(row["props"], "简体中文") or "（留空）",
                         info["keys"]))

    print(f"  畫面有用到　{live} 筆")
    print(f"  OMS 沒有這個字串　{len(unknown)} 筆（多半是文件自有術語）")
    print(f"  ⚠️ 語言檔有值但畫面沒引用　{len(dead)} 筆\n")
    print("=" * 70)
    print("以下是**候選**，不是結論——每一筆都要打開實際畫面確認過才算數。")
    print("語言檔有值不代表使用者看得到；反過來，畫面顯示英文也可能只是")
    print("該 key 的 zh_CN 缺譯（那是工程要補，不是文件該跟著寫英文）。")
    print("=" * 70)
    for en, zh, keys in sorted(dead, key=lambda r: (-len(r[2]), r[0])):
        print(f"\n  {en}　→　{zh}")
        for k in keys[:3]:
            print(f"      {k}")
        if len(keys) > 3:
            print(f"      …等 {len(keys)} 個")

    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps([{"en": e, "zh": z, "keys": k} for e, z, k in dead],
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n✓ 已寫入 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
