# n8n workflow

**匯出前務必確認憑證欄位是引用（credential id）而非明文**，n8n 匯出時可能夾帶敏感資訊。
webhook 的 path 同樣不可寫回這裡——它就是那條端點的識別碼（CLAUDE.md）。

## 檔案

| 檔案 | 內容 |
| --- | --- |
| `notion-sync-to-wp.workflow.json` | 主流程：Notion 按鈕 → 轉換 → WP 草稿 → 回寫 Notion（33 節點） |
| `wp-publish-callback.workflow.json` | WP 按發佈 → 標記已發佈、Status 轉 Existing、維護版本標記（15 節點） |
| `diagnose-prod-access.workflow.json` | 診斷：從 n8n 打正式站四個路由，判斷是誰擋的（6 節點，**唯讀**）。手寫的，不由 `build_n8n_code_node.py` 產生 |
| `code-node.py` | 上面兩條 workflow 共用的 Code node 程式，**兩邊都要貼同一份** |
| `n8n-workflow-blueprint.md` | Workflow 2／3 的節點藍圖（尚未實作） |
| `translation-node-migration.md` | Workflow 3 翻譯 prompt 的移植方案 |

三組憑證的 **ID 寫在 `scripts/build_n8n_code_node.py`**（`WP_CRED_ID`、
`NOTION_CRED_ID`、`WEBHOOK_AUTH_CRED_ID`），所以匯入後不必再逐一雙擊節點補憑證。
**那些只是 n8n 內部識別碼，不是帳密**——帳密與密鑰留在 n8n 的憑證管理裡。
換 n8n 環境或重建憑證時要一併更新這三個常數，否則匯入後會出現紅色三角形。

## 診斷：正式站打不進去的時候

`diagnose-prod-access.workflow.json`：Import from File → Execute Workflow，不必
Publish。全部是 GET、唯讀，不會在站上留下任何東西。

**要從 n8n 跑，不是從自己的電腦跑。** 攔截可能發生在網路層（WAF 按來源 IP 判斷），
本機通得過不代表 n8n 通得過，反過來也一樣——2026-08-13 就是為了回答維運「到底是
哪個 workflow、有沒有具體紀錄」才做的。

`整理結果` 節點會依回應特徵自動判定是誰擋的：

| 特徵 | 判定 |
| --- | --- |
| 回應標頭有 `x-amzn-waf-action`（HTTP 202＋HTML/空白內容） | AWS WAF |
| JSON 的鍵是 `status` / `error` / `error_description` | REST 安全外掛（miniOrange） |
| JSON 的鍵是 `code` / `message` / `data` | WordPress 原生 |

HTTP 節點都開了 `neverError` 與 `responseFormat: text`，所以 401/403 不會讓節點
中斷、WAF 回的 HTML 也不會因為解析不了 JSON 而讓原因變成 `undefined`。

另有 `n8n 的對外 IP` 節點——維運要比對白名單時，這個比任何猜測都準。

## 目標站台

`scripts/build_n8n_code_node.py` 的 `TARGET` 決定產出要打哪一台，也可以用
`--target` 覆寫不必改檔案：

```bash
python scripts/build_n8n_code_node.py                    # 正式站（預設）
python scripts/build_n8n_code_node.py --target test      # 測試站
```

| target | 站台 | 產出檔名 | webhook path 取自 |
| --- | --- | --- | --- |
| `prod` | support.synctify.net | `notion-sync-to-wp.workflow.json` | `.env` 的 `N8N_WEBHOOK_PATH` |
| `test` | support.synctify.io | `notion-sync-to-wp.test.workflow.json` | `.env` 的 `N8N_WEBHOOK_PATH_TEST` |

一個開關切換四件事：WP 網址、WP 憑證、webhook path、回寫哪個 Notion 屬性。
workflow 名稱也會標明站台，避免在 n8n 裡認錯。

**站台相依的「內容」不在這裡**——封面照、作者、分類頁、FAQ 群組一律由端點在站上
依名稱解析，所以搬站不必改任何 ID（見 `docs/mapping-rules.md` §六之二）。

> 兩站要同時運作時，把 `TARGETS["test"]["post_id_prop"]` 改成獨立欄位
> （例如 `WP Post ID (Test)`）並在 Notion 加上該欄位，否則兩邊的回寫會互相覆蓋。

## 匯入時不必手填 Path

進版控的 JSON 裡 webhook 的 `path` 是**佔位字串**——真實 path 屬於端點識別碼，
不入庫（CLAUDE.md）。要產一份可直接匯入的：

```bash
python scripts/build_n8n_code_node.py --local
```

會從 `.env` 的 `N8N_WEBHOOK_PATH` / `N8N_PUBLISH_WEBHOOK_PATH` 讀值，輸出到
`n8n/local/`（已 gitignore）。匯入那份就**完全不用手填**——憑證與 path 都齊了。
`.env` 沒填時會提示產生指令並以非 0 結束。

> 真正的門是 Header Auth，不是 path——實測用正確 path 但不帶 header 會回 403。
> path 仍不入庫是為了縱深防禦，不是因為它單獨能通過認證。

**三個 JSON 都由 `scripts/build_n8n_code_node.py` 產生，不要手改**——手改會在下次
建置時被覆蓋，且映射邏輯的單一真實來源在 `converter/`。

開發過程的舊產物（`sync-to-wp`、`notion-to-wp-draft`、`notion-to-elementor-test`、
`notion-poll-to-wp-draft`）已於 2026-08-11 移除，建置時也會主動刪除重新出現的同名檔。
