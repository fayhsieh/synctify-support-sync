#!/usr/bin/env python3
"""
對測試站的 /synctify/v1/ 端點各實打一次驗證（含認證與清理）。

驗證內容：
  0. 轉換 HTTP service /convert（產生真實 elementor_data）
  1. POST /synctify/v1/elementor/{id}      寫入 + 產生備份
  2. POST /synctify/v1/elementor/{id}/restore  還原備份
  3. POST /synctify/v1/seo/{id}            AIOSEO meta
  4. POST /synctify/v1/tp/lookup           TP 字典查詢（唯讀）
  5. POST /synctify/v1/tp/update           TP 寫入（bogus id 冒煙，不動真實譯文）
  6. GET  /wp/v2/faq、/wp/v2/faq-group     Arconix FAQ REST
  + 認證負向測試（無憑證應 401）

安全設計：建一篇專用測試草稿當標的，不碰既有內容；結束時把草稿丟垃圾桶（非永久刪除）。

前置：
  - .env 需填該站台的 WP_USERNAME / WP_APP_PASSWORD（測試站是 _TEST 後綴，見 wp_env）
    及選填 TP_TARGET_LANGUAGE
  - 轉換 service 需先啟動（預設 http://127.0.0.1:8800，可用環境變數 CONVERTER_URL 覆蓋）：
        ./.venv/bin/python -m uvicorn service.app:app --port 8800

執行：
    ./.venv/bin/python scripts/verify_endpoints.py                  # 測試站（預設）
    ./.venv/bin/python scripts/verify_endpoints.py --target prod    # 需二次確認

**這支腳本會寫入**（建草稿、寫 Elementor、寫 SEO、寫 TP），所以預設打測試站，
跟唯讀的 verify_site_ready.py 相反。要打正式站必須自己加 --target prod，
而且會再問一次——正式站上多一篇垃圾桶裡的草稿雖然不致命，但那是小編在用的站。

備註：測試站若被 SSO/OAuth 閘門擋在最前面，所有請求會 302 轉去登入頁而到不了
WordPress——此時需先在閘門放行 /wp-json/ 或提供可通過 proxy 的憑證。
"""
import argparse
import os
import sys

import httpx

import wp_env

ROOT = wp_env.ROOT

# ---- 選站台、讀 .env ----
_ap = argparse.ArgumentParser(description="對 /synctify/v1/ 端點各實打一次（會寫入）")
wp_env.add_target_arg(_ap, default="test", help_extra="這支會寫入，所以預設測試站。")
_ap.add_argument("--yes", action="store_true", help="跳過打正式站時的二次確認")
_args = _ap.parse_args()

try:
    _wp = wp_env.resolve(_args.target)
except wp_env.MissingCredentials as e:
    sys.exit(f"✗ {e}")

if _wp.target == "prod" and not _args.yes:
    print(f"⚠️  即將對{_wp.label}（{_wp.base}）執行**會寫入**的驗證："
          f"\n    建立一篇測試草稿、寫 Elementor 版面與 SEO meta，結束後丟垃圾桶。")
    if input("    確定要繼續嗎？輸入 yes：").strip().lower() != "yes":
        sys.exit("已取消。")

WP = _wp.base
USER, PW = _wp.user, _wp.password
env = wp_env.read_env()
LANG = env.get("TP_TARGET_LANGUAGE", "zh_CN")
CONV = os.environ.get("CONVERTER_URL", "http://127.0.0.1:8800").rstrip("/")

print(f"驗證目標：{WP}（{_wp.label}）\n")
auth = httpx.BasicAuth(USER, PW)
client = httpx.Client(timeout=30.0)

results = []


def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"{'✅' if ok else '❌'} {name}: {detail}")


def short(r):
    body = r.text
    return f"HTTP {r.status_code} {body[:180] + '…' if len(body) > 180 else body}"


def guard_gate(r):
    """偵測 SSO/OAuth 閘門攔截（整站被轉去登入頁）。"""
    if r.status_code in (301, 302) and "accounts.google.com" in (r.headers.get("location") or ""):
        return True
    return False


# ---- 0. 轉換 service → elementor_data ----
try:
    md = (ROOT / "samples" / "amazon-sc-v2-notion.md").read_text()
    r = client.post(f"{CONV}/convert", json={
        "markdown": md, "title": "ENDPOINT TEST — safe to delete",
        "faq_group": "endpoint-test", "sync_date": "July 22, 2026"})
    r.raise_for_status()
    conv = r.json()
    elementor_data = conv["template"]["content"]
    record("converter /convert", True,
           f"{len(elementor_data)} containers, {len(conv['faq_items'])} FAQ")
except Exception as e:
    record("converter /convert", False, f"轉換服務未就緒：{e}")
    print("\n轉換服務未就緒，中止。先啟動：./.venv/bin/python -m uvicorn service.app:app --port 8800")
    sys.exit(1)

# ---- 前置探測：REST 是否被閘門擋住 ----
probe = client.get(f"{WP}/wp-json/", follow_redirects=False)
if guard_gate(probe):
    record("測試站 REST 可達性", False,
           "整站被 SSO/OAuth 閘門攔截（302 → accounts.google.com），流量到不了 WordPress")
    print("\n閘門未放行，無法驗證 WP 端點。需先在閘門把 /wp-json/ 設為例外或提供可通過 proxy 的憑證。")
    sys.exit(2)
record("測試站 REST 可達性", True, f"/wp-json/ HTTP {probe.status_code}")

