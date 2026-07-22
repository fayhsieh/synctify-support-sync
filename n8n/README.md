# n8n workflow

存放 n8n workflow 的匯出 JSON。

**匯出前務必確認憑證欄位是引用（credential id）而非明文**，n8n 匯出時可能夾帶敏感資訊。

規劃中的 workflow：

- `sync-to-wp.json` — 主流程：Notion 按鈕 webhook → 讀取內容 → 轉換 → WP 寫入 → 狀態回寫
- `confirm-publish.json` — 影子草稿覆蓋正式文章
- `translate.json` — 簡中翻譯流程（含術語確認暫停點）
- `error-handler.json` — 全域錯誤處理：Notion 狀態標記＋email 通知
