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
| 7 | 數字清單 `1. 2. 3.` | docly_list_item widget（order_list 圓形數字樣式） | 全站統一此樣式。整段連續編號＝**同一個** docly_list_item（編號連續靠同一 widget，`steps` 留空）。編號項下 tab 縮排的巢狀子內容（bullet／接續說明）內嵌成 `<p style="padding-left: 40px;">`，**不可**用 `<ul><li>`（主題 CSS counter 會把 `<li>` 算進圓圈編號）。結構逆向自實站範本 7899。⚠️ **巢狀在步驟底下的 callout／程式碼區塊／表格／引用／分隔線目前仍會切斷編號**——它們在中介 markdown 會插入空行且不帶縮排，跳出清單層級。段落與圖片已正確處理。要讓編號不斷，這些區塊必須折進步驟內文，那是尚未決定的映射（2026-08-11 暫緩）；實務上最常見的觸發原因是圖片佔位鷹架，那個已改為整段剔除 |
| 8 | 行內程式碼 `` `UI 路徑` `` | `[direction]...[/direction]` shortcode | 可點擊 UI 路徑；路徑用 `>` 分隔放同一組。分隔符 `>` 輸出為 `&gt;`（否則 Docly shortcode 會渲染成箭頭圖示，站上要字面 `>`）|
| 9 | 粗體 `**文字**` | `<strong>` | 不可點擊的 UI 文字、狀態名稱 |
| 10 | 連結 `[文字](url)` | `<a href="..." target="_blank" rel="noopener">` | 連結文字一律去除粗體。**指向 Content Hub 的 Notion 連結會換成 WP 永久連結**，見 §六之六 |
| 11 | 程式碼區塊（fenced，含語言標記） | docly_code_syntax_highlighter widget | 語言標記 → `lng_type`，內容 → `source_code`。語言名可能含空格（Notion 的 `plain text`、`shell script`），一律正規化：`plain text` → `markdown`（站上慣例，範本 7978 確認），其餘去空格。⚠️ fence 解析須容許帶空格的語言標記，否則開頭 fence 認不出來、結尾 fence 被當開頭，會把文件剩餘內容整段吞掉 |
| 12 | 表格 | text-editor widget 內 HTML `<table>` | |
| 13 | 圖片＋圖說 | image widget | 圖說 → alt text＋caption；詳見「三、圖片規則」。**例外**：tab 縮排在數字清單步驟下的巢狀圖片 → 內嵌成該步驟 item 內的 `[caption]` shortcode（`<a href>` 保 lightbox、`[caption]` 保圖說、不佔圓圈編號），非獨立 widget。統一規範：**Link To = Media File**（`<a href>` 指原圖）、**Size = Large 1024×576**（`img` 帶 `size-large` class＋`width/height`）。結構逆向自實站範本 7915 |

## 二、Callout 映射（五種，依 Notion icon＋底色判別）

| Notion callout | 類型 | WP 輸出（docly_alerts_box） |
| --- | --- | --- |
| 灰底＋💡 燈泡 | Message | display_type=note，無 alert_type |
| 藍底＋ℹ️ 圓形 i | Info | alert_type=info |
| 綠底＋✅ 打勾 | Success | alert_type=success |
| 黃底＋⚠️ 三角驚嘆號 | Warning | alert_type=warning |
| 紅底＋⚠️ 三角驚嘆號 | Danger | alert_type=danger |

Callout 首行若為粗體獨立行 → alert_title，其餘內容 → alert_description。

**判別順序**：Message → Info → Success → Warning/Danger。Warning 與 Danger 同為 ⚠️，
**只能靠底色區分**（黃＝Warning、紅＝Danger），故底色判斷不可省；底色無法判斷時保守歸為 Warning。

**兩種來源格式都支援**（判別結果一致，見 `callout_type()`）：

| 訊號 | Notion API 原生（n8n Blocks→Markdown 輸出） | 舊匯出格式（`samples/` 用） |
| --- | --- | --- |
| icon | emoji：`💡` `ℹ️` `✅` `⚠️` | 路徑字串含 `light-bulb`／`info`／`checkmark`／`warning` |
| 底色 | Notion 名：`gray_background`／`blue_background`／`green_background`／`yellow_background`／`red_background` | `green_bg`／`yellow_bg`／`red_bg` 等 |

