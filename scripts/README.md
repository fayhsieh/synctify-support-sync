# scripts

## wp_env.py —— 兩站的帳密共存

`.env` 同時放正式站與測試站的憑證，腳本用 `--target` 選，不必改檔案切來切去。
命名慣例跟 `N8N_WEBHOOK_PATH` / `N8N_WEBHOOK_PATH_TEST` 一致：

| target | 站台 | .env 變數 |
| --- | --- | --- |
| `prod`（多數腳本的預設） | support.synctify.net | `WP_USERNAME` / `WP_APP_PASSWORD` |
| `test` | support.synctify.io | `WP_USERNAME_TEST` / `WP_APP_PASSWORD_TEST` |

`WP_BASE_URL` / `WP_BASE_URL_TEST` 可留空，模組內有預設網址。

確認兩站都填齊了：

```bash
./.venv/bin/python scripts/wp_env.py
```

**兩站的 Application Password 各自獨立**，拿錯那組會 401——而那個 401 的外觀
跟站台故障、跟被安全外掛攔截都很像。搬站期間最容易誤判的就是這一項，所以錯誤
訊息會指名是哪個站台的哪個變數。


## glossary_audit.py / glossary_sync.py —— 術語表對帳與回寫

`glossary_audit.py` 是**唯讀報告**（簡繁檢查、一致性對帳、候選新詞）；
`glossary_sync.py` 把**算得出來的欄位**寫回 Notion 的產品用術語表。

```bash
./.venv/bin/python scripts/glossary_sync.py --target test          # dry-run
./.venv/bin/python scripts/glossary_sync.py --target test --write
```

| | |
| --- | --- |
| **會寫** | 文件現況、OMS v0 現況、i18n key、一致性、文件出現次數、OMS 使用處數 |
| **絕不碰** | 简体中文、繁體中文、已確認、備註、類型 |

這條界線是最重要的設計。術語表要成為單一真實來源，靠的是「每一筆都有人決定過」；
腳本一旦能覆蓋 `简体中文` 或 `已確認`，那個保證就沒了——與外掛 `/tp/update`
永不覆蓋 `status=2` 是同一個原則。

資料來源是 OMS repo 的 `resources/lang/`（需 `gh` 已登入）與 Support Center 的
TranslatePress 人工譯文。需要 `.env` 的 `NOTION_API_KEY`。

**OMS 使用處數比文件出現次數重要**：前者是該字串對應幾個 i18n key ＝ 改動會影響
產品幾個地方。2026-08-14 實測文件次數 97 筆都是 1、幾乎無鑑別度，而 OMS 處數
分布在 1–14；`Active` 在產品用了 10 處、文件 0 次——只看文件次數會把影響面
最大的詞排到最底。


## verify_endpoints.py

對測試站的六個 `/synctify/v1/` 端點各實打一次驗證（含認證負向測試與自動清理）。
用一篇專用測試草稿當標的，不碰既有內容；`tp/update` 用 bogus id 冒煙，不動真實譯文。

**前置：**

1. `.env` 填好測試站那組（`WP_USERNAME_TEST` / `WP_APP_PASSWORD_TEST`，見上）
2. 啟動轉換 service：

   ```bash
   ./.venv/bin/python -m uvicorn service.app:app --port 8800
   ```

**執行：**

```bash
./.venv/bin/python scripts/verify_endpoints.py
```

轉換 service 若不在預設位址，用 `CONVERTER_URL` 覆蓋。

> ⚠️ **這支會寫入**，所以預設打測試站，跟唯讀的 `verify_site_ready.py` 相反。
> 要打正式站得自己加 `--target prod`，而且會再問一次確認（`--yes` 可跳過）。


## verify_site_ready.py —— 搬站前的前置檢查（唯讀）

把上稿流程搬到另一個站台前先跑一次，確認那個站台具備所有先決條件。
**全部是 GET 與唯讀的 POST，可以安全地對正式站執行。**

```bash
./.venv/bin/python scripts/verify_site_ready.py                  # 正式站（預設）
./.venv/bin/python scripts/verify_site_ready.py --target test    # 測試站
```

帳密由 `wp_env` 依 `--target` 取（見上）。`--base` 可以只覆寫網址、沿用同一組帳密。

檢查八組：連線與認證、輔助外掛與 9 條路由、站方預設欄位的三個名稱解析
（封面照／作者／分類頁）、Arconix FAQ、**Notion 記錄的 WP Post ID 是否指向
正確的文章**、只存在於測試站的文章、相依外掛、發佈回呼設定。

第五項最關鍵，也刻意分成兩種嚴重度：

- **文章不存在** → 硬性失敗。ID 無效，同步一定出錯。
- **標題與 Notion 不同** → 只警告。可能是還沒同步過去的改名（例如 `5-1` 在 Notion
  已改成 Manage Sales Orders），也可能是 ID 指向了別篇文章——**腳本沒資格判定**，
  兩個值都印出來交由人確認。

對照表取自 Notion（Fay 已逐列與正式站對齊），不是測試站的快照。文章改名或新增後
要更新 `EXPECTED_POSTS`。

認證沒過會直接停在第一組——否則後面每一項都變成「找不到」，25 行誤導訊息比
一行真正的原因難懂得多。
