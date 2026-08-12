# scripts

## verify_endpoints.py

對測試站的六個 `/synctify/v1/` 端點各實打一次驗證（含認證負向測試與自動清理）。
用一篇專用測試草稿當標的，不碰既有內容；`tp/update` 用 bogus id 冒煙，不動真實譯文。

**前置：**

1. `.env` 填好 `WP_BASE_URL` / `WP_USERNAME` / `WP_APP_PASSWORD`
2. 啟動轉換 service：

   ```bash
   ./.venv/bin/python -m uvicorn service.app:app --port 8800
   ```

**執行：**

```bash
./.venv/bin/python scripts/verify_endpoints.py
```

轉換 service 若不在預設位址，用 `CONVERTER_URL` 覆蓋。

> ⚠️ 測試站目前被 SSO/OAuth 閘門擋在最前面，所有請求會 302 轉去 Google 登入而到不了
> WordPress。腳本會在前置探測時偵測到並提早中止。需先在閘門把 `/wp-json/` 設為例外
> （改由 Application Password 認證）或提供可通過 proxy 的憑證，才能完成驗證。


## verify_site_ready.py —— 搬站前的前置檢查（唯讀）

把上稿流程搬到另一個站台前先跑一次，確認那個站台具備所有先決條件。
**全部是 GET 與唯讀的 POST，可以安全地對正式站執行。**

```bash
./.venv/bin/python scripts/verify_site_ready.py --base https://support.synctify.net
```

帳密取自 `.env` 的 `WP_USERNAME` / `WP_APP_PASSWORD`——**正式站要用正式站自己的
Application Password**，測試站那組在正式站無效。

檢查八組：連線與認證、輔助外掛與 9 條路由、站方預設欄位的三個名稱解析
（封面照／作者／分類頁）、Arconix FAQ、**Notion 記錄的 WP Post ID 是否指向
正確的文章**、只存在於測試站的文章、相依外掛、發佈回呼設定。

第五項最關鍵，也刻意分成兩種嚴重度：

- **文章不存在** → 硬性失敗。ID 無效，同步一定出錯。
- **標題與 Notion 不同** → 只警告。可能是還沒同步過去的改名（例如 `5-1` 在 Notion
  已改成 Manage Sales Orders），也可能是 ID 指向了別篇文章——**腳本沒資格判定**，
  兩個值都印出來交由人確認。

對照表取自 Notion（Fay 已逐列與正式站對齊），不是測試站的快照。文章改名或新增後
要更新 `EXPECTED_POSTS`。

認證沒過會直接停在第一組——否則後面每一項都變成「找不到」，25 行誤導訊息比
一行真正的原因難懂得多。