> n8n 的 `Blocks → Markdown` 節點直接輸出 Notion 原生 emoji＋`*_background`，轉換器已能直接吃，
> 不需在 n8n 端另做對映。

## 三、圖片規則

| 情境 | 處理 |
| --- | --- |
| 圖片已在 WP 媒體庫（assets.synctify.net） | 去除檔名尺寸後綴（-1024x469）還原原始檔，反查 media ID 引用，不重複上傳 |
| 圖片在 Notion（S3 暫存網址） | 由 `POST /synctify/v1/media/sideload` 下載並匯入媒體庫；Notion 圖說寫入 alt text＋caption |

**Notion S3 網址會過期**：帶預簽章且 `X-Amz-Expires=3600`（一小時）。必須在寫入 WP
版面「之前」完成上傳與網址替換，否則文章會在一小時內變破圖。上傳失敗時退回佔位圖，
絕不可把來源網址寫進 WP。

**⚠️ Notion API 不提供 alt text**（2026-08-02 以實際 API 回應確認）。image block 的
`image` 物件只有 `caption`／`type`／`file`，沒有任何 alt 欄位——即使在 Notion UI 裡
設定了 alt text 也讀不到。

**因此改以圖說內的標記承載兩段文字**（唯一 API 看得到的通道）：

```
可見圖說文字 [alt: 無障礙描述文字]
```

轉換器拆成 caption（可見圖說）與 alt（alt text），分別寫入 WP 的 Caption 與 Alt text。
**無標記時兩者同值**，舊文章行為不變（向下相容）。
中介 markdown 用 title 欄位攜帶 alt：`![可見圖說](url "alt text")`。

**WP 媒體庫三個文字欄位的儲存位置不同**（很容易寫錯）：

| 媒體庫欄位 | 實際位置 |
| --- | --- |
| Title | `post_title` |
| Alt text | post meta `_wp_attachment_image_alt` |
| Caption | `post_excerpt`（**不是** `post_content`，後者是 Description）|

## 四、Icon button 對照表（Notion emoji → custom_icon shortcode）

**Notion 端寫作慣例（已定案）**：icon 用 inline code 包住，格式為 `` `emoji (Label)` ``，例如 `` `⏬ (Expand)` ``、`` `🎛️ (Adjust)` ``。

⚠️ **不一定是 emoji**：`⋮` 是 U+22EE VERTICAL ELLIPSIS，屬一般標點字元。判斷只看
是否以對照表中的字元開頭，沒有「必須是 emoji」的限制，所以這類字元照樣適用。

**轉換規則**：inline code 內容以 icon emoji 開頭時，「不」套用 `[direction]`，改輸出 shortcode＋標籤純文字。
例：`` `⏬ (Expand)` `` → `[custom_icon class="chevrons-down"] (Expand)`

| Notion emoji | 名稱 | WP 輸出 |
| --- | --- | --- |
| ✏️ | 鉛筆（Edit） | `[custom_icon class="pencil"]` |
| ⚙️ | 齒輪（Settings） | `[custom_icon class="settings"]` |
| ⬇️ | 向下箭頭 | `[custom_icon class="chevron-down"]` |
| ⏬ | 向下雙箭頭 | `[custom_icon class="chevrons-down"]` |
| 🎛️ | 調整庫存 | `[custom_icon class="adjustments-alt"]` |
| ⋮ | More Actions（直式三點選單） | `[custom_icon class="dots-vertical"]` |

統一輸出 `[custom_icon]` shortcode（由站上自訂 PHP 渲染為 `<kbd><i class="ti ti-{class}"></i></kbd>`，含按鍵樣式外框）。舊文章中直接寫原生 `<i class="ti ti-...">` 的寫法缺少 `<kbd>` 外框，屬歷史不一致，不再使用。裸 emoji（未包 inline code）為舊文件相容寫法，轉換器同樣支援。

## 五、FAQ / Troubleshooting 段

| Notion 寫法 | WP 輸出 |
| --- | --- |
| `## FAQs` 或 `## Troubleshooting` ＋ `### 問題` ＋ 答案段落 | 問答寫入 Arconix FAQ（group＝文章 slug）；頁面上只插入 `[faq group="文章slug" groupby="date" style="accordion"]` shortcode |

