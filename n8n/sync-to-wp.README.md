# Workflow 1：sync-to-wp（骨架）

`sync-to-wp.workflow.json` — 「同步到 WP」按鈕觸發的主流程骨架。節點結構依
[n8n-workflow-blueprint.md](n8n-workflow-blueprint.md)。

**目前是骨架**：轉換器與 Notion 節點接真的，**WP 寫入節點全是 mock**（SSO 閘門放行前用假回應頂著）。

## 匯入與設定

1. n8n → Workflows → Import from File → 選 `sync-to-wp.workflow.json`
2. 設定 credential / 環境變數：
   - **Notion API credential**：四個 Notion HTTP 節點（取得子列／母列／子列內容／回寫母列）用「Predefined Credential Type → Notion API」，匯入後在每個節點的下拉選你的 Notion 憑證
   - **`CONVERTER_URL`**（n8n 環境變數）：轉換 service 位址。**內網 n8n 連得到才行**，見下方「轉換器部署」
   - **`N8N_WEBHOOK_TOKEN`**（n8n 環境變數）：webhook header token，與 `.env` 同值。「驗證 token」節點比對 `x-synctify-token` header
3. 存檔並 **Activate**（webhook 生產網址啟用）

## 轉換器部署與 CONVERTER_URL（測試前必解）

轉換器（`service/`）目前跑在開發機 localhost:8800。n8n 是內網伺服器
`automation.internal.synctify.net`，**連不到開發機的 localhost**，所以轉換器必須部署在內網 n8n
能觸達的位置。依 n8n 跑法選 `CONVERTER_URL`：

| n8n 跑法 / 轉換器位置 | CONVERTER_URL |
| --- | --- |
| 轉換器與 n8n 同一個 docker-compose（同 network） | `http://<compose 服務名>:8800`（推薦） |
| 轉換器 container 與 n8n 同主機、n8n 在 Docker | `http://host.docker.internal:8800` |
| 轉換器獨立內網服務（有內網 DNS） | `http://converter.internal.synctify.net:8800` |
| n8n 直接跑在主機（非 Docker）、轉換器同主機 | `http://127.0.0.1:8800` |

> 開發機 localhost **不是**可行選項（內網 n8n 打不到）。最省事是把轉換器包成 container 塞進 n8n 的
> compose，用服務名互連。轉換器已可容器化（`service/` + `service/requirements.txt`）。

## Webhook 網址

- webhook path：**`synctify-sync-to-wp`**
- n8n base：`https://automation.internal.synctify.net`
- 生產網址：`https://automation.internal.synctify.net/webhook/synctify-sync-to-wp`
- 測試網址（開發時按「Listen for test event」）：`https://automation.internal.synctify.net/webhook-test/synctify-sync-to-wp`

> ⚠️ base host 帶 `internal`：若此 n8n 僅限內網／VPN，Notion 雲端按鈕（發自 Notion 伺服器）可能打不到。
> 上線前需確認生產 webhook 對 Notion 可達，否則按鈕無法觸發（同 SSO 閘門那類的可達性問題）。

**Content Hub 的「同步到 WP」按鈕填生產網址**，並帶 header `x-synctify-token: <N8N_WEBHOOK_TOKEN>`，
body 帶按下的版本子列 page id：`{ "page_id": "<notion-page-id>" }`。

## 用 pinData 在畫布直接測試

workflow 已內建 `pinData`，Webhook 節點釘了一筆測試資料（Amazon SC v2 子列
`3492f2ede27d80b68faceb92929e5a0c`）。在 n8n 畫布按 **Execute Workflow** 即可用這筆跑，不必打 webhook。

⚠️ pinData 裡的 `x-synctify-token` 是佔位字串 `REPLACE_WITH_YOUR_N8N_WEBHOOK_TOKEN`——
**在 n8n UI 把它改成你真正的 token** 才過得了「驗證 token」節點（真 token 不入 repo）。
或改打測試 webhook：

```bash
curl -X POST https://automation.internal.synctify.net/webhook-test/synctify-sync-to-wp \
  -H "Content-Type: application/json" \
  -H "x-synctify-token: <你的 N8N_WEBHOOK_TOKEN>" \
  -d '{"page_id":"3492f2ede27d80b68faceb92929e5a0c"}'
```

只測前半段（Webhook→轉換器）時，先把尾端「回寫母列狀態」節點停用，避免寫髒真實母列。

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

1. ~~**Notion 屬性名稱**~~ ✅ 已對 schema 驗證（2026-07-23）：`Doc name`(title)、`Parent item`(relation)、
   `WP Post ID`(text)、`上稿狀態`(select)、`最後同步時間`(date) 全部與 Content Hub 一致。
   注意：另有 `Status`（status 型）＝內容工作流狀態，**與 `上稿狀態` 不同**，別誤用；`Version` 的 v1 標籤是
   `"v1 (Initial Version)"`（Workflow 2 比對時注意）。標題已在「組裝參數」節點去掉 `- vN`／`(Current)` 後綴。
2. ~~**Callout icon/color 對映**~~ ✅ 已解決：轉換器 `callout_type()` 現在同時吃 Notion 原生 emoji＋
   `*_background` 底色，與舊匯出 icon-path 兩種格式（見 `docs/mapping-rules.md` §二、
   `converter/test_callout_mapping.py`）。`Blocks → Markdown` 直接輸出 Notion 原生格式即可，n8n 端不需另做對映。
3. **Blocks → Markdown v0 覆蓋度**：巢狀清單、table、多層 children、分頁（>100 blocks）尚未處理。
4. **圖片處理**：mock。真實版要對 `report.images` 中 `pending_upload=true` 者下載 Notion S3 → 上傳 WP 媒體庫
   （帶 alt/caption）→ 取 media ID → 回填 Elementor JSON 圖片網址。
5. **Switch 線上狀態**：目前用母列 `上稿狀態` 當「WP 線上是不是已發佈」的代理。閘門放行後可改成實際讀 WP 文章 status。

## 換成真實 WP（閘門放行後）

把五個 mock 節點（圖片處理、9a/9b/9c、寫入 Elementor/SEO/FAQ）換成真實 HTTP Request：
`POST /wp/v2/docs`、`POST /synctify/v1/elementor/{id}`、`POST /synctify/v1/seo/{id}`、`POST /wp/v2/faq`、`POST /wp/v2/media`，
WP 認證用 Application Password credential。先跑 `scripts/verify_endpoints.py` 確認端點可達再切換。
