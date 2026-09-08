#!/usr/bin/env python3
"""
同步失敗的錯誤代碼表——**單一真實來源**。

n8n 用的 JavaScript 判斷式、Notion 上給小編看的對照表，都由這份產生。
不要在 n8n UI 或 Notion 上手動維護第二份，那必定會漂移。

## 分類軸是「下一步做什麼」，不是「誰的錯」

小編（Iris）2026-09-05 建議做錯誤代碼表，原始提案分成「人為操作失誤」與
「程式錯誤」兩類。改成現在這三格，理由是實際錯誤不乾淨地落在那兩類：

  * Notion 圖片網址過期是**程式面的**，但小編自己重按一次就好
  * Category 填了站上還沒建的分類頁是**操作面的**，但小編改不了，要有人去建

用「誰的錯」分，這兩個都會被放錯格。而且「誰的錯」隱含究責，
「下一步做什麼」才是看到錯誤的人當下真正需要的資訊。

  A  Notion 上的內容或欄位有問題  → 改完再按一次同步
  B  暫時性失敗，跟內容無關      → 直接再按一次
  C  環境或程式問題              → 找 Fay，重按沒用

## 比對用「小寫子字串」而不是正規表達式

Python 與 JavaScript 的 regex 方言不同（跳脫、旗標、字元類別都有差異），
同一個 pattern 兩邊行為未必一致——而這份表的價值就在於兩邊必須一致。
子字串比對在兩種語言裡完全相同，沒有方言問題，代價是表達力低一點，值得。

**順序即優先序，先命中先贏**，所以specific 的要排在 generic 前面。

## 沒命中怎麼辦

退回 C0（找 Fay）。這是安全的預設值：把未知錯誤丟給人，不會讓小編照著
錯誤的指示亂改。C0 同時是「這個錯誤還沒進表」的訊號，看到就該補一條。
"""
import json

# (代碼, 命中關鍵字（小寫子字串）, 說明, 該做什麼)
#
# ⚠️ 說明與動作裡**不要用單引號**——產生出來的 JS 用單引號包字串。
#    中文引號「」是安全的。有 assert 會擋住，不會靜默壞掉。
CODES = [
    # ── A：Notion 上的內容或欄位有問題，改完再按一次 ────────────────────
    ("A1", ["category_not_found", "no category page titled"],
     "站上找不到 Notion Category 欄位對應的分類頁。",
     "請確認該列的 Category 填對了；若那個分類確實還沒在站上建立，請通知 Fay。"
     "訊息裡的「可用分類」是站上目前真的有的分類頁。"),

    ("A2", ["rest_post_invalid_id", "invalid post id"],
     "這一列的 WP Post ID 指向一篇不存在的文章。",
     "請檢查該列的 WP Post ID 欄位。如果這篇是要**新建**的文章，把欄位清空再按一次；"
     "同步完成後會自動填回正確的 ID。"),

    ("A3", ["__mother_row__"],
     "按到的是最上層母列，它沒有內容區塊。",
     "請改按底下帶 - vN 的版本子列。"),

    ("A4", ["__draft_layer__"],
     "按到的是 (Draft) 草稿層（第三層），這一層不會同步到站上。",
     "請改按正式的版本子列。"),

    ("A5", ["__not_approved__"],
     "這一列的 Status 還不是可以同步的狀態，內容可能還沒審核完。",
     "請先確認 Copy Approved 與 Image Approved 兩個勾勾都打了，"
     "再把 Status 改成 Content Approved，然後重新按同步。",),

    # ── B：暫時性失敗，直接再按一次 ────────────────────────────────────
    ("B1", ["request has expired", "accessdenied", "<code>expired"],
     "Notion 的圖片網址已過期（Notion 給的暫存網址只有一小時有效）。",
     "直接再按一次同步就會重新取得網址。不用改任何東西。"),

    ("B2", ["etimedout", "econnreset", "socket hang up", "esockettimedout",
            "enotfound", "eai_again"],
     "連線逾時或中斷，跟文章內容無關。",
     "等一分鐘再按一次。連續三次都這樣的話再找 Fay。"),

    # ── C：環境或程式問題，找 Fay ──────────────────────────────────────
    ("C1", ["rest_forbidden", "incorrect_password", "rest_cannot_edit",
            "rest_not_logged_in"],
     "WordPress 拒絕了這次請求（認證或權限）。",
     "請找 Fay。這是 n8n 的憑證問題，重按不會好。"),

    ("C2", ["x-amzn-waf", "awswaf", "challenge"],
     "請求被站台前面的防火牆（WAF）攔住了。",
     "請找 Fay 確認 n8n 的來源 IP 是否還在白名單內。重按不會好。"),

    ("C3", ["rest_invalid_param"],
     "WordPress 說參數不合法——這通常代表認證被跳過了，不是參數真的寫錯。",
     "請找 Fay 檢查 n8n 裡的站台位址設定（走 http 會讓認證失效）。"),

    ("C4", ["no_elementor", "no_document", "autosave_failed", "not_autosave"],
     "Elementor 那一端出問題，版面寫不進去。",
     "請找 Fay。可能是 Elementor 版本或文章狀態的問題。"),

    ("C5", ["no_arconix", "no_aioseo", "no_trp"],
     "站上有個必要的外掛沒啟用（FAQ／SEO／翻譯其中之一）。",
     "請找 Fay 到 WordPress 後台把外掛啟用。"),

    ("C6", ["root_not_found", "featured_not_found", "author_not_found"],
     "站方預設缺件：文件根節點、預設封面照或作者其中之一找不到。",
     "請找 Fay。這是站台設定問題，跟這篇文章無關。"),

    ("C7", ["rest_no_route"],
     "找不到這個 API 端點——輔助外掛沒安裝，或版本太舊。",
     "請找 Fay 確認 Synctify Sync Helper 外掛的版本。"),
]