**每個符合條件的 h2 各自成為一組 accordion。** 先前所有段落共用同一個 group，
一篇文章同時有 FAQ 與 Troubleshooting 時（如 4-10 BigCommerce），兩段會插入
**完全相同**的 shortcode，前台各自展開全部題目——Troubleshooting 底下會出現 FAQ 的
題目（2026-08-11 實測確認）。

| 情況 | 群組名 |
| --- | --- |
| 只有一段 | `{文章 slug}`（與站上既有兩組相同，不受影響） |
| 兩段以上 | `{slug}-faq`、`{slug}-troubleshooting`（依標題，不依順序） |

**逐篇控制**（Fay 2026-08-11 決定）：h2 標題結尾加標記覆寫預設行為，標記不會進站上的標題。

| 標記 | 效果 |
| --- | --- |
| `## Troubleshooting (Plain)` | 這段當一般內文，不折疊 |
| `## Common Issues (Accordion)` | 這段折成 accordion，即使標題不是 FAQ／Troubleshooting |
| 無標記 | 維持既有行為：FAQ／FAQs／Troubleshooting → accordion，其餘 → 一般內文 |

全形括號 `（）` 同樣接受。**既有文章一個標記都不用加**。

**打錯字會被回報而非靜默忽略**：標記寫成 `(Accordian)`、`(Plian)` 這類近似字時，
`report["unrecognized_section_markers"]` 會列出來。判斷用編輯距離 ≤ 2 而非前綴比對
——`Plian` 是字母換位，前綴抓不到。`(Beta)`、`(Optional)`、`(v2)`、`(Plus)` 這些
正常括號不會誤判。

> **為什麼用標題標記而不是 Notion 的 toggle 區塊**：toggle 在這個專案已經是
> 「內部審核筆記、絕不同步」的訊號（CLAUDE.md）。若改用 toggle 表示 accordion，
> 同一種區塊就有兩個相反語意，靠上下文區分——寫作者把備註寫成 toggle 就會直接
> publish 到公開站上。那個失敗比「該折疊的沒折疊」嚴重得多。

問題標題 **`###` 與 `####` 都接受**——Style Guide 寫 `###`，實際文章多用 `####`。
只認一種的話另一種會既不進 `faq_items` 也不進頁面，**整段靜默消失**。

由 `POST /synctify/v1/faq/sync` 寫入。從站上實況反推的三件事（2026-08-11 確認）：

| | |
| --- | --- |
| 分類法的 REST 欄位名 | **`faq-group`**（rest_base），不是 `group` |
| 排序依據 | **`post_date`**（`menu_order` 全為 0，shortcode 用 `groupby="date"`）。每題相隔 60 秒，基準沿用群組內最早的既有日期，避免每次同步整組日期漂移 |
| 既有 FAQ | 人工建立、**沒有任何標記**，內容還帶著 Notion 貼上殘留 |

因此比對用**同群組內的標題**而非自訂 meta——否則人工那批會被當成不存在，同步後
整組重複。比對到就認領（補上 `_synctify_faq_managed` 並更新內容），回應以
`adopted` 區分。標題比對會忽略大小寫、多餘空白與結尾問號。

**三道保護**：刪除只動管過的且僅移到垃圾桶；對不上的人工題目不動、以 `orphans`
回報；**`items` 為空時一律跳過清除**——「FAQ 段落沒被解析出來」與「真的要刪光」
在端點看來一模一樣，往安全方向倒。

⚠️ **FAQ 在 workflow 裡走並行支線，不可串在主線上**（2026-08-11 實測踩到）。
`splitOut` 在沒有 FAQ 段落時輸出 0 筆，串成一直線的話**後面整條都不會執行**——
連 Notion 的狀態回寫都做不到，而且 n8n 仍顯示 Succeeded。沒有 FAQ 的文章佔多數，
所以這個錯法影響的是大部分文章，不是少數例外。

另外 `splitOut` 只能拆「輸入項目自己的欄位」。它的上游是 SEO 端點的回應，裡面沒有
`faq_sections`——要先用一個 Set 節點顯式從轉換節點取過來。

⚠️ **FAQ 是獨立文章，無法放進 Elementor 草稿暫存**，所以對已發佈文章會立刻反映到
前台（Fay 2026-08-11 決定，與 SEO meta 一致）。內文仍走 autosave 受保護，因此
更新既有文章時 FAQ 會比內文早一步上線。

