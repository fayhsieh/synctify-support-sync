# 轉換 HTTP microservice

把 `converter/notion2elementor.py` 的 `convert()` 包成 HTTP 服務，給 n8n 的 HTTP Request 節點呼叫。
**映射邏輯的單一真實來源永遠是 `converter/notion2elementor.py`**——本服務只做 HTTP 轉接，不重寫、不改任何轉換規則。

## 安裝與啟動

```bash
# 本機開發（repo 根目錄）
python3.12 -m venv .venv
./.venv/bin/pip install -r service/requirements.txt

# 啟動（開發，附 auto-reload）
./.venv/bin/python -m uvicorn service.app:app --host 127.0.0.1 --port 8800 --reload
```

> 注意：本機的系統 `python3` 是壞掉的 Xcode 存根，請用 homebrew 的 `python3.12`。

## 端點

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET | `/health` | 健康檢查（n8n 探活用） |
| POST | `/convert` | 收 Notion markdown＋標題＋faq group，回傳轉換後 JSON |
| GET | `/openapi.json` | OpenAPI schema（n8n 可 import 產生節點） |

### POST /convert

Request：

```json
{
  "markdown": "## Overview\n...",
  "title": "文章標題",
  "faq_group": "文章-slug",
  "sync_date": "July 15, 2026"
}
```

`sync_date` 選填，不給則用今天。

Response：

```json
{
  "template": { "content": [...], "page_settings": [], "version": "0.4", "title": "...", "type": "page" },
  "faq_items": [ { "question": "...", "answer_html": "..." } ],
  "report": { "widgets": 55, "containers": 6, "faq_items": 3, "images": [...], "images_pending_upload": 6 }
}
```

- `template` → 可直接匯入 Elementor，或經 WP 輔助外掛 `POST /synctify/v1/elementor/{id}` 寫入 `_elementor_data`
- `faq_items` → 寫入 Arconix FAQ
- `report.images` 標 `pending_upload` 者為 Notion S3 暫存圖，需先上傳 WP 媒體庫

## 認證（選用）

設環境變數 `CONVERTER_API_KEY` 後，`/convert` 需帶 header：

```
Authorization: Bearer <key>
```

未設時不驗證（本機開發）。正式部署給 n8n 呼叫時建議設定，n8n 端把 key 存在 credential。

## 測試

```bash
./.venv/bin/python -m pytest service/test_app.py -v
```

走完整 HTTP 層，用 `samples/amazon-sc-v2-notion.md` 驗證回傳結構與關鍵映射。
