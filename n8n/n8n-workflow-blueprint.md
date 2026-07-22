# n8n Workflow 節點藍圖

Synctify Support Center 自動上稿的三條 workflow 節點結構。給 Claude Code 搭骨架時當依據；閘門未放行前，凡是呼叫 WP 的 HTTP 節點先用 mock 回應頂著。

共用前提：
- 轉換器已包成 HTTP service（`POST /convert`，收 Notion markdown＋標題＋faq group slug，回 Elementor JSON＋faq items＋圖片清單）
- WP 認證用 Application Password（存 n8n credential）
- Webhook 都要驗 header token（防止知道網址的人亂觸發）

## Notion 結構與版本語意（重要）

Content Hub 是自我參照結構：**母列＝一篇 guide 的穩定身分**（body 放 Overview＋Changelog），**子列＝各版本**（v1、v2…，body 放實際要上稿的內文）。

- **按鈕長在「版本子列」上**：你在想上稿的那個版本按「同步到 WP」，n8n 讀那一版的內文。由你明確指定推哪版。
- **母列的 `Version` 欄位（select）＝目前 WP 線上版本**（結構化訊號，可靠）。例：母列 Version=v2 代表 WP 上跑的是 v2。判斷「線上是哪版」一律讀這個欄位，不要去解析標題字串。
- **「(Current)」標題後綴＝同一件事的人看顯示**，與母列 Version 欄位對應。可能出現 v2 已寫好但未上稿、母列 Version 仍是 v1 的情況。所以 n8n 讀的是「你按的那一版」，不是線上那一版。
- 每個版本子列自己也有 `Version` 欄位（v1/v2/v3），n8n 讀按下的子列 Version 即知這次要推哪版。
- **WP Post ID、上稿狀態、翻譯狀態、最後同步時間都記在「母列」**（一篇 WP 文章對應一個母列，穩定不變）。n8n 從按下的子列往上找 `Parent item` 取得母列。
- **確認發佈後 n8n 同時更新兩處**：母列 `Version` 欄位改成剛發佈的版本；標題的「(Current)」從舊版搬到新版。找舊版子列用母列的舊 Version 值比對（比解析標題準）。

---

## Workflow 1：sync-to-wp（主流程，「同步到 WP」按鈕觸發）

| # | 節點 | 類型 | 說明 |
| --- | --- | --- | --- |
| 1 | **Webhook** | Trigger | 接**版本子列**按鈕的 POST，payload 帶該子列的 Notion page ID |
| 2 | **驗證 token** | IF | 檢查 header token，錯就中止回 401 |
| 3 | **Notion: 取得子列＋母列** | Notion / HTTP | 讀按下的子列 → 循 `Parent item` 取得母列的 WP Post ID、上稿狀態、Doc name |
| 4 | **Notion: 取得子列內容** | Notion / HTTP | 抓**該版本子列**的 body（轉成 markdown）＝要上稿的內文 |
| 5 | **組裝參數** | Set | markdown、標題（用母列的 guide 名，去掉版本後綴）、faq group slug（由文章 slug 推導）|
| 6 | **呼叫轉換器** | HTTP Request | `POST 轉換器/convert` → 拿 Elementor JSON＋faq items＋圖片清單 |
| 7 | **圖片處理（迴圈）** | Loop + HTTP | 對圖片清單中 `pending_upload=true` 的：下載 Notion S3 圖 → 上傳 WP `/wp/v2/media`（帶 alt/caption）→ 取 media ID → 回填 Elementor JSON 的圖片網址 |
| 8 | **判斷路徑** | Switch | 依 WP Post ID＋上稿狀態分三路（見下）|
| 9a | **新文章** | HTTP | `POST /wp/v2/docs`（status=draft）建草稿 → 取得新 Post ID |
| 9b | **更新草稿** | HTTP | `POST /wp/v2/docs/{id}` 更新既有草稿 |
| 9c | **影子草稿** | HTTP | `POST /wp/v2/docs` 建「[更新預覽] …」草稿，不動正式文章；記錄影子草稿 ID |
| 10 | **寫入 Elementor** | HTTP | `POST /synctify/v1/elementor/{目標id}` 寫版面資料 |
| 11 | **寫入 SEO** | HTTP | `POST /synctify/v1/seo/{目標id}` 寫 AIOSEO meta |
| 12 | **寫入 FAQ** | HTTP（有 faq items 才跑）| `POST /wp/v2/faq` 建問答、掛 group taxonomy |
| 13 | **Notion: 回寫母列狀態** | Notion / HTTP | 寫回**母列**：WP Post ID、上稿狀態（新文章/更新草稿→「草稿已建立」；影子草稿→「待確認發佈」）、最後同步時間 |
| 14 | **回應 Webhook** | Respond | 回成功給 Notion 按鈕 |