## 六、不同步（自動剔除）的內容

| 內容 | 處理 |
| --- | --- |
| `*Last updated: ...*` 開頭行 | **沿用 Notion 上標記的日期**（那是寫作者標記的「內容實質更新日」，不是同步時間，不可被同步當天覆蓋）；Notion 未標記時才退回同步日期 |
| `**SEO Meta**` 段（Title／Meta description） | 不進頁面；**擷取**進 `report["seo"]`，由 `POST /synctify/v1/seo/{id}` 寫入 All in One SEO。段內是 quote block，寫法為「粗體標籤＋軟換行＋內容」（同一個 rich_text 陣列，純文字為 `Title\n實際標題`）；標籤認 Title／SEO Title／Meta description／Description，大小寫與結尾冒號皆容忍 |
| Version History 段（`### vN - 日期`） | 不同步，僅留 Notion 內部追蹤 |
| 內部審核筆記（帶 toggle 的 callout、標題含「Content Review Notes」） | 不同步，自動剔除 |
| **圖片佔位鷹架**：首行為 `Image Placeholder` 的 callout（內含檔名與 caption/alt） | 不同步，整個剔除。標記寫在 callout 自身或第一個子區塊都認。計入 `report["excluded_placeholders"]` |
| Notion 留言標記（discussion span）、notionvc 註解 | 剔除 |

## 六之二、Notion 沒有、但 WP 每篇必填的欄位

由 `POST /synctify/v1/doc/defaults/{id}` 統一套用。值是從測試站 23 篇既有文章反推，
非設計而來（23/23 一致）。**一律以名稱解析、不寫死 ID**——`opengraph.png` 在測試站是
attachment 5988，正式站不保證同號，分類頁 ID 同理。

| 欄位 | 值 | 解析方式 |
| --- | --- | --- |
| 封面照 | `opengraph.png`（1200×630） | 媒體庫 slug `opengraph` |
| 作者 | The Synctify Team | 使用者顯示名稱精確比對 |
| 討論 | `comment_status` / `ping_status` 皆 closed | 固定值 |
| Parent | 依 Notion `Category` 對應的分類頁 | `Synctify Documentation` 底下、標題與 Category 去掉序號前綴後同名者 |

Notion `Category` → WP 分類頁（測試站 ID 僅供對照，程式不使用）：

| Notion Category | WP 分類頁 | 測試站 ID |
| --- | --- | --- |
| 1. Getting Started | Getting Started | 5953 |
| 2. Settings | Settings | 5599 |
| 3. Products | Products | 5633 |
| 4. Integrations | Integrations | 5918 |
| 5. Orders | Orders | 5930 |
| 6. Inventory | Inventory | 5933 |
| 7. Reports | Reports | 5936 |
| 8. Overview | Overview | 6083 |
| 9. Automation | **尚無對應分類頁** | — |
| 10. Finance | Finance | 7148 |
| 0. Troubleshooting | Troubleshooting | 5939 |

分類在站上找不到時端點回 **422 並附可用清單**，刻意不靜默把文章留在根目錄
（那會讓它掉出側邊欄結構）。

**已發佈文章的保護**：`/doc/defaults` 與 `/seo` 對 `post_status=publish` 的文章
**預設只回報差異、不寫入**，需明確傳 `allow_published=true`。AIOSEO meta 沒有草稿
機制，寫下去即線上生效，因此比照「已發佈文章不能直接覆蓋」處理。

> 同步 workflow 的這兩個節點**刻意帶了 `allow_published=true`**（Fay 2026-08-02 決定）：
> 這四項欄位與 SEO 文案都以 Notion 為單一真實來源，既有已發佈文章也直接校正，
> 讓站上狀態不會漂移。內文本身仍受保護——走 Elementor autosave，前台不受影響。
> `/seo` 回應的 `previous` 保留改動前的值，`/doc/defaults` 回應的 `diff` 列出改了什麼。

**AIOSEO 智慧標籤保護**：站上有幾篇的 SEO 是人工用智慧標籤寫的模板，例如
`#post_title: Requests & Labels #separator_sa #site_title`（7068）。Notion 的 SEO Meta
是純文字，直接覆蓋會讓客製部分永久消失，站名日後改動也不會再跟著變。因此
`/seo` 對**現值含智慧標籤**的欄位跳過不寫，並在回應的 `skipped_smart_tags` 列出。

