#!/usr/bin/env python3
"""
上稿流程搬站前的前置檢查（**唯讀**，不會寫入任何資料）。

用途：把流程搬到另一個站台之前，先確認那個站台具備所有先決條件。少一項就會在
第一次同步時失敗——例如分類頁不存在會讓 /doc/defaults 回 422 直接卡住。

    ./.venv/bin/python scripts/verify_site_ready.py                 # 用 .env 的 WP_BASE_URL
    ./.venv/bin/python scripts/verify_site_ready.py --base https://support.synctify.net

帳密取自 .env 的 WP_USERNAME / WP_APP_PASSWORD（正式站要用正式站自己的
Application Password，測試站那組在正式站無效）。

⚠️ 全部是 GET 與唯讀的 POST（帶空 body 的端點只回報差異、不寫入），
可以安全地對正式站執行。
"""
import argparse
import base64
import html
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

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

# Notion 母列記著的 WP Post ID → 該篇標題（2026-08-12 由測試站取得）。
# 測試站是正式站的資料庫複製，故這些 ID 兩站應當一致；對不上就代表
# Notion 的 WP Post ID 在目標站台指向了別篇文章，同步會覆蓋錯的文章。
SHARED_POSTS = {
    5601: "Configure Warehouse", 5620: "Manage User Access", 6483: "Roles & Permission",
    5635: "Add & Edit Categories", 5868: "Add/Edit/Delete Variant Attributes",
    5883: "Add/Edit/Archive Product", 5907: "Configure SKU Aliases",
    5921: "Connect Integrations",
    6627: "Amazon Seller Central (Appstore) Integration Guide",
    6616: "Walmart Marketplace Integration Guide",
    6788: "Walmart Supplier One Integration Guide",
    6691: "Wayfair & CastleGate Integration Guide",
    6651: "Etsy Integration Guide", 6746: "Temu Integration Guide",
    6041: "Manage 3PL Orders", 6074: "Manage Exception Orders",
    7068: "Shipment Routing", 7251: "New Order Frozen Period",
    6086: "Manage Stock Level", 6104: "Inventory Logs", 6118: "Reports Center",
    6132: "Performance Metrics", 7150: "Remittance Reconciliation",
}

# 只存在於測試站的文章——搬站前要把 Notion 母列的 WP Post ID 清空，
# 讓正式站第一次同步時重新建立並回填。
TEST_ONLY_POSTS = {7570: "BigCommerce Integration Guide",
                   7561: "TikTok Shop Integration Guide"}

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


def read_env():
    out = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


class Checker:
    def __init__(self, base, auth):
        self.base, self.auth = base.rstrip("/"), auth
        self.results = []

    def get(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Basic {self.auth}",
                   "User-Agent": "synctify-verify/1.0"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw or "{}")
            except json.JSONDecodeError:
                return e.code, {"_raw": raw[:200]}
        except Exception as e:                       # 連不到、憑證錯、非 JSON
            return 0, {"_error": f"{type(e).__name__}: {e}"}

    def check(self, name, ok, detail=""):
        self.results.append((bool(ok), name, detail))
        print(f"  {'✅' if ok else '❌'} {name}" + (f"\n       {detail}" if detail else ""))
        return bool(ok)


def strip_html(t):
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def main():
    ap = argparse.ArgumentParser(description="搬站前的前置檢查（唯讀）")
    ap.add_argument("--base", help="目標站台網址，預設取 .env 的 WP_BASE_URL")
    args = ap.parse_args()

    env = read_env()
    base = (args.base or env.get("WP_BASE_URL", "")).rstrip("/")
    user, pw = env.get("WP_USERNAME", ""), env.get("WP_APP_PASSWORD", "").replace(" ", "")
    if not (base and user and pw):
        print("✗ .env 需要 WP_BASE_URL / WP_USERNAME / WP_APP_PASSWORD")
        return 1
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    c = Checker(base, auth)

    print(f"\n檢查目標：{base}\n" + "=" * 62)

    # ── 1. 連得到、認證過得了 ──
    print("\n【1】連線與認證")
    code, root = c.get("/wp-json/")
    if not c.check("REST API 可連線", code == 200,
                   root.get("_error") or root.get("_raw", "")[:120]):
        print("\n連不上就不必往下了。若是 WAF 擋住，需要把你的來源 IP 加進白名單。")
        return 1
    code, me = c.get("/wp-json/wp/v2/users/me?context=edit&_fields=id,name,capabilities")
    c.check("Application Password 有效", code == 200,
            f"登入為 {me.get('name')!r}" if code == 200 else str(me)[:120])
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
    wrong, absent = [], []
    for pid, expect in SHARED_POSTS.items():
        code, d = c.get(f"/wp-json/wp/v2/docs/{pid}?context=edit&_fields=id,title")
        if code != 200:
            absent.append(f"{pid}（{expect}）")
            continue
        actual = strip_html((d.get("title") or {}).get("raw") or (d.get("title") or {}).get("rendered"))
        if actual != expect:
            wrong.append(f"{pid}：預期 {expect!r}，實際 {actual!r}")
    c.check(f"{len(SHARED_POSTS)} 篇既有文章的 ID 對得上", not wrong and not absent,
            "\n       ".join(wrong + [f"找不到：{a}" for a in absent]))

    print("\n【6】只存在於測試站的文章")
    for pid, name in TEST_ONLY_POSTS.items():
        code, d = c.get(f"/wp-json/wp/v2/docs/{pid}?context=edit&_fields=id,title")
        actual = strip_html((d.get("title") or {}).get("raw", "")) if code == 200 else None
        if code != 200:
            print(f"  ℹ️ {pid} 在此站不存在（預期如此）"
                  f"——搬站前請把 Notion 母列「{name}」的 WP Post ID 清空")
        elif actual != name:
            c.check(f"{pid} 指向別篇文章", False,
                    f"此站的 {pid} 是 {actual!r}，不是 {name!r}——"
                    f"Notion 若留著這個 ID 會覆蓋到錯的文章")
        else:
            print(f"  ℹ️ {pid} 在此站也叫 {name!r}（同一站或已同步過）")

    # ── 7. 相依外掛（透過端點行為判斷，不寫入）──
    print("\n【7】相依外掛")
    probe = next(iter(SHARED_POSTS))
    code, seo = c.get(f"/wp-json/synctify/v1/seo/{probe}", "POST", {})
    c.check("All in One SEO 已啟用", not (code == 501 and seo.get("code") == "no_aioseo"),
            "端點回 no_aioseo" if seo.get("code") == "no_aioseo" else "")
    code, plugins = c.get("/wp-json/wp/v2/plugins")
    names = " ".join(p.get("plugin", "") for p in plugins) if isinstance(plugins, list) else ""
    c.check("Elementor 已安裝", "elementor" in names.lower(),
            "讀不到外掛清單時此項僅供參考" if not names else "")

    # ── 8. 發佈回呼設定 ──
    print("\n【8】發佈回呼設定（可稍後再設，不影響同步）")
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
