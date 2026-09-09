#!/usr/bin/env python3
"""驗證 /tp/update 的「人工譯文不覆蓋」保護是否真的生效。

## 為什麼需要這支

`verify_endpoints.py` 已經有 /tp/update 的冒煙測試，但它用 **bogus id**——
只驗端點可達，不會碰到真實的 status=2 列。也就是說**保護邏輯本身從沒被執行過**。

這是整個專案風險最高的一段程式碼：心柔手工精修的譯文若被機器翻譯覆蓋，
沒有還原路徑。而自動翻譯上線後，我們就會開始大量往這支端點寫入。

## 兩面都要測

只測「status=2 沒被覆蓋」是不夠的——一個「什麼都不寫」的壞保護也會通過。
所以同時測正向：status=1 的列**必須寫得進去**。

正向測試刻意挑 status=1（機翻）而不是 status=0（未翻譯）的列：
寫入後狀態仍是 1，把原文寫回去就完全復原，測試站不留痕跡。
挑 status=0 的話寫完會變成 1，狀態回不去。

## 用法

    python scripts/verify_tp_guard.py                # 預設測試站
    python scripts/verify_tp_guard.py --target test

**不要對正式站跑。** 正向測試會實際寫入一列（雖然事後會還原）。
腳本對 target=prod 會直接拒絕。
"""
import argparse
import sys
import time

import httpx

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import wp_env  # noqa: E402

LANG = "zh_CN"
MARK = "__SYNCTIFY_GUARD_TEST__"


def get_strings(c, t, status, limit=3):
    r = c.get(f"{t.base}/wp-json/synctify/v1/tp/strings", auth=t.auth,
              params={"language": LANG, "status": status, "limit": limit})
    r.raise_for_status()
    d = r.json()
    return d.get("items") or d.get("strings") or []


def lookup(c, t, sid):
    """讀回單列的現況。用 /tp/strings 的 search 撈不精準，直接比對 id。"""
    for status in (0, 1, 2):
        for it in get_strings(c, t, status, limit=200):
            if int(it.get("id", -1)) == int(sid):
                return {"id": sid, "status": status,
                        "translated": it.get("translated") or ""}
    return None


def update(c, t, sid, text):
    r = c.post(f"{t.base}/wp-json/synctify/v1/tp/update", auth=t.auth,
               json={"language": LANG, "items": [{"id": sid, "translated": text}]})
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    wp_env.add_target_arg(ap, default="test")
    args = ap.parse_args()
    if args.target != "test":
        print("✗ 這支腳本只能對測試站跑——正向測試會實際寫入一列。")
        return 2

    t = wp_env.resolve(args.target)
    c = httpx.Client(timeout=30, follow_redirects=True)
    print(f"目標：{t.label}　{t.base}\n")
    fails = []

    # ── 反向：status=2 必須被擋 ──────────────────────────────
    human = get_strings(c, t, 2, limit=1)
    if not human:
        print("✗ 測試站沒有 status=2 的列，無法驗證保護")
        return 2
    hid = int(human[0]["id"])
    before = human[0].get("translated") or ""
    print(f"【反向】拿 id={hid}（人工譯文）試圖覆蓋")
    print(f"  覆蓋前：{before!r}")

    res = update(c, t, hid, MARK)
    print(f"  回應：{res}")
    ok_report = res.get("skipped_human") == 1 and res.get("updated") == 0
    print(f"  回報正確（skipped_human=1, updated=0）：{'✓' if ok_report else '✗'}")
    if not ok_report:
        fails.append("status=2 的列回報為已寫入")

    after = lookup(c, t, hid)
    actually_safe = after and after["translated"] == before and after["status"] == 2
    print(f"  重新讀回：{after['translated']!r}（status={after['status']}）"
          if after else "  重新讀回：找不到該列")
    print(f"  **譯文與狀態都沒被動**：{'✓' if actually_safe else '✗'}")
    if not actually_safe:
        fails.append(f"status=2 的列被改動了！id={hid} 原值 {before!r}")

    # ── 正向：status=1 必須寫得進去 ──────────────────────────
    # 沒有 status=1 的列時退而求其次用 status=0，並在最後說明狀態回不去。
    print()
    machine = get_strings(c, t, 1, limit=1)
    fallback = not machine
    if fallback:
        machine = get_strings(c, t, 0, limit=1)
    if not machine:
        print("✗ 找不到可寫入的列，正向測試跳過——**保護是否過度攔截無法確認**")
        fails.append("正向測試沒跑到")
    else:
        mid = int(machine[0]["id"])
        m_before = machine[0].get("translated") or ""
        src = "status=0（未翻譯）" if fallback else "status=1（機翻）"
        print(f"【正向】拿 id={mid}（{src}）確認寫得進去")
        if fallback:
            print("  ⚠️ 用 status=0 的列，寫完狀態會變 1、回不去 0")
        print(f"  寫入前：{m_before!r}")

        res2 = update(c, t, mid, MARK)
        print(f"  回應：{res2}")
        wrote = res2.get("updated") == 1 and res2.get("skipped_human") == 0
        print(f"  回報正確（updated=1）：{'✓' if wrote else '✗'}")
        time.sleep(0.5)
        got = lookup(c, t, mid)
        landed = got and got["translated"] == MARK
        print(f"  重新讀回：{got['translated']!r}" if got else "  重新讀回：找不到")
        print(f"  **確實寫進去了**：{'✓' if landed else '✗'}")
        if not (wrote and landed):
            fails.append("保護過度攔截：可寫入的列也沒寫進去")

        # 還原
        back = update(c, t, mid, m_before)
        restored = lookup(c, t, mid)
        ok_back = restored and restored["translated"] == m_before
        print(f"  還原：{'✓' if ok_back else '✗ 請手動改回 ' + repr(m_before)}"
              f"（回應 {back.get('updated')} 列）")
        if not ok_back:
            fails.append(f"還原失敗，id={mid} 原值 {m_before!r}")

    print("\n" + "=" * 60)
    if fails:
        print("✗ 未通過：")
        for f in fails:
            print("   -", f)
        return 1
    print("✓ 人工譯文不會被覆蓋，機器譯文寫得進去——保護正確")
    return 0


if __name__ == "__main__":
    sys.exit(main())
