# Workflow 1：sync-to-wp（骨架）

`sync-to-wp.workflow.json` — 「同步到 WP」按鈕觸發的主流程骨架。節點結構依
[n8n-workflow-blueprint.md](n8n-workflow-blueprint.md)。

**目前是骨架**：轉換器與 Notion 節點接真的，**WP 寫入節點全是 mock**（SSO 閘門放行前用假回應頂著）。

## 匯入與設定

1. n8n → Workflows → Import from File → 選 `sync-to-wp.workflow.json`
2. 設定 credential / 環境變數：
   - **Notion API credential**：四個 Notion HTTP 節點（取得子列／母列／子列內容／回寫母列）用「Predefined Credential Type → Notion API」，匯入後在每個節點的下拉選你的 Notion 憑證
   - **`CONVERTER_URL`**（n8n 環境變數）：轉換 service 位址，預設 `http://host.docker.internal:8800`（n8n 在 Docker、轉換器跑在宿主機時適用）；不同部署自行覆蓋
   - **`N8N_WEBHOOK_TOKEN`**（n8n 環境變數）：webhook header token，與 `.env` 同值。「驗證 token」節點比對 `x-synctify-token` header
3. 存檔並 **Activate**（webhook 生產網址啟用）

## Webhook 網址

- webhook path：**`synctify-sync-to-wp`**
- 生產網址：`<你的 n8n base URL>/webhook/synctify-sync-to-wp`
- 測試網址（開發時按「Listen for test event」）：`<base>/webhook-test/synctify-sync-to-wp`

**Content Hub 的「同步到 WP」按鈕填生產網址**，並帶 header `x-synctify-token: <N8N_WEBHOOK_TOKEN>`，
body 帶按下的版本子列 page id：`{ "page_id": "<notion-page-id>" }`。

## 節點：真實 vs mock

| 節點 | 狀態 |
| --- | --- |
| Webhook、驗證 token、回應 | ✅ 真實 |
| Notion：取得子列 / 母列 / 子列內容 / 回寫母列狀態 | ✅ 真實（走 Notion API） |
| Blocks → Markdown（Code） | ✅ 真實（best-effort v0，見下方落差） |
| 呼叫轉換器 /convert | ✅ 真實 |
| 圖片處理 | 🔸 MOCK（NoOp）— 閘門放行後改 Loop + `POST /wp/v2/media` |
| 判斷路徑（Switch） | ✅ 真實邏輯（但依母列 `上稿狀態` 當線上狀態代理，見落差） |
| 9a/9b/9c、寫入 Elementor/SEO/FAQ | 🔸 MOCK（Set 假回應：假 Post ID 999001/999002、ok=true） |

分路邏輯（Switch）：母列無 WP Post ID → 新文章；有 Post ID 且上稿狀態≠已發佈 → 更新草稿；有 Post ID 且＝已發佈 → 影子草稿。

## ⚠️ 執行前必讀

**「回寫母列狀態」是真實 Notion 寫入**。在 mock 階段，它會把**假 Post ID（999001/999002）**與狀態寫進真實母列。
所以**請用一個拋棄式的測試 guide 母列＋版本子列**來跑端到端測試，不要對正式 Content Hub 的 guide 列按按鈕，直到 WP 寫入換成真的。

## 已知落差（骨架待補，非 bug）

1. **Notion 屬性名稱**：節點用了假設的屬性名 `Parent item`、`Doc name`、`WP Post ID`、`上稿狀態`、`最後同步時間`。
   匯入後請對照實際 Content Hub 屬性名，不符就改節點內的屬性 key。
2. **Callout icon/color 對映**：`Blocks → Markdown` 直接輸出 Notion API 給的 emoji 與 `green_background` 類色值；
   但轉換器 `callout_type()` 期望的是 `checkmark`/`light-bulb`/`warning`/`info` 與 `green_bg`/`yellow`/`red` 類字串
   （來自舊 Notion 匯出格式，見 `samples/`）。**兩者需要一層對映**，否則 callout 分類會失準。這是 Notion→markdown
   保真度的核心待辦，建議與產出 `samples/` 的來源流程對齊。
3. **Blocks → Markdown v0 覆蓋度**：巢狀清單、table、多層 children、分頁（>100 blocks）尚未處理。
4. **圖片處理**：mock。真實版要對 `report.images` 中 `pending_upload=true` 者下載 Notion S3 → 上傳 WP 媒體庫
   （帶 alt/caption）→ 取 media ID → 回填 Elementor JSON 圖片網址。
5. **Switch 線上狀態**：目前用母列 `上稿狀態` 當「WP 線上是不是已發佈」的代理。閘門放行後可改成實際讀 WP 文章 status。

## 換成真實 WP（閘門放行後）

把五個 mock 節點（圖片處理、9a/9b/9c、寫入 Elementor/SEO/FAQ）換成真實 HTTP Request：
`POST /wp/v2/docs`、`POST /synctify/v1/elementor/{id}`、`POST /synctify/v1/seo/{id}`、`POST /wp/v2/faq`、`POST /wp/v2/media`，
WP 認證用 Application Password credential。先跑 `scripts/verify_endpoints.py` 確認端點可達再切換。