預設保護範圍是 `["title"]`——**標題保留智慧標籤，描述一律以 Notion 為準**
（Fay 2026-08-02 決定）。呼叫端可用 `preserve_smart_tags` 覆寫，傳 `[]` 即全部照寫。
偵測條件是 `#` 後接至少 3 個小寫字母／底線，因此 `#1 Guide` 這類正常標題不會誤判；
真的誤判時方向也是「保留現值」，不會造成覆蓋。

> ⚠️ **每篇的空值不等於「沒有標題」，而是「沿用 AIOSEO 全站範本」**，而那個範本
> 本身就是智慧標籤。第一版只保護「已存值含智慧標籤」的欄位，結果空值欄位被寫成
> 純文字，繼承關係被換成寫死的字串（2026-08-03 Fay 在 demo 上發現，6074／7553 中招）。
> 現在空值與含標籤的值一樣受保護，回應的 `preserved` 會標明原因：
> `inherits_global_template`（空值）或 `has_smart_tags`（已是模板）。

## 六之三、文章標題的推導

站上標題取自 Notion 的 `Doc name`，但要剝掉兩段 Notion 內部管理用的資訊：

| Notion `Doc name` | WP 標題 |
| --- | --- |
| `4-10 BigCommerce Integration Guide - v1 (Current)` | `BigCommerce Integration Guide` |
| `2-2 Manage User Access - v2 (Current)` | `Manage User Access` |
| `Shopify Integration Guide` | `Shopify Integration Guide`（無前綴則不動） |

1. **版本後綴** `- vN`、`(Current)` —— 版本追蹤用，站上只有「目前版本」
2. **編號前綴** `^\d+-\d+` —— Notion 內部排序用。**這段特別容易漏**：它不只出現在
   標題，WP 還會拿標題生 slug，導致網址變成 `/4-10-bigcommerce-integration-guide/`
   （2026-08-03 Fay 在 demo 上發現）

前綴不符合格式的標題完全不動，中文標題與 `How to ...` 這類敘述句都不受影響。

## 六之四、可同步的層級與上稿狀態

Notion 的結構是**三層**，不是兩層：

```
深度1  母列                 無 Parent item；WP Post ID 與上稿狀態記在這
  └ 深度2  版本子列          ← 只有這層可以同步
      └ 深度3  (Draft)      老闆早期沒有 spec 時做的草稿，絕不可同步
```

深度 3 出現在 5-1／5-3／5-4。判斷用**深度**而非命名或 Status——`(Draft)` 後綴與
`Not started` 只是佐證，結構訊號才不依賴命名紀律。

按鈕是資料庫欄位，每一列都有、母列上藏不掉，所以擋在 workflow：

| 情況 | 判斷 | 處理 |
| --- | --- | --- |
| 按到母列 | 自己沒有 Parent item | 中止；回寫 `❌ 同步失敗` 並在該頁留言說明 |
| 按到草稿層 | 母列自己還有 Parent item | 同上 |
| 版本子列 | 其餘 | 正常同步 |

（`待同步` checkbox 與「先取消勾選（認領）」節點隨輪詢一起移除——認領只在輪詢下
有意義：處理時間超過輪詢間隔時，下一輪會重抓同一列。按鈕一次只送一列，沒這問題。）

同一條失敗路徑也接收**節點執行失敗**（不只防呆攔截）：13 個可能失敗的節點都開了
錯誤輸出導向它，用 `$prevNode.name` 指出實際失敗的節點。刻意**不用 n8n 的
Error Workflow 機制**——它的 Error Trigger 只給執行 id 與錯誤訊息，拿不到這篇文章的
Notion page id，無法回寫到正確的列。

路徑末端接 Stop and Error，讓 Executions 也顯示失敗；否則流程斷在中間時 n8n 仍會
顯示 Succeeded（2026-08-11 實測踩過）。

失敗狀態寫在**被按下的那一列**而非母列——使用者在哪裡按就在哪裡看到結果。
留言比 select 值能承載更多資訊，按鈕觸發時沒人盯著 n8n，這點很重要。

**狀態寫在哪一列**：`WP Post ID` **只記在母列**（它是整篇文章的穩定身分，跨版本不變）；
`上稿狀態` 與 `最後同步時間` **母列與子列都寫**。

