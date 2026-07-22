# Synctify Support Center 自動上稿流程

Notion 教學文件 → n8n → WordPress（Docly + EazyDocs + Elementor）自動同步，含簡體中文翻譯流程。

## 這個專案解決什麼問題

Support Center 文件維持在 Notion 單一來源管理，按一下 Notion 按鈕就自動轉換格式、同步到 WordPress 草稿，人工只負責確認發佈與譯文校閱。取代原本逐篇手動在 Elementor 上稿的流程。

## 目錄結構

```
converter/    Notion → Elementor JSON 轉換器（Python，映射邏輯核心）
wp-plugin/    WordPress 輔助外掛（自訂 REST 端點）
n8n/          n8n workflow JSON（待建立）
docs/         格式映射規則
samples/      測試用的 Notion 內容樣本
```

## 元件說明

### converter/notion2elementor.py

把 Notion 頁面內容（Notion-flavored Markdown）轉成可匯入的 Elementor template JSON。同時輸出 FAQ 項目清單與轉換報告（待上傳圖片、SEO meta）。

映射規則完整定義見 `docs/mapping-rules.md`，已透過 4 篇正式站文章實測驗證。

```bash
python3 converter/notion2elementor.py <notion.md> "<文章標題>" <faq-group-slug> <輸出目錄>
```

輸出三個檔案：

| 檔案 | 內容 |
| --- | --- |
| `elementor-template-output.json` | 可在 Elementor → Templates → Import 匯入 |
| `faq-items.json` | 待寫入 Arconix FAQ 的問答 |
| `conversion-report.json` | 統計、圖片清單（含待上傳標記） |

### wp-plugin/synctify-sync-helper.php

WordPress 端的自訂 REST 端點，補足標準 REST API 做不到的部分。所有端點皆需 Application Password 認證＋`edit_posts` 權限。

| 端點 | 用途 |
| --- | --- |
| `POST /synctify/v1/elementor/{id}` | 寫入 `_elementor_data`（覆蓋前自動備份最近 3 版） |
| `POST /synctify/v1/elementor/{id}/restore` | 還原備份 |
| `POST /synctify/v1/tp/lookup` | 查詢 TranslatePress 字典表（取得未翻譯字串） |
| `POST /synctify/v1/tp/update` | 寫入譯文（status=2 人工翻譯永不覆蓋） |
| `POST /synctify/v1/seo/{id}` | 寫入 AIOSEO meta title / description |

另外開啟 Arconix FAQ post type 的 REST 存取。

**安裝**：打包成 zip 從後台上傳，或整個檔案放進 `wp-content/mu-plugins/`。

## 環境

| | |
| --- | --- |
| 測試站 | support.synctify.io（WordPress 7.0） |
| 正式站 | support.synctify.net |
| 文章 post type | `docs`（REST base：`/wp-json/wp/v2/docs`，已開放） |
| FAQ post type | `faq`（REST base：`/wp-json/wp/v2/faq`）；group taxonomy `group`（REST base：`/wp-json/wp/v2/faq-group`）。兩者皆由輔助外掛的 filter 開啟 REST |
| 相依外掛 | Elementor、Docly 主題、EazyDocs、Arconix FAQ、TranslatePress、All in One SEO |

開發與驗證一律先走測試站。

## 設定

複製 `.env.example` 為 `.env` 並填入實際值。**`.env` 已列入 .gitignore，絕對不要提交任何憑證。**

n8n 的憑證應存在 n8n credential 管理中，不要寫進 workflow JSON。匯出 workflow 時確認憑證欄位為引用而非明文。

## 流程總覽

**英文上稿**：Notion 按鈕 → n8n webhook → 讀取 Notion 內容 → 轉換 Elementor JSON → 依 WP Post ID 判斷新建草稿／更新草稿／建立影子草稿 → 人工確認 → 發佈

**簡中翻譯**：發佈後觸發 TP 字串登錄 → 撈未翻譯字串（＝差異清單）→ 抽新術語 → 有新詞則暫停等 Notion 確認 → 套術語表 LLM 翻譯 → 寫回 TP 字典表 → Notion 標記待校閱

完整設計與決策背景見 Notion 主文件（Marketing Wiki）。

## 現況與待辦

已完成：

- [x] 格式映射規則 v1.1（4 篇實測驗證）
- [x] 轉換器原型（測試站匯入驗證通過）
- [x] WP 輔助外掛
- [x] 確認 docs post type 已開放 REST
- [x] 轉換器 HTTP microservice（`service/`，FastAPI，本機測試通過）
- [x] 部署輔助外掛到測試站（`/wp-json/synctify/v1/` 六個端點路由註冊成功，已確認路由列表）
- [x] 確認 Arconix FAQ 結構（post type `faq`、group taxonomy `group`，與外掛假設一致，無需修改；REST 已由外掛 filter 開啟）
- [x] 建立 Application Password（已填入 `.env`）
- [x] 驗證腳本就緒（`scripts/verify_endpoints.py`：converter service → 六端點各實打一次含認證與清理）

待辦：

- [ ] **驗證各端點（阻塞中）**：測試站被 SSO/OAuth 閘門擋在最前面，`/wp-json/` 全被 302 轉去 Google 登入，流量到不了 WP。需先在閘門把 `/wp-json/` 設為例外或提供可通過 proxy 的憑證；放行後跑 `scripts/verify_endpoints.py` 即可打勾（同一閘門也會擋 n8n）
- [ ] 建置 n8n workflow（webhook → 轉換 → WP 寫入 → 狀態回寫）
- [ ] 圖片上傳邏輯（Notion S3 → WP 媒體庫，含 alt/caption）
- [ ] TranslatePress 字串切分顆粒度驗證
- [ ] Support Center Writer prompt 移植＋翻譯品質對照測試
- [ ] Notion Content Hub 兩顆按鈕（欄位已加：WP Post ID、上稿狀態、翻譯狀態、最後同步時間；兩顆按鈕待 n8n webhook 網址）
- [ ] 上線前內容對帳（正式站較新的改動補回 Notion）
