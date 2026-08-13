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
| `GET /synctify/v1/tp/info` | TranslatePress 環境探查：版本、語言設定、實際存在的 `trp_*` 表與各表筆數。字典表沒有「字串屬於哪篇文章」，能不能做「只翻剛發佈那篇」取決於站上有沒有 `trp_original_strings` / `trp_original_meta` |
| `GET /synctify/v1/tp/strings` | **列舉** TranslatePress 字典表，可依 `post_id`（經 `trp_original_meta` 的 `post_parent_id` 關聯到文章）、`block_type`（0＝TP 自動登錄的片段／1＝人工在編輯器建的整句，含 HTML）、`status`、`search` 篩選，分頁。`/tp/lookup` 要呼叫端先知道字串長什麼樣，但 TP 儲存的 `original` 帶行內標記、猜不出來（實測 12 句只命中 3 句），所以撈「這次要翻什麼」得用這支 |
| `POST /synctify/v1/tp/lookup` | 查詢指定字串的翻譯狀態（給定字串清單 → 回 id/譯文/status） |
| `POST /synctify/v1/tp/update` | 寫入譯文（status=2 人工翻譯永不覆蓋） |
| `POST /synctify/v1/doc/defaults/{id}` | 套用站方統一欄位：封面照 `opengraph`、作者 The Synctify Team、討論 closed、Parent 依 Notion Category 對到分類頁。全部依名稱在站上解析，不寫死 ID。另存 `notion_page_id` 供發佈回呼對應 |
| `POST /synctify/v1/faq/sync` | 把 FAQ 題目同步進 Arconix FAQ（依 group 分類詞）。以標題比對，人工建立的既有題目會被認領而非重複建立；移除只動管過的且僅移到垃圾桶；`items` 為空時刻意不清除 |
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

**觸發方式：Notion 按鈕 → webhook**（`n8n/notion-sync-to-wp.workflow.json`，33 節點）。

勾選「待同步」的定時輪詢已於 2026-08-11 移除——按鈕實測通過後，輪詢只是讓 n8n
大部分時間在空掃沒有更新的 Notion。要救回的話把 `scripts/build_n8n_code_node.py`
的 `POLLING` 改成 `"active"` 重新產生即可，處理鏈完全共用。

**失敗處理**：13 個可能失敗的節點都開了錯誤輸出，統一導向同一條失敗路徑——
回寫 `❌ 同步失敗` 到被按下的那一列、在該 Notion 頁面留言說明卡在哪個節點
（用 `$prevNode.name` 取得），最後以 Stop and Error 讓 n8n 的 Executions 也顯示失敗。

> 沒有這條的話，流程斷在中間時 n8n 仍顯示 Succeeded，只有翻 executions 才發現
> ——2026-08-11 實測踩過。小編不會去看 n8n，訊號必須出現在 Notion。

**發佈回呼**：`n8n/wp-publish-callback.workflow.json`（15 節點）。WP 按下發佈 →
外掛打 webhook → 標記已發佈、Status 轉 Existing、維護版本標記、對齊母列的
Version 與 Last edited date。

**Notion 按鈕設定**：Content Hub 的「同步到 WP」按鈕 → Send webhook → 網址填 n8n 的 Production URL，
並用 Add custom header 帶上與 n8n Header Auth 憑證相同的 header。
按鈕請用在**版本子列**（母列沒有內容區塊）。

webhook 的 path 不入庫，進版控的 JSON 是佔位字串。跑
`python scripts/build_n8n_code_node.py --local` 會從 `.env` 取真實 path，
產一份可直接匯入的到 `n8n/local/`（已 gitignore），憑證與 path 都不必手填。

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
  - [x] Workflow 1（`n8n/notion-sync-to-wp.workflow.json`）：按鈕觸發、圖片上傳、站方欄位、SEO meta、狀態回寫，端到端實測通過
  - [x] 失敗處理（併入主 workflow，非獨立 Error Workflow：需要文章的 Notion page id，而 n8n 的 Error Trigger 拿不到）
  - [ ] Workflow 2 confirm-publish、Workflow 3 translate
- [ ] 圖片上傳邏輯（Notion S3 → WP 媒體庫，含 alt/caption）
- [x] **TranslatePress 字串切分顆粒度驗證**（2026-08-13，測試站實測）：TP **不存 HTML
  標籤**，而是**以行內元素的邊界切分**——粗體、inline code、連結、我們的 shortcode
  都是切點，兩個行內元素之間的一段純文字就是字典的一列。所以
  `Click **Submit** to update the stock level.` 在字典裡只有 `to update the stock level.`。
  兩個後果：(a) 呼叫端無法重現字串形態，撈清單必須用 `GET /tp/strings` 問 TP；
  (b) **翻譯單位是殘句而非完整句**，節點 9 的 prompt 必須補語境，否則產出會很生硬
  （人工譯者處理 id=930 時把半句改寫成完整句並補了冒號）
- [ ] Support Center Writer prompt 移植＋翻譯品質對照測試
- [x] Notion Content Hub 上稿按鈕（2026-08-11 公司升級 Plus 方案後解鎖，端到端實測通過）
- [x] 發佈回呼：WP 按發佈 → Notion 標記已發佈＋版本標記自動維護（2026-08-11 實測通過）
- [ ] 翻譯按鈕（等 Workflow 3）
- [ ] 上線前內容對帳（正式站較新的改動補回 Notion）