FALLBACK = ("C0",
            "發生了還沒被歸類的錯誤。",
            "請把這則留言整段複製給 Fay。她會把它補進錯誤代碼表。")

# ── W：同步「成功了」，但結果有問題 ─────────────────────────────────────
#
# 跟 A／B／C 是不同的東西，所以用不同的前綴：A／B／C 是「沒成功，要重來」，
# W 是「成功了，但你最好看一下」。混在一起小編會分不清要不要重按。
#
# 這三項轉換器**本來就在算**，也確實輸出了，但 2026-09-05 之前下游沒有任何
# 節點去讀——算完就丟掉。所以這不是新增偵測，是把既有的偵測接出來。
#
# (代碼, 來源欄位, 說明, 該做什麼, 明細要顯示哪些鍵（空＝值本身就是字串）)
WARNINGS = [
    ("W1", "unrecognized_section_markers",
     "有段落標記可能打錯了，那幾段會落回預設行為（該折疊的沒折疊）。",
     "請檢查這些 h2 標題結尾的括號標記。正確寫法只有 (Accordion) 與 (Plain) 兩種。",
     ["marker", "heading"]),

    ("W2", "still_placeholder",
     "有圖片沒有成功上傳，站上那幾張目前是灰色的佔位圖。",
     "多半是 Notion 圖片網址過期。直接再按一次同步通常就會好；"
     "連續兩次都這樣請找 Fay。",
     ["alt"]),

    ("W3", "unresolved_notion_links",
     "有 Notion 連結沒有換成站上的網址。",
     "讀者點下去會被導到 Notion（他們打不開）。請確認被連到的那篇文章在"
     " Content Hub 裡填了 WP Post ID；沒有的話要先同步那一篇。",
     []),
]

# 哪些 Status 可以同步（**允許清單**，不是封鎖清單）。
#
# 用允許清單是刻意的：之後若有人在 Notion 新增了 Status 選項，封鎖清單會**預設放行**
# ——那正是這道防呆要擋的事情悄悄溜過去。允許清單則會擋下來、報 A5，
# 訊息裡帶著實際的 Status，看一眼就知道要把新選項加進來。防呆要往安全方向倒。
#
# `Existing` 一定要放行：文章發佈後回呼會把**子列**的 Status 寫成 Existing
# （wp-publish-callback 的「Notion：子列也標記已發佈」），
# 擋掉它就等於不能再同步修正——5601、5620 那兩次修正都會被擋住。
SYNCABLE_STATUS = ["Content Approved", "Existing"]

BUCKETS = {
    "A": ("Notion 上的內容或欄位有問題", "改完再按一次同步"),
    "B": ("暫時性失敗，跟內容無關", "直接再按一次"),
    "C": ("環境或程式問題", "找 Fay，重按沒用"),
}


def _check_table():
    """表本身的健康檢查——產生前先跑，壞掉的表不該被產出去。"""
    seen = set()
    for code, keys, why, todo in CODES:
        assert code not in seen, f"代碼重複：{code}"
        seen.add(code)
        assert code[0] in BUCKETS, f"{code} 的分類不存在"
        assert keys, f"{code} 沒有任何命中關鍵字"
        for text in (why, todo):
            # 產生的 JS 用單引號包字串，內文有單引號會直接讓運算式壞掉
            assert "'" not in text, f"{code} 的文字含單引號，會破壞產生的 JS：{text}"
        for k in keys:
            assert k == k.lower(), f"{code} 的關鍵字必須全小寫：{k}"
    # 關鍵字不可被更前面的條目吃掉——先命中先贏，順序錯了會靜默歸錯格
    for i, (code, keys, _, _) in enumerate(CODES):
        for k in keys:
            for prev_code, prev_keys, _, _ in CODES[:i]:
                for pk in prev_keys:
                    assert pk not in k, (
                        f"{code} 的關鍵字 {k!r} 含有更前面 {prev_code} 的 {pk!r}，"
                        f"永遠輪不到 {code}。請調整順序或改關鍵字。")


