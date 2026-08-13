#!/usr/bin/env python3
"""
上稿流程搬站前的前置檢查（**唯讀**，不會寫入任何資料）。

用途：把流程搬到另一個站台之前，先確認那個站台具備所有先決條件。少一項就會在
第一次同步時失敗——例如分類頁不存在會讓 /doc/defaults 回 422 直接卡住。

    ./.venv/bin/python scripts/verify_site_ready.py                  # 正式站（預設）
    ./.venv/bin/python scripts/verify_site_ready.py --target test    # 測試站
    ./.venv/bin/python scripts/verify_site_ready.py --base https://…  # 只換網址

帳密由 `wp_env` 依 --target 取自 .env（無後綴＝正式站、_TEST＝測試站）。
兩站的 Application Password 各自獨立，拿錯那組會 401。

⚠️ 全部是 GET 與唯讀的 POST（帶空 body 的端點只回報差異、不寫入），
可以安全地對正式站執行。

已知環境限制（2026-08-13 更新）：正式站裝了 miniOrange 的 REST API
Authentication 外掛，免費版把 `/wp-json/` 索引與所有自訂命名空間擋成 403
Restricted、內建端點回 401，所以這支腳本會停在檢查 1。那不是站台或憑證的
問題，`diagnose()` 會據回應特徵指出來。AWS WAF 那層已於同日從 n8n 實測排除。
"""
import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request

import wp_env

# 站台相依的東西一律以「名稱」定義——這也是搬站不必改 ID 的原因，
# 與 wp-plugin/synctify-sync-helper.php 的常數必須一致。
DOC_ROOT = "Synctify Documentation"
FEATURED_SLUG = "opengraph"
AUTHOR_NAME = "The Synctify Team"

# Notion Category → 分類頁標題（去掉序號前綴）。見 docs/mapping-rules.md §六之二。
# 「9. Automation」站上尚無對應分類頁，屬已知缺口，不列為失敗。
CATEGORIES = ["Getting Started", "Settings", "Products", "Integrations", "Orders",
              "Inventory", "Reports", "Overview", "Finance", "Troubleshooting"]
KNOWN_MISSING_CATEGORY = "Automation"

# Notion 母列記著的 WP Post ID → 該篇標題（2026-08-12 由 Notion 取得；
# Fay 已逐列與正式站對齊過，所以這是「正式站應有的樣子」，不是測試站的快照）。
# 標題由 Doc name 去掉管理編號前綴與結尾的全形括號備註而來。
#
# 對不上代表 Notion 的 WP Post ID 在目標站台指向別篇文章——同步會把 Elementor
# 草稿寫進錯的文章。這是整份檢查裡最重要的一項。
#
# ⚠️ 這份對照表對準的是**正式站**，所以 `--target test` 必定會有兩筆對不上：
# 7761 / 7802 是正式站人工發佈後回填的 ID，測試站上沒有那兩篇。那是預期的，
# 不是測試站壞了。
EXPECTED_POSTS = {
    5601: "Configure Warehouse",
    5620: "Manage User Access",
    5635: "Add & Edit Categories",
    5868: "Add/Edit/Delete Variant Attributes",
    5883: "Add/Edit/Archive Product",
    5907: "Configure SKU Aliases",
    5921: "Connect Integrations",
    6041: "Manage Sales Orders",   # Notion 已改名；正式站可能仍是 Manage 3PL Orders
    6074: "Manage Exception Orders",
    6086: "Manage Stock Level",
    6104: "Inventory Logs",
    6118: "Reports Center",
    6132: "Performance Metrics",
    6483: "Roles & Permission",
    6616: "Walmart Marketplace Integration Guide",
    6627: "Amazon Seller Central (Appstore) Integration Guide",
    6651: "Etsy Integration Guide",
    6691: "Wayfair & CastleGate Integration Guide",
    6746: "Temu Integration Guide",
    6788: "Walmart Supplier One Integration Guide",
    7068: "Shipment Routing",
    7150: "Remittance Reconciliation",
    7251: "New Order Frozen Period",
    7761: "TikTok Shop Integration Guide",   # 正式站人工發佈後回填
    7802: "BigCommerce Integration Guide",   # 同上
}