# ---- 建測試草稿 ----
test_post_id = None
try:
    r = client.post(f"{WP}/wp-json/wp/v2/docs", auth=auth, json={
        "title": "SYNCTIFY ENDPOINT TEST — safe to delete",
        "status": "draft", "content": "endpoint verification placeholder"})
    if r.status_code in (200, 201):
        test_post_id = r.json()["id"]
        record("建立測試草稿 (POST /wp/v2/docs)", True, f"post_id={test_post_id} (draft)")
    else:
        record("建立測試草稿 (POST /wp/v2/docs)", False, short(r))
except Exception as e:
    record("建立測試草稿 (POST /wp/v2/docs)", False, str(e))

if test_post_id:
    # 1. elementor 寫入 + 備份
    try:
        r = client.post(f"{WP}/wp-json/synctify/v1/elementor/{test_post_id}",
                        auth=auth, json={"elementor_data": elementor_data})
        record("1. elementor 寫入", r.status_code == 200 and r.json().get("ok") is True, short(r))
        r2 = client.post(f"{WP}/wp-json/synctify/v1/elementor/{test_post_id}",
                         auth=auth, json={"elementor_data": elementor_data})
        record("   elementor 再寫一次（產生備份）", r2.status_code == 200,
               f"backups_kept={r2.json().get('backups_kept')}")
    except Exception as e:
        record("1. elementor 寫入", False, str(e))

    # 2. 還原備份
    try:
        r = client.post(f"{WP}/wp-json/synctify/v1/elementor/{test_post_id}/restore",
                        auth=auth, json={"index": 0})
        record("2. elementor 還原備份", r.status_code == 200 and r.json().get("ok") is True, short(r))
    except Exception as e:
        record("2. elementor 還原備份", False, str(e))

    # 3. SEO meta
    try:
        r = client.post(f"{WP}/wp-json/synctify/v1/seo/{test_post_id}",
                        auth=auth, json={"title": "Endpoint Test Title",
                                         "description": "Endpoint test meta description."})
        if r.status_code == 200 and r.json().get("ok") is True:
            record("3. SEO meta 寫入", True, short(r))
        elif r.status_code == 501:
            record("3. SEO meta 寫入", True, f"端點可達但 AIOSEO 未啟用（{short(r)}）")
        else:
            record("3. SEO meta 寫入", False, short(r))
    except Exception as e:
        record("3. SEO meta 寫入", False, str(e))

# 4. tp/lookup（唯讀）
try:
    r = client.post(f"{WP}/wp-json/synctify/v1/tp/lookup", auth=auth,
                    json={"language": LANG,
                          "strings": ["Overview", "Authorize Now", "__synctify_nonexistent_probe__"]})
    if r.status_code == 200:
        rows = r.json()
        record("4. tp/lookup 查詢", True,
               f"回傳 {len(rows)} 筆，status={{{', '.join(str(row['status']) for row in rows)}}}")
    elif r.status_code == 501:
        record("4. tp/lookup 查詢", True, f"端點可達但 TP 未設定（{short(r)}）")
    else:
        record("4. tp/lookup 查詢", False, short(r))
except Exception as e:
    record("4. tp/lookup 查詢", False, str(e))

# 5. tp/update（bogus id，不動真實譯文）
try:
    r = client.post(f"{WP}/wp-json/synctify/v1/tp/update", auth=auth,
                    json={"language": LANG,
                          "items": [{"id": 999999999, "translated": "__synctify_probe__"}]})
    if r.status_code == 200:
        body = r.json()
        # 不存在的 id 必須回報 not_found，不可算進 updated
        # （若算進 updated，過期 id 會造成「宣稱寫入成功但一列都沒寫」的靜默失敗）
        ok = body.get("not_found") == 1 and body.get("updated") == 0
        record("5. tp/update 寫入（冒煙，bogus id）", ok,
               f"{short(r)}" + ("" if ok else "  ⚠️ 期望 not_found=1/updated=0，外掛可能是舊版"))
    elif r.status_code == 501:
        record("5. tp/update 寫入（冒煙）", True, f"端點可達但 TP 未設定（{short(r)}）")
    else:
        record("5. tp/update 寫入（冒煙）", False, short(r))
except Exception as e:
    record("5. tp/update 寫入（冒煙）", False, str(e))

# 6. Arconix FAQ REST
try:
    r = client.get(f"{WP}/wp-json/wp/v2/faq", auth=auth, params={"per_page": 1})
    record("6. Arconix FAQ REST (/wp/v2/faq)", r.status_code == 200,
           f"HTTP {r.status_code}")
    rg = client.get(f"{WP}/wp-json/wp/v2/faq-group", auth=auth, params={"per_page": 1})
    record("   faq-group taxonomy REST", rg.status_code == 200, f"HTTP {rg.status_code}")
except Exception as e:
    record("6. Arconix FAQ REST", False, str(e))

# 認證負向測試
try:
    r = client.post(f"{WP}/wp-json/synctify/v1/tp/lookup",
                    json={"language": LANG, "strings": ["x"]})
    record("認證檢查（無憑證應 401）", r.status_code == 401, f"HTTP {r.status_code}（預期 401）")
except Exception as e:
    record("認證檢查（無憑證應 401）", False, str(e))

# 清理：測試草稿丟垃圾桶（非永久刪除）
if test_post_id:
    try:
        r = client.delete(f"{WP}/wp-json/wp/v2/docs/{test_post_id}", auth=auth)
        record("清理：測試草稿移到垃圾桶", r.status_code == 200,
               f"HTTP {r.status_code}（post_id={test_post_id}）")
    except Exception as e:
        record("清理：測試草稿移到垃圾桶", False, str(e))

# 總結
print("\n" + "=" * 50)
passed = sum(1 for _, ok, _ in results if ok)
print(f"結果：{passed}/{len(results)} 通過")
failed = [n for n, ok, _ in results if not ok]
if failed:
    print("未通過：" + ", ".join(failed))
    sys.exit(1)
