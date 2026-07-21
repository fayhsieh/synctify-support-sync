# 給 Claude Code 的專案脈絡

## 專案本質

把 Synctify Support Center 的教學文件從 Notion 自動同步到 WordPress。核心難點不是搬運，是**格式映射**——Notion 的寫法要精確轉成 Docly/EazyDocs 主題的 Elementor widget 結構，且要能安全地更新已發佈文章。

## 動之前先讀

`docs/mapping-rules.md` 是這個專案的規格書。轉換器的任何行為改動都應該先反映在這份規則上，兩者必須同步。規則是從 4 篇正式站文章的實際 Elementor 匯出檔逆向確認出來的，不是憑空設計的——所以**不要憑直覺改映射邏輯**，改之前先確認實際站上的 widget 長什麼樣。

## 幾個容易踩的點

**`_elementor_data` 的儲存格式**：是 JSON 字串存在 post meta，寫入時需要 `wp_slash()`，否則反斜線會被剝除導致 Elementor 解析失敗。改完要清 Elementor CSS 快取才會生效。

**已發佈文章不能直接覆蓋**：WordPress 沒有「已發佈文章的草稿修訂版」。更新既有文章一律走「影子草稿」——另建一篇草稿放新內容，人工確認後才覆蓋正式文章。輔助外掛的 elementor 端點寫入前會自動備份最近 3 版。

**TranslatePress 譯文有狀態**：`status=2` 是人工精修過的譯文，機器翻譯**永遠不可覆蓋**。`/tp/update` 端點已內建這個保護，改動時不要拿掉。

**Notion 內容有雜訊**：頁面內容會夾帶 discussion span、notionvc 註解等留言標記，轉換時要剔除。另外內部審核筆記（帶 `<details>` toggle 的 callout、標題含 Content Review Notes）絕對不能同步到公開站上。

**inline code 有兩種語意**：一般的 `` `Submit` `` 轉成 `[direction]` shortcode（可點擊 UI 路徑），但如果內容以 icon emoji 開頭（如 `` `⏬ (Expand)` ``）要轉成 `[custom_icon]` shortcode。判斷順序不能反。

## 驗證方式

轉換器改動後，用 `samples/` 裡的樣本跑一次，把輸出的 `elementor-template-output.json` 匯入測試站（Elementor → Templates → Import）視覺比對。輸出 JSON 的 schema 必須和站上匯出檔一致（top-level 有 content/page_settings/version/title/type，元素有 id/settings/elements/isInner/elType）。

WP 端改動一律先在測試站（support.synctify.io，WP 7.0）驗證，正式站是 support.synctify.net。

## 不要提交的東西

Application Password、OpenAI key、webhook token、n8n 帶憑證的 workflow 匯出檔。`.gitignore` 已涵蓋，但 n8n 匯出時要自己確認一次憑證欄位是引用而非明文。