> 一開始只寫母列，但失敗路徑本來就寫在「被按下的那一列」——成功卻只寫母列的話，
> 使用者在自己按的地方看不到任何回應（2026-08-11 Fay 回報）。兩邊各有用途：
> 母列是整篇文章的彙總與下次同步的依據，子列是「這個版本何時被同步過」的紀錄。
> 發佈回呼同樣兩列都更新，否則母列變「已發佈」時子列還停在「草稿已建立」。

**上稿狀態只有三個會自動出現**（2026-08-11 簡化）：

| 狀態 | 誰寫的 |
| --- | --- |
| `草稿已建立` | 同步成功一律寫這個（不再分「待確認發佈」） |
| `已發佈` | WP 按下發佈 → 外掛回呼 → n8n 寫回 |
| `❌ 同步失敗` | 防呆攔截或流程出錯 |

`已發佈` 的回呼有兩個訊號，因為兩種情境的 WP 端行為不同：

- **新文章**：草稿 → 發佈，有狀態轉換，`transition_post_status` 就夠
- **既有已發佈文章**：套用 Elementor 草稿時文章本來就是 publish、**不會有狀態轉換**，
  改以「主文章 `_elementor_data` 被改動」當訊號。我們自己的同步對已發佈文章只寫
  autosave（revision 的 meta），不會動到主文章這個鍵，所以被觸發就代表是人工套用。
  另有 `$GLOBALS['synctify_internal_write']` 旗標防止同步流程觸發自己。

外掛不直接打 Notion API——那要把 Notion token 存進 WordPress。改打 n8n webhook，
由已持有 Notion 憑證的 n8n 完成寫入。

網址與密鑰有兩種設定來源，**常數優先**：`wp-config.php` 的
`SYNCTIFY_PUBLISH_WEBHOOK_URL` / `_HEADER` / `_SECRET`，或 `POST /synctify/v1/settings`
寫進資料庫。後者是為了不必有主機檔案存取權——`wp-config.php` 少一個分號就會讓整站
白畫面，對非工程角色風險太高。兩者都沒設時整組回呼靜默停用。

## 六之五、版本標記的自動維護

發佈某個版本後，母列與子列有四處要跟著改。原本是人工維護，但老闆與小編常忘記
（Fay 2026-08-11），改由發佈回呼接手：

| # | 位置 | 動作 |
| --- | --- | --- |
| 1 | 版本子列的 `Doc name` | 目標版本加上 ` (Current)`，其餘版本拿掉 |
| 2 | 母列 Overview 的 `- Current Version: vN (Month Year)` | 換成新的版本 |
| 3 | 母列 Version History 的 `### **vN – Month Year**` | 目標版本結尾加 ` (Current)`，其餘拿掉 |
| 4 | 母列的 `Version` 屬性 | 對齊為現行版本 |
| 5 | 子列的 `Status` | 轉為 `Existing`（內容已上線，不再是待上稿） |
| 6 | 母列的 `Last edited date` | 對齊為現行版本的日期——母列的 Content Freshness 公式吃這個欄位，不對齊新鮮度會是錯的 |

格式取自實際母列（5-5 Shipment Routing）。兩個容易弄壞的細節：
**破折號是 en dash `–`**，且 **Version History 的標題整段帶粗體**——改寫時
rich_text 要沿用原本的標註，否則排版跑掉。

**Overview 的日期不自行編造**：從 Version History 中該版本的標題讀出來沿用。
找不到時只寫版本號，不猜月份。

子列沒有 `Last edited date` 時整個欄位不送，避免把母列既有的值清成空白。

**沒有變化就不送 API**：`plan_version_marks()` 只回傳真的需要改動的項目，
避免在 Notion 的編輯紀錄裡刷出一堆無意義的版本。

文字判斷全在 `converter/notion_blocks.py`（有單元測試涵蓋改名、粗體保留、
長版本標籤 `v1 (Initial Version)`、已正確時不動作），n8n 只負責照結果打 API。
子列改名不影響站上文章標題——同步時的 `clean_title` 本來就會把版本後綴與
`(Current)` 剝掉（見 §六之三）。

## 六之六、Notion 內部連結 → WP 永久連結

寫作端（GPT Skill）引用其他文章時會優先貼 Notion 連結。那個網址對讀者是**私有、
打不開的**，直接同步等於在公開站上放死連結（2026-08-11 Fay 回報，6086 文末的
「see Reports Center」）。

