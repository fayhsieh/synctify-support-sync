# Synctify Support Center 格式映射規則 v1.1（2026-07-17）

Notion 教學文件 → WordPress Elementor（Docly + EazyDocs）自動上稿轉換規則。
已透過 4 篇正式站文章實測驗證（Amazon SC、Walmart Supplier One、BigCommerce、Add & Edit Categories）。

## 一、內容區塊映射

| # | Notion 寫法 | WP / Elementor 輸出 | 備註 |
| --- | --- | --- | --- |
| 1 | `##` 標題 | heading widget（h2） | 每個 h2 起新 container；EazyDocs TOC 抓 h2+h3，總數 ≤ 12 |
| 2 | `###` 標題 | heading widget（h3） | |
| 3 | `####` 標題 | heading widget（h4） | 不進 TOC |
| 4 | 粗體獨立行 `**Step X-X. ...**` | heading widget（h4） | 舊文件相容規則（Notion 尚無 H4 時期的寫法） |
| 5 | 一般段落 | text-editor widget（`<p>`） | 輸出乾淨 HTML，不帶任何貼上殘留 |
| 6 | 項目符號清單（含巢狀） | text-editor widget（`<ul><li>`） | |
| 7 | 數字清單 `1. 2. 3.` | docly_list_item widget（order_list 圓形數字樣式） | 全站統一此樣式 |
| 8 | 行內程式碼 `` `UI 路徑` `` | `[direction]...[/direction]` shortcode | 可點擊 UI 路徑；路徑用 `>` 分隔放同一組 |
| 9 | 粗體 `**文字**` | `<strong>` | 不可點擊的 UI 文字、狀態名稱 |
| 10 | 連結 `[文字](url)` | `<a href="..." target="_blank" rel="noopener">` | 連結文字一律去除粗體 |
| 11 | 程式碼區塊（fenced，含語言標記） | docly_code_syntax_highlighter widget | 語言標記 → `lng_type`，內容 → `source_code` |
| 12 | 表格 | text-editor widget 內 HTML `<table>` | |
| 13 | 圖片＋圖說 | image widget | 圖說 → alt text＋caption；詳見「三、圖片規則」 |

## 二、Callout 映射（五種，依 Notion icon＋底色判別）

| Notion callout | 類型 | WP 輸出（docly_alerts_box） |
| --- | --- | --- |
| 灰底＋💡 燈泡 | Message | display_type=note，無 alert_type |
| 藍底＋ℹ️ 圓形 i | Info | alert_type=info |
| 綠底＋✅ 打勾 | Success | alert_type=success |
| 黃底＋⚠️ 三角驚嘆號 | Warning | alert_type=warning |
| 紅底＋⚠️ 三角驚嘆號 | Danger | alert_type=danger |

Callout 首行若為粗體獨立行 → alert_title，其餘內容 → alert_description。

## 三、圖片規則

| 情境 | 處理 |
| --- | --- |
| 圖片已在 WP 媒體庫（assets.synctify.net） | 去除檔名尺寸後綴（-1024x469）還原原始檔，反查 media ID 引用，不重複上傳 |
| 圖片在 Notion（S3 暫存網址） | 下載 → 上傳 WP 媒體庫；檔名由圖說生成（`文章主題-動作描述.png` 慣例）；圖說寫入 alt text＋caption |

## 四、Icon button 對照表（Notion emoji → custom_icon shortcode）

**Notion 端寫作慣例（已定案）**：icon 用 inline code 包住，格式為 `` `emoji (Label)` ``，例如 `` `⏬ (Expand)` ``、`` `🎛️ (Adjust)` ``。

**轉換規則**：inline code 內容以 icon emoji 開頭時，「不」套用 `[direction]`，改輸出 shortcode＋標籤純文字。
例：`` `⏬ (Expand)` `` → `[custom_icon class="chevrons-down"] (Expand)`

| Notion emoji | 名稱 | WP 輸出 |
| --- | --- | --- |
| ✏️ | 鉛筆（Edit） | `[custom_icon class="pencil"]` |
| ⚙️ | 齒輪（Settings） | `[custom_icon class="settings"]` |
| ⬇️ | 向下箭頭 | `[custom_icon class="chevron-down"]` |
| ⏬ | 向下雙箭頭 | `[custom_icon class="chevrons-down"]` |
| 🎛️ | 調整庫存 | `[custom_icon class="adjustments-alt"]` |

統一輸出 `[custom_icon]` shortcode（由站上自訂 PHP 渲染為 `<kbd><i class="ti ti-{class}"></i></kbd>`，含按鍵樣式外框）。舊文章中直接寫原生 `<i class="ti ti-...">` 的寫法缺少 `<kbd>` 外框，屬歷史不一致，不再使用。裸 emoji（未包 inline code）為舊文件相容寫法，轉換器同樣支援。

## 五、FAQ / Troubleshooting 段

| Notion 寫法 | WP 輸出 |
| --- | --- |
| `## FAQs` 或 `## Troubleshooting` ＋ `### 問題` ＋ 答案段落 | 問答寫入 Arconix FAQ（group＝文章 slug）；頁面上只插入 `[faq group="文章slug" groupby="date" style="accordion"]` shortcode |

## 六、不同步（自動剔除）的內容

| 內容 | 處理 |
| --- | --- |
| `*Last updated: ...*` 開頭行 | 剔除，由同步日期自動重新生成 |
| `**SEO Meta**` 段（Title／Meta description） | 不進頁面；寫入 All in One SEO 欄位（`_aioseo_title`／`_aioseo_description`） |
| Version History 段（`### vN - 日期`） | 不同步，僅留 Notion 內部追蹤 |
| 內部審核筆記（帶 toggle 的 callout、標題含「Content Review Notes」） | 不同步，自動剔除 |
| Notion 留言標記（discussion span）、notionvc 註解 | 剔除 |

## 七、寫作端注意事項（給 Support Center Writer Skill）

- 可點擊 UI 路徑一律用 inline code；不可點擊 UI 文字用粗體（Style Guide §5）
- H2＋H3 總數控制在 12 以內，超過時往 H4 降級（Style Guide §4）
- Icon button 一律使用上表五種 emoji，並用 inline code 包住、附上英文標籤：`` `⏬ (Expand)` ``；不要用其他相近 emoji
- 程式碼區塊務必標語言（http／markdown／json 等），會直接變成 WP 端語法高亮的語言設定
- 內部筆記請放在帶 toggle 的 callout 內，或標題含「Content Review Notes」，確保不會被同步
- FAQ 問題用 `###`，一題一個標題，答案直接接在下方