分路邏輯（節點 8）：
- 母列無 WP Post ID → 9a 新文章
- 母列有 Post ID＋WP 上是草稿 → 9b 更新草稿
- 母列有 Post ID＋WP 上已發佈 → 9c 影子草稿

---

## Workflow 2：confirm-publish（「確認發佈」按鈕觸發，只給已發佈文章的更新用）

| # | 節點 | 類型 | 說明 |
| --- | --- | --- | --- |
| 1 | **Webhook** | Trigger | 接「確認發佈」按鈕（按在剛推上去的版本子列），帶 Notion page ID |
| 2 | **驗證 token** | IF | 同上 |
| 3 | **Notion: 取得子列＋母列** | Notion | 循 Parent item 取母列的正式文章 Post ID＋影子草稿 ID |
| 4 | **讀影子草稿版面** | HTTP | 取影子草稿的 `_elementor_data` |
| 5 | **覆蓋正式文章** | HTTP | `POST /synctify/v1/elementor/{正式id}` 寫入（端點會自動備份原版）|
| 6 | **刪除影子草稿** | HTTP | 清掉「[更新預覽]」那篇 |
| 7 | **搬 (Current) 標記＋更新 Version** | Notion | 用母列舊 Version 值找到舊線上版子列 → 移除其標題「(Current)」；把「(Current)」加到剛發佈的版本子列標題；母列 `Version` 欄位更新為剛發佈的版本 |
| 8 | **Notion: 更新母列狀態** | Notion | 上稿狀態→「已發佈」、最後同步時間 |
| 9 | **（可選）觸發翻譯** | Execute Workflow | 呼叫 Workflow 3 |

---

## Workflow 3：translate（簡中翻譯，發佈後觸發）

| # | 節點 | 類型 | 說明 |
| --- | --- | --- | --- |
| 1 | **Trigger** | — | 由 Workflow 1/2 發佈後呼叫，或手動 |
| 2 | **登錄 TP 字串** | HTTP | GET 一次該頁簡中網址，讓 TranslatePress 登錄新字串（TP 自動翻譯保持關閉）|
| 3 | **撈未翻譯字串** | HTTP | `POST /synctify/v1/tp/lookup` → 取 status 為未翻譯的字串（＝差異清單）|
| 4 | **抽專有名詞** | OpenAI | 從未翻譯字串抽出專有名詞 |
| 5 | **比對術語表** | Notion | 查詞彙對照表，分出「已有定案」與「新詞」|
| 6 | **有新詞？** | IF | 有 → 進 7；無 → 跳 9 |
| 7 | **暫停等確認** | Wait | 把新詞清單發到 Notion，等人工確認後恢復 |
| 8 | **新詞寫回術語表** | Notion | 確認過的譯法寫進詞彙對照表 |
| 9 | **正式翻譯** | OpenAI | 套 Writer prompt＋術語表翻譯未翻譯字串 |
| 10 | **寫回 TP 字典** | HTTP | `POST /synctify/v1/tp/update`（status=機翻；人工譯文端點自動保護不覆蓋）|
| 11 | **Notion: 標記待校閱** | Notion | 機翻段落清單寫進留言、翻譯狀態→「待校閱」|

---

## 全域：Error Workflow

任何 workflow 節點失敗時觸發：
1. 該篇 Notion 列上稿／翻譯狀態 → 「❌ 同步失敗」，留言附錯誤摘要＋n8n 執行紀錄連結
2. email 通知 Fay（主）
3. 修復後重按按鈕重跑即可（Post ID 防重複機制保證不會產生重複文章）

---

## 建置順序建議（閘門未放行期間）

1. 先搭 Workflow 1 骨架，WP 相關 HTTP 節點用 mock 回應（假 Post ID、假成功）
2. 轉換器 HTTP 節點可以接真的（轉換器不需要 WP）
3. Notion 讀寫節點可以接真的（Notion 不受閘門影響）
4. 閘門放行後，把 mock 換成真 WP URL，跑第一篇端到端測試（建議用 Amazon SC，post 6627，走影子草稿路徑）