REQUIRED_ROUTES = [
    "/synctify/v1/elementor/(?P<id>\\d+)",
    "/synctify/v1/elementor/(?P<id>\\d+)/draft",
    "/synctify/v1/media/sideload",
    "/synctify/v1/doc/defaults/(?P<id>\\d+)",
    "/synctify/v1/faq/sync",
    "/synctify/v1/seo/(?P<id>\\d+)",
    "/synctify/v1/settings",
    "/synctify/v1/tp/lookup",
    "/synctify/v1/tp/update",
]


class Checker:
    def __init__(self, base, auth):
        # auth 是**完整的** Authorization 標頭值（含 "Basic " 前綴），
        # 由 wp_env.WPTarget.basic_auth_header() 產生。不要在這裡再補前綴——
        # 補兩次會變成 "Basic Basic …"，站台一律回 401，看起來就像帳密錯。
        self.base, self.auth = base.rstrip("/"), auth
        self.results = []

    def get(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": self.auth,
                   "User-Agent": "synctify-verify/1.0"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body = r.read().decode(errors="replace")
                # 空白內容刻意**不**當成 {}：WAF 的 challenge 就是回 202＋空內容，
                # 當成合法 JSON 會把標頭裡的原因吃掉，只剩沒用的「HTTP 202」。
                if not body.strip():
                    return r.status, {"_notjson": "", "_hdr": dict(r.headers)}
                try:
                    return r.status, json.loads(body)
                except json.JSONDecodeError:
                    return r.status, {"_notjson": body[:200],
                                      "_hdr": dict(r.headers)}
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"_notjson": raw[:200], "_hdr": dict(e.headers)}
        except Exception as e:                       # DNS、連線、逾時
            return 0, {"_error": f"{type(e).__name__}: {e}"}

    def check(self, name, ok, detail=""):
        self.results.append((bool(ok), name, detail))
        print(f"  {'✅' if ok else '❌'} {name}" + (f"\n       {detail}" if detail else ""))
        return bool(ok)


def diagnose(code, payload):
    """把「回了東西但不是 JSON」翻譯成看得懂的原因。"""
    hdr = {k.lower(): v for k, v in (payload.get("_hdr") or {}).items()}
    waf = hdr.get("x-amzn-waf-action")
    if waf:
        return (f"AWS WAF 的 {waf} 動作（HTTP {code}）。"
                f"它期待瀏覽器執行 JS 驗證，API client 一律通不過。\n"
                f"       需要請維運把你的來源 IP 加進 WAF 白名單——"
                f"n8n 能寫入正式站就是因為它的 IP 已在白名單裡。")
    if payload.get("_error"):
        return payload["_error"]
    # REST API 安全外掛（miniOrange 等）會用自己的格式擋下來，訊息與 WP 的不同
    if isinstance(payload, dict) and payload.get("error") in ("Restricted", "UNAUTHORIZED"):
        why = payload.get("error_reason") or payload.get("error_description") or ""
        return (f"被 REST API 安全外掛攔下（HTTP {code}）：{why[:180]}\n"
                f"       實測：/synctify/v1/* 可通過，被擋的是內建的 /wp/v2/*。"
                f"這會讓同步流程無法建立草稿與查詢文章。")
    if payload.get("_notjson") is not None:
        body = payload["_notjson"].strip()
        return (f"HTTP {code}，回的不是 JSON"
                + (f"：{body[:120]}" if body else "（空白內容）")
                + f"\n       server={hdr.get('server', '?')}")
    return f"HTTP {code}"