def _check_warnings():
    """W 的表也要檢查——同樣的失誤在這裡一樣會靜默壞掉。"""
    seen = set()
    for code, key, why, todo, keys in WARNINGS:
        assert code not in seen, f"警告代碼重複：{code}"
        seen.add(code)
        assert code.startswith("W"), f"{code} 不是 W 開頭"
        assert key, f"{code} 沒有來源欄位"
        for text in (why, todo):
            assert "'" not in text, f"{code} 的文字含單引號，會破壞產生的 JS"


_check_table()
_check_warnings()


def classify(raw):
    """把原始錯誤字串歸類。回傳 (代碼, 說明, 該做什麼)。"""
    s = str(raw or "").lower()
    for code, keys, why, todo in CODES:
        for k in keys:
            if k in s:
                return code, why, todo
    return FALLBACK


def format_reason(raw, pretty=None):
    """組出要留言回 Notion 的完整訊息。與 JS 版必須一致（有測試比對）。"""
    code, why, todo = classify(raw)
    out = f"[{code}] {why}\n→ {todo}"
    if pretty:
        out += f"\n（原始訊息：{pretty}）"
    return out


def to_js():
    """產生等價的 JavaScript 判斷式，供 n8n 運算式內嵌使用。

    刻意產生成一個吃字串、回字串的 IIFE，沒有任何外部相依，
    貼進 n8n 的 Set node 運算式就能用。
    """
    table = [[c, k, w, t] for c, k, w, t in CODES]
    return (
        "(function(raw){"
        " var s = String(raw || []).toLowerCase();"
        " var T = " + json.dumps(table, ensure_ascii=False) + ";"
        " for (var i = 0; i < T.length; i++) {"
        " for (var j = 0; j < T[i][1].length; j++) {"
        " if (s.indexOf(T[i][1][j]) >= 0) {"
        " return [T[i][0], T[i][2], T[i][3]]; } } }"
        " return " + json.dumps(list(FALLBACK), ensure_ascii=False) + "; })"
    )


def to_reason_js():
    r"""產生 n8n `原因：節點失敗` 節點要用的完整運算式內容（不含外層 {{ }}）。

    整段 JS 集中在這裡產生，而不是在 build script 裡拼字串——那邊要疊到四層
    反斜線跳脫（Python 字串 → JSON → n8n 運算式 → JS regex），改一次錯一次，
    而且沒辦法測。放這裡就能直接丟給 node 驗。

    兩個實測踩到的細節：

    1. **不要先剝反斜線再抓 message**。WP 的錯誤長這樣：
           422 - {"code":"category_not_found","message":"No category page
                  titled \"Automation\" under \"Synctify Documentation\""}
       先剝掉反斜線的話 `\"` 變成真引號，`[^"]+` 會在分類名前面就停住，
       訊息被腰斬成「No category page titled」——最關鍵的分類名反而不見了。
       所以改成在**原文**上用容許跳脫的 `(?:[^"\\]|\\.)*`，抓到之後才還原。

    2. **`available` 在 data 裡，不在 message 裡**。分類找不到時，端點是把
       站上可用的分類放在 `data.available`。不特別抓出來的話，小編永遠看不到
       「那到底有哪些分類可以選」——那正是他當下唯一需要的資訊。
    """
    return (
        "(function(m){"
        " var raw = String(m);"
        # 分類用原文（關鍵字都是小寫 ASCII，跳脫與否不影響命中）
        " var c = " + to_js() + "(raw);"
        # 訊息：容許跳脫的引號，抓完再還原
        " var im = raw.match(/\"message\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"/);"
        " var msg = im ? im[1].replace(/\\\\\"/g, '\"').replace(/\\\\\\\\/g, '\\\\') : '';"
        " var cm = raw.match(/\"code\"\\s*:\\s*\"([^\"]+)\"/);"
        " var st = raw.match(/(\\d{3})/);"
        " var pretty = msg ? ((st ? st[1] + ' ' : '') + msg"
        " + (cm ? '（' + cm[1] + '）' : ''))"
        " : raw.replace(/\\\\\\\\/g, '').slice(0, 300);"
        # 可用分類清單（只有 A1 會有）
        " var av = raw.match(/\"available\"\\s*:\\s*\\[([^\\]]*)\\]/);"
        " var extra = av ? ('\\n可用分類：' + av[1].replace(/\\\\?\"/g, '')"
        ".replace(/,/g, '、')) : '';"
        " return '[' + c[0] + '] ' + c[1] + '\\n→ ' + c[2]"
        " + '\\n（原始訊息：' + pretty + '）' + extra;"
        "})"
    )


