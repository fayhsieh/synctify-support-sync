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
  - .env 需填 WP_BASE_URL / WP_USERNAME / WP_APP_PASSWORD（及選填 TP_TARGET_LANGUAGE）
  - 轉換 service 需先啟動（預設 http://127.0.0.1:8800，可用環境變數 CONVERTER_URL 覆蓋）：
        ./.venv/bin/python -m uvicorn service.app:app --port 8800

執行：
    ./.venv/bin/python scripts/verify_endpoints.py

備註：測試站若被 SSO/OAuth 閘門擋在最前面，所有請求會 302 轉去登入頁而到不了
WordPress——此時需先在閘門放行 /wp-json/ 或提供可通過 proxy 的憑證。
"""
import os
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---- 讀 .env ----
env = {}
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    env[k.strip()] = v.strip()

WP = env["WP_BASE_URL"].rstrip("/")
USER = env["WP_USERNAME"]
PW = env["WP_APP_PASSWORD"].replace(" ", "")  # WP app password 去空白
LANG = env.get("TP_TARGET_LANGUAGE", "zh_CN")
CONV = os.environ.get("CONVERTER_URL", "http://127.0.0.1:8800").rstrip("/")

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
        record("5. tp/update 寫入（冒煙，bogus id）", True, f"{short(r)}（未命中真實列）")
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
