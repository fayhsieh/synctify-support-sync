# Workflow 3 翻譯節點：Support Article Writer（Miles）prompt 移植方案

## 前提：只移植 Workflow B（翻譯），且要改成「翻片段」而非「翻整篇」

Skill 有兩個工作流：
- **Workflow A**：技術素材 → 全新英文文章。**不移植**（寫作端留在原流程）。
- **Workflow B**：定稿英文 → 簡體中文。**移植這半**。

關鍵差異：Skill 的 Workflow B 翻譯「整篇文章」；n8n 翻譯「TranslatePress 撈出的差異字串」（只有改動段落）。所以移植 Skill 的**在地化與術語規則**，**不移植**它產出完整文章結構（SEO Meta / Version History / 章節）的部分。

## 兩個 OpenAI 節點

### 節點 4「抽術語」

從未翻譯字串中抽出需要確認譯法的詞：功能專有名稱、產品模組、狀態名、按鈕名、欄位名、UI 用詞、需保留英文的詞。輸出候選詞清單。

System prompt 取自 Skill 的術語 gate 概念，但改成「抽出候選詞」而非「問使用者」：

> You extract terms that need translation confirmation from Synctify Support Center English strings before Simplified Chinese localization. Identify: feature/product/module names, status names, button names, field names, UI labels, and terms that should stay in English. Output a JSON list of candidate terms only. Do not translate.

### 節點 9「正式翻譯」

套用「已確認術語表」＋ Skill 的簡中在地化規則翻譯字串。System prompt 移植 Skill 這幾節（**只取翻譯相關,去掉文章結構**）：

- **Simplified Chinese Localization**：自然簡中、避免繁中與台灣用語、保留未提供譯名的英文 UI label、術語一致
- **UI wording and formatting**：可點擊 UI 路徑用 inline code（`>` 分隔）、不可點擊狀態用粗體、不要改寫可見 UI label
- **Icon-only controls**：沿用 `✏️(Edit)` 等 emoji＋英文動作名
- **Tables**：表格內預設不用 inline code
- **不臆測**：來源不明時用保守用詞

已確認術語表以變數注入 prompt（例：`Workspace → 工作区`、`Pending → 待处理`、`[term] → 保留英文`）。

## 術語 gate → Notion Wait 節點（不是對話）

Skill 原本是「在對話裡用繁中問使用者確認術語」。n8n 沒有對話,改成:
- 節點 5 比對詞彙對照表,分出「已有定案」與「新詞」
- **只有新詞**才進節點 7 的 Wait:把新詞清單發到 Notion(繁中呈現、附建議譯法),等人工在 Notion 確認後恢復
- 確認過的新詞寫回詞彙對照表(節點 8),下次同詞不再問

這樣既保留 Skill「翻譯前先確認術語」的品質保證,又符合自動化(無真人對話)。

## 品質對照測試(移植後必做)

1. 挑 3–5 段你們之前用 Miles Skill 翻過、且滿意的英文原文
2. 用 n8n 移植版(同模型＋上面的 prompt＋對應術語表)再翻一次
3. 逐段比對:術語是否一致、UI label 是否正確保留、語氣是否自然簡中
4. 有落差就微調節點 9 的 system prompt,直到與 Skill 產出一致

閘門未放行也能做這個測試——純 OpenAI,不碰 WP。

## 一個小對齊點

Skill 的 icon 格式是 `✏️(Edit)`(emoji＋括號無空格),我們 Notion 寫作慣例是 `` `⏬ (Expand)` ``(inline code＋空格)。轉換器兩種都吃得下(偵測 emoji 開頭後取剩餘文字),但建議寫作端統一一種,避免站上風格不一。這條回頭跟寫作流程對一下即可,不影響翻譯節點。

## 模型

用跟 Miles Skill 相同的底層模型,確保品質一致(Skill 的 `agents/openai.yaml` 只有介面設定、沒指定模型,實際模型請 Fay 從 ChatGPT 端確認後填入 n8n OpenAI 節點)。