def format_warnings(data, limit=5):
    """把警告資料組成訊息。data 是 {代碼: [明細, ...]}。沒有警告回空字串。

    與 JS 版必須一致（有測試比對）。
    """
    out = []
    for code, _key, why, todo, label_keys in WARNINGS:
        items = data.get(code) or []
        if not items:
            continue
        labels = []
        for it in items[:limit]:
            if isinstance(it, dict):
                parts = [str(it.get(k, "")) for k in label_keys]
                # 退路要與 JS 的 JSON.stringify 一模一樣。用 str(dict) 會得到
                # Python 的 repr（單引號、逗號後有空格），JS 那邊是 JSON——
                # 兩邊就不一致了。parity 測試 2026-09-05 抓到過。
                labels.append(" ".join(p for p in parts if p)
                              or json.dumps(it, ensure_ascii=False,
                                            separators=(",", ":")))
            else:
                labels.append(str(it))
        more = " …" if len(items) > limit else ""
        out.append(f"[{code}] {why}\n→ {todo}\n"
                   f"（{len(items)} 處：{'、'.join(labels)}{more}）")
    return "\n\n".join(out)


def to_warning_js(limit=5):
    """產生 n8n 用的警告組裝函式。吃 {代碼: [明細]}，回字串（沒警告＝空字串）。

    回空字串是刻意的：下游用「非空」當作要不要留言的條件，
    這樣「沒有警告」就不會在 Notion 上留下一則說「沒有警告」的噪音。
    """
    table = [[c, w, t, keys] for c, _k, w, t, keys in WARNINGS]
    return (
        "(function(d){"
        " var W = " + json.dumps(table, ensure_ascii=False) + ";"
        " var out = [];"
        " for (var i = 0; i < W.length; i++) {"
        " var items = (d || {})[W[i][0]] || [];"
        " if (!items.length) continue;"
        " var labels = [];"
        " for (var j = 0; j < Math.min(items.length, " + str(limit) + "); j++) {"
        " var it = items[j];"
        " if (it && typeof it === 'object') {"
        " var parts = [];"
        " for (var k = 0; k < W[i][3].length; k++) {"
        " var v = it[W[i][3][k]];"
        " if (v !== undefined && v !== null && String(v) !== '') parts.push(String(v)); }"
        " labels.push(parts.length ? parts.join(' ') : JSON.stringify(it));"
        " } else { labels.push(String(it)); } }"
        " out.push('[' + W[i][0] + '] ' + W[i][1] + '\\n→ ' + W[i][2]"
        " + '\\n（' + items.length + ' 處：' + labels.join('、')"
        " + (items.length > " + str(limit) + " ? ' …' : '') + '）');"
        " }"
        " return out.join('\\n\\n'); })"
    )


def to_markdown():
    """產生給 Notion 用的對照表。"""
    lines = ["# Synctify 上稿：同步失敗代碼表", "",
             "同步失敗時，Notion 會在該列留言，開頭就是代碼。",
             "代碼的第一個字母告訴你**下一步該做什麼**：", ""]
    for b, (what, action) in BUCKETS.items():
        lines.append(f"- **{b}** —— {what} → **{action}**")
    lines += ["", "---", "",
              "| 代碼 | 發生什麼事 | 你該做的事 |", "| --- | --- | --- |"]
    for code, _, why, todo in CODES:
        if code in ("A3", "A4"):
            pass   # 防呆訊息也走同一套代碼，一併列出
        lines.append(f"| **{code}** | {why} | {todo} |")
    lines.append(f"| **{FALLBACK[0]}** | {FALLBACK[1]} | {FALLBACK[2]} |")
    lines += ["", "---", "",
              "## W —— 同步成功了，但結果有問題", "",
              "看到 W 開頭的留言，**文章已經同步上去了**，不用重按。",
              "但站上那篇有下面的狀況，請看一下要不要處理。", "",
              "| 代碼 | 發生什麼事 | 你該做的事 |", "| --- | --- | --- |"]
    for code, _key, why, todo, _keys in WARNINGS:
        lines.append(f"| **{code}** | {why} | {todo} |")
    lines += ["", "---", "",
              "這份表由 `scripts/error_codes.py` 產生，不要手動編輯——",
              "程式那邊改了，這裡沒跟著改，就會開始誤導人。"]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(to_markdown() if "--markdown" in sys.argv else to_js())
