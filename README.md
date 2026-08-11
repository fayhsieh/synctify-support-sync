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
| `POST /synctify/v1/elementor/{id}/draft` | 只寫入 Elementor autosave 版本，主文章與前台完全不動（已發佈文章的更新走這條） |
| `DELETE /synctify/v1/elementor/{id}/draft` | 刪掉上面那筆 autosave。WP core 不允許從 REST 刪 autosave，Elementor UI 的 Discard 也只清得掉「當前登入者自己寫的」那筆，所以需要這支 |
| `POST /synctify/v1/media/sideload` | 把 Notion 的 S3 暫存圖匯入媒體庫，並寫入 title / alt / caption |
| `POST /synctify/v1/tp/lookup` | 查詢 TranslatePress 字典表（取得未翻譯字串） |
| `POST /synctify/v1/tp/update` | 寫入譯文（status=2 人工翻譯永不覆蓋） |
| `POST /synctify/v1/doc/defaults/{id}` | 套用站方統一欄位：封面照 `opengraph`、作者 The Synctify Team、討論 closed、Parent 依 Notion Category 對到分類頁。全部依名稱在站上解析，不寫死 ID。另存 `notion_page_id` 供發佈回呼對應 |
| `GET` / `POST /synctify/v1/settings` | 讀寫發佈回呼的網址與密鑰（存資料庫，不必碰 `wp-config.php`）。權限要求 `manage_options`；GET 不回傳密鑰本身，只回報有無與長度 |
| `POST /synctify/v1/seo/{id}` | 寫入 AIOSEO meta title / description。回傳 `previous` 供還原；現值是 AIOSEO 智慧標籤模板、**或為空（＝沿用全站範本）**的欄位預設跳過不寫，回應的 `preserved` 標明原因（預設只保護 `title`，見 `preserve_smart_tags`） |

另外開啟 Arconix FAQ post type 的 REST 存取。

**發佈回呼**（0.2.0+）：WP 按下發佈（或對已發佈文章套用 Elementor 草稿）時，外掛打一個
n8n webhook，由 n8n 把 Notion 母列標成「已發佈」。外掛不持有 Notion token。
設定方式二選一：`wp-config.php` 定義 `SYNCTIFY_PUBLISH_WEBHOOK_URL` / `_HEADER` / `_SECRET`，
或用 `POST /synctify/v1/settings` 寫進資料庫（不必有主機檔案存取權）。**常數優先**。
兩者都沒設時靜默停用。

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

**英文上稿**：Notion 按鈕（或勾選「待同步」等輪詢）→ n8n → 讀取 Notion 內容 → 轉換 Elementor JSON → 圖片上傳媒體庫 → 依母列的 WP Post ID 判斷新建草稿／寫入既有文章的 Elementor 草稿 → 套用站方預設欄位 → 寫入 SEO meta → 回寫 Notion 狀態 → 人工確認 → 發佈

兩個觸發器共用**同一條處理鏈**（`n8n/notion-poll-to-wp-draft.workflow.json`，30 節點）：

| 觸發方式 | 用途 |
| --- | --- |
| Notion 按鈕 → webhook | 單篇即時同步 |
| 勾選「待同步」→ 定時輪詢 | 批次；也是按鈕失效時的後備 |

> 刻意不分成兩份 workflow——先前分家的按鈕版落後了 14 個節點，兩份各自演化只會讓修正漏掉其中一邊。

**Notion 按鈕設定**：Content Hub 的「同步到 WP」按鈕 → Send webhook → 網址填 n8n 的 Production URL。
按鈕請用在**版本子列**（母列沒有內容區塊）。webhook 的 path 就是這條端點的唯一憑證，
匯入後手動改成隨機字串，**不要寫回 repo**。

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

- [x] **驗證各端點**（2026-07-29，測試站閘門放行後實打）：`scripts/verify_endpoints.py` **13/13 通過** —— elementor 寫入／備份／還原、SEO meta、tp lookup／update、Arconix FAQ REST、認證負向測試、測試草稿自動清理

待辦：
- [ ] 建置 n8n workflow（webhook → 轉換 → WP 寫入 → 狀態回寫）
  - [x] Workflow 1 sync-to-wp 骨架（`n8n/sync-to-wp.workflow.json`；Notion＋轉換器接真，WP 寫入 mock）
  - [ ] Workflow 1：閘門放行後把 WP mock 換真＋圖片上傳迴圈＋Notion→markdown callout 對映
  - [ ] Workflow 2 confirm-publish、Workflow 3 translate、Error Workflow
- [ ] 圖片上傳邏輯（Notion S3 → WP 媒體庫，含 alt/caption）
- [ ] TranslatePress 字串切分顆粒度驗證
- [ ] Support Center Writer prompt 移植＋翻譯品質對照測試
- [x] Notion Content Hub 上稿按鈕（2026-08-11 公司升級 Plus 方案後解鎖；webhook 觸發已併入 `notion-poll-to-wp-draft.workflow.json`）
- [ ] 翻譯按鈕（等 Workflow 3）
- [ ] 上線前內容對帳（正式站較新的改動補回 Notion）
