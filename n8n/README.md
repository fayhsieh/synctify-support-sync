# n8n workflow

**匯出前務必確認憑證欄位是引用（credential id）而非明文**，n8n 匯出時可能夾帶敏感資訊。
webhook 的 path 同樣不可寫回這裡——它就是那條端點的識別碼（CLAUDE.md）。

## 檔案

| 檔案 | 內容 |
| --- | --- |
| `notion-sync-to-wp.workflow.json` | 主流程：Notion 按鈕 → 轉換 → WP 草稿 → 回寫 Notion（33 節點） |
| `wp-publish-callback.workflow.json` | WP 按發佈 → 標記已發佈、Status 轉 Existing、維護版本標記（15 節點） |
| `code-node.py` | 上面兩條 workflow 共用的 Code node 程式，**兩邊都要貼同一份** |
| `n8n-workflow-blueprint.md` | Workflow 2／3 的節點藍圖（尚未實作） |
| `translation-node-migration.md` | Workflow 3 翻譯 prompt 的移植方案 |

三組憑證的 **ID 寫在 `scripts/build_n8n_code_node.py`**（`WP_CRED_ID`、
`NOTION_CRED_ID`、`WEBHOOK_AUTH_CRED_ID`），所以匯入後不必再逐一雙擊節點補憑證。
**那些只是 n8n 內部識別碼，不是帳密**——帳密與密鑰留在 n8n 的憑證管理裡。
換 n8n 環境或重建憑證時要一併更新這三個常數，否則匯入後會出現紅色三角形。

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