解析方式：Content Hub 的母列存著 `WP Post ID`，站上查得到每篇的永久連結，兩邊一併
就能反查。**WP 網址含分類路徑**（`/docs/synctify-documentation/reports/reports-center/`），
拼不出來，只能從站上取。

| 情況 | 處理 |
| --- | --- |
| 連到母列 | 換成該篇的 WP 永久連結 |
| 連到版本子列 | 沿用其母列的網址（`WP Post ID` 只記在母列） |
| 連到尚未同步的頁面 | **保留原連結**，記進 `report["unresolved_notion_links"]` |
| 非 Notion 網址 | 完全不動 |

換不掉時刻意不刪連結——靜默移除會讓寫作者不知道哪裡要修。那是內容問題，
該讓它可見。

網址形式容忍帶標題 slug（`.../Reports-Center-3272f2ed…`）與查詢字串（`?pvs=4`），
取**最後一個** 32 位十六進位當頁面 id，連字號有無皆可。

⚠️ **模組層級狀態的讀寫兩端都要宣告 `global`**（2026-08-11 踩到，難查）。
打包進 n8n Code node 時整份程式會被包在一個函式裡（因為頂層有 `return`），
「模組層級」的變數於是變成**外層函式的區域變數**。`convert()` 有宣告 `global`
所以寫進真正的全域，讀取端若沒宣告就沿閉包讀到外層那份空的——**查得到卻換不掉，
而且完全不報錯**。`_eid_counter` 一直沒事，正是因為它讀寫兩端都有宣告。

> 這種 bug **只在打包後出現**，單獨 import 模組的單元測試全綠也照樣漏掉。
> 回歸測試放在 `scripts/test_n8n_code_node.py`（情境 2c），那支會把程式包進函式後才跑。

⚠️ **實作位置**：網址解析放在 `notion2elementor.py` 而非 `notion_blocks.py`——
轉換器必須能單獨執行且只依賴 `re`（n8n 沙箱限制），不可跨模組取用。
對照表的組裝（`build_link_map`）才在 `notion_blocks.py`，因為它吃的是 Notion API 格式。

⚠️ **n8n 的資料流有兩個容易踩的點**（2026-08-11 實測踩到）：

1. **取數節點要排在「取得頁面 blocks」之前**。放在後面的話，它們的輸入是 45 個
   區塊項目 → 各自被執行 45 次，而且輸出會**取代**區塊資料流，區塊在到達轉換器
   之前就被沖掉。
2. **HTTP 節點回傳陣列時 n8n 會拆成多個項目**。所以 `wp_docs` 要用
   `.all().map(i => i.json)` 收回成陣列，`.item.json` 只會拿到單一物件；而下游的
   「取得頁面 blocks」必須開 **Execute Once**，否則會被執行二十幾次。

⚠️ **Content Hub 目前約 90 列，查詢用 `page_size: 100`**。超過 100 列時要改成分頁
抓取，否則排在後面的文章會靜默解析不到。

## 七、寫作端注意事項（給 Support Center Writer Skill）

- 可點擊 UI 路徑一律用 inline code；不可點擊 UI 文字用粗體（Style Guide §5）
- H2＋H3 總數控制在 12 以內，超過時往 H4 降級（Style Guide §4）
- Icon button 一律使用上表五種 emoji，並用 inline code 包住、附上英文標籤：`` `⏬ (Expand)` ``；不要用其他相近 emoji
- 程式碼區塊務必標語言（http／markdown／json 等），會直接變成 WP 端語法高亮的語言設定
- 內部筆記請放在帶 toggle 的 callout 內，或標題含「Content Review Notes」，確保不會被同步
- FAQ 問題用 `###` 或 `####` 皆可，一題一個標題，答案直接接在下方
- 引用其他文章時**可以直接貼 Notion 連結**，同步時會自動換成 WP 永久連結；
  但被引用的那篇必須已經同步過（母列有 WP Post ID），否則連結會原樣留著
- **圖片的 alt text 要寫在圖說裡**，用 `可見圖說 [alt: 無障礙描述]` 格式。
  Notion API 讀不到 image block 的 alt text 欄位，在 Notion UI 填的 alt 不會同步到 WP；
  只有寫進圖說的才抓得到。不加標記時 alt 會沿用圖說文字
