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