def strip_html(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def main():
    ap = argparse.ArgumentParser(description="搬站前的前置檢查（唯讀）")
    wp_env.add_target_arg(ap)
    ap.add_argument("--base", help="覆寫網址（帳密仍取自 --target 對應的那組）")
    args = ap.parse_args()

    try:
        wp = wp_env.resolve(args.target, base_override=args.base)
    except wp_env.MissingCredentials as e:
        print(f"✗ {e}")
        return 1
    sfx = wp_env.TARGETS[wp.target]["suffix"]
    c = Checker(wp.base, wp.basic_auth_header())

    print(f"\n檢查目標：{wp.base}（{wp.label}，--target {wp.target}）"
          f"\n" + "=" * 62)

    # ── 1. 連得到、認證過得了 ──
    print("\n【1】連線與認證")
    code, root = c.get("/wp-json/")
    ok = code == 200 and "_notjson" not in root and "_error" not in root
    if not c.check("REST API 可連線", ok, "" if ok else diagnose(code, root)):
        print("\n連不上就不必往下了——後面每一項都會跟著失敗，訊息只會更混亂。")
        return 1
    code, me = c.get("/wp-json/wp/v2/users/me?context=edit&_fields=id,name,capabilities")
    authed = c.check("Application Password 有效", code == 200,
            f"登入為 {me.get('name')!r}" if code == 200 else
            (f".env 的 WP_USERNAME{sfx} / WP_APP_PASSWORD{sfx} 對{wp.label}無效"
             "——兩站的 Application Password 是各自獨立的"
             if (me or {}).get("code") == "rest_not_logged_in" else str(me)[:120]))
    if not authed:
        # 認證不過就別往下了——後面每一項都會變成「找不到」，
        # 25 行誤導訊息比一行真正的原因難懂得多。
        print("\n認證沒過，後續檢查都會失真，先停在這裡。")
        return 1
    caps = (me.get("capabilities") or {}) if code == 200 else {}
    c.check("具備 edit_posts 權限", bool(caps.get("edit_posts")))
    c.check("具備 manage_options 權限（/settings 端點需要）",
            bool(caps.get("manage_options")),
            "沒有的話只影響發佈回呼的設定端點，其餘功能不受影響")

    # ── 2. 輔助外掛 ──
    print("\n【2】輔助外掛")
    code, plugins = c.get("/wp-json/wp/v2/plugins")
    ver = None
    if code == 200 and isinstance(plugins, list):
        for p in plugins:
            if "synctify-sync-helper" in p.get("plugin", ""):
                ver, status = p.get("version"), p.get("status")
                c.check(f"外掛已安裝並啟用（{ver}）", status == "active")
    c.check("外掛存在", ver is not None,
            "沒讀到版本；可能是權限不足或外掛未安裝" if ver is None else "")
    code, ns = c.get("/wp-json/synctify/v1")
    routes = set((ns or {}).get("routes", {}))
    for r in REQUIRED_ROUTES:
        c.check(f"路由 {r}", r in routes)

    # ── 3. 站方預設欄位的三個名稱解析 ──
    print("\n【3】站方預設欄位（全部以名稱解析，不依賴 ID）")
    code, att = c.get(f"/wp-json/wp/v2/media?slug={FEATURED_SLUG}&per_page=5"
                      "&_fields=id,slug,media_details")
    hit = [a for a in att if a.get("slug") == FEATURED_SLUG] if code == 200 and isinstance(att, list) else []
    d = (hit[0].get("media_details") or {}) if hit else {}
    c.check(f"封面照 slug={FEATURED_SLUG}", bool(hit),
            f"id={hit[0]['id']}，{d.get('width')}x{d.get('height')}" if hit else "找不到")

    code, users = c.get("/wp-json/wp/v2/users?per_page=100&_fields=id,name")
    match = [u for u in users if u.get("name") == AUTHOR_NAME] if code == 200 and isinstance(users, list) else []
    c.check(f"作者顯示名稱 {AUTHOR_NAME!r}", bool(match),
            f"id={match[0]['id']}" if match else "找不到，/doc/defaults 會回 422")

    code, roots = c.get("/wp-json/wp/v2/docs?parent=0&per_page=100"
                        "&status=publish,draft,private&_fields=id,title")
    root_hit = [r for r in roots if strip_html((r.get("title") or {}).get("rendered")) == DOC_ROOT] \
        if code == 200 and isinstance(roots, list) else []
    c.check(f"文件根節點 {DOC_ROOT!r}", bool(root_hit),
            f"id={root_hit[0]['id']}" if root_hit else "找不到，所有文章都會找不到 Parent")

    if root_hit:
        rid = root_hit[0]["id"]
        code, kids = c.get(f"/wp-json/wp/v2/docs?parent={rid}&per_page=100"
                           "&status=publish,draft,private&_fields=id,title")
        have = {strip_html((k.get("title") or {}).get("rendered")) for k in kids} \
            if code == 200 and isinstance(kids, list) else set()
        missing = [x for x in CATEGORIES if x not in have]
        c.check(f"10 個分類頁齊全（{len(CATEGORIES) - len(missing)}/{len(CATEGORIES)}）",
                not missing, ("缺：" + "、".join(missing)) if missing else "")
        if KNOWN_MISSING_CATEGORY not in have:
            print(f"       ℹ️ {KNOWN_MISSING_CATEGORY} 分類頁不存在（已知缺口）——"
                  f"有文章用 9. Automation 時才需要補")

    # ── 4. Arconix FAQ ──
    print("\n【4】Arconix FAQ")
    code, types = c.get("/wp-json/wp/v2/types/faq")
    c.check("faq post type 已開放 REST", code == 200 and types.get("rest_base") == "faq")
    code, tax = c.get("/wp-json/wp/v2/taxonomies/group")
    c.check("group taxonomy 已開放 REST（rest_base=faq-group）",
            code == 200 and tax.get("rest_base") == "faq-group")

    # ── 5. Notion 的 WP Post ID 在這個站台指向正確的文章 ──
    print("\n【5】Notion 記錄的 WP Post ID 是否對得上")
    absent, renamed = [], []
    for pid, expect in sorted(EXPECTED_POSTS.items()):
        code, d = c.get(f"/wp-json/wp/v2/docs/{pid}?context=edit&_fields=id,title")
        if code != 200:
            absent.append(f"{pid}（Notion 說是「{expect}」）")
            continue
        actual = strip_html((d.get("title") or {}).get("raw")
                            or (d.get("title") or {}).get("rendered"))
        if actual != expect:
            renamed.append(f"{pid}：Notion「{expect}」／站上「{actual}」")

    # 文章不存在 ＝ ID 無效，同步一定出錯 → 硬性失敗
    c.check(f"{len(EXPECTED_POSTS)} 個 Post ID 在此站都存在", not absent,
            "\n       ".join(absent))
    # 標題不同未必是錯（可能是還沒同步過去的改名）→ 只回報，腳本沒資格判定
    if renamed:
        print(f"  ⚠️ 有 {len(renamed)} 篇標題與 Notion 不同。請確認是預期中的改名，"
              "而不是 ID 指向了別篇文章：")
        for r in renamed:
            print(f"       {r}")
    else:
        print("  ℹ️ 所有標題都與 Notion 一致")

    # ── 6. 相依外掛（透過端點行為判斷，不寫入）──
    print("\n【6】相依外掛")
    probe = next(iter(EXPECTED_POSTS))
    code, seo = c.get(f"/wp-json/synctify/v1/seo/{probe}", "POST", {})
    c.check("All in One SEO 已啟用", not (code == 501 and seo.get("code") == "no_aioseo"),
            "端點回 no_aioseo" if seo.get("code") == "no_aioseo" else "")
    code, plugins = c.get("/wp-json/wp/v2/plugins")
    names = " ".join(p.get("plugin", "") for p in plugins) if isinstance(plugins, list) else ""
    c.check("Elementor 已安裝", "elementor" in names.lower(),
            "讀不到外掛清單時此項僅供參考" if not names else "")

    # ── 8. 發佈回呼設定 ──
    print("\n【7】發佈回呼設定（可稍後再設，不影響同步）")
    code, st = c.get("/wp-json/synctify/v1/settings")
    if code == 200:
        print(f"  ℹ️ callback_enabled = {st.get('callback_enabled')}"
              f"（來源：{st.get('publish_webhook_url_source')}）")
        if not st.get("callback_enabled"):
            print("       尚未設定——同步仍可運作，只是發佈後 Notion 不會自動變「已發佈」")
    else:
        print(f"  ℹ️ 讀不到設定（HTTP {code}）；需要 manage_options 權限")

    # ── 總結 ──
    failed = [n for ok, n, _ in c.results if not ok]
    print("\n" + "=" * 62)
    print(f"通過 {len(c.results) - len(failed)}/{len(c.results)}")
    if failed:
        print("\n未通過：\n  - " + "\n  - ".join(failed))
        print("\n這些都會讓第一次同步失敗，請先補齊再搬。")
        return 1
    print("\n✅ 這個站台具備搬遷條件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
