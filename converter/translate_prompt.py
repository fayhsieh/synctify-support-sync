"""組出翻譯用的 prompt——Workflow 3 節點 4。

## 兩個來源，職責分開（Fay 2026-09-08 決定）

**術語 → 一律以詞彙表為準。** 樣本裡的術語**不可**當依據：老闆校對那兩篇的時間
早於術語表定案，Products 兩篇一律譯成「商品」12 次，而詞彙表定案是「产品」。
拿樣本學術語，模型會學到跟詞彙表打架的東西。

**語氣、句式、標籤處理 → 學樣本。** 這些寫不成規則。老闆的實際習慣是：
語序會重排（`in the top-right corner` → 「点击**右上角的**创建」）、專有名詞
保留原文並加括號（Anchor PO）、HTML 標籤結構原樣不動。規則寫得再細也不如給例子。

## 只放「這一段真的出現的」術語

詞彙表有 150+ 筆，全部塞進 prompt 有兩個壞處：稀釋注意力（真正相關的那兩三個被
淹沒），以及讓模型在原文沒有該詞時硬套。所以先掃出這一段出現的詞，只放那些。

## 比對用詞邊界，不是子字串

2026-08 在術語稽核時踩過：`Important Note` 命中 `Import`、`Retail Link` 命中
`Link`。純子字串比對必然誤判，一律加 `\\b`。

另外**長詞優先**：`Product SKU` 要比 `Product` 先命中，否則「Product SKU」會被
拆成「Product」+「SKU」兩條規則，模型收到互相矛盾的指示。

## 環境限制

跟其他 converter 模組一樣，打包進 n8n Python Code node 後只允許 `import re`。
不要引入其他標準庫。
"""
import re

# 譯文必須原樣保留的標籤。這些是站上樣式的一部分，改壞了版面就跑掉。
# direction_steps 是可點擊的 UI 路徑，custom_icon 是按鍵圖示。
_PROTECTED_HINT = "direction_steps／direction_step／custom_icon"


def _boundary_pattern(term):
    r"""為術語做出「詞邊界」比對式。

    ASCII 開頭／結尾才加 `\b`——中文沒有詞邊界的概念，對中文詞加 `\b` 會永遠
    比對不到（`\b` 定義在 `\w` 與非 `\w` 之間，中文字在 Python 的 `re` 裡算 `\w`，
    前後若也是中文就沒有邊界）。術語表的 English 欄位理論上都是英文，但
    Product SKU 這種混合詞、以及未來可能出現的中文詞條，保險起見兩端各自判斷。
    """
    esc = re.escape(term)
    left = r"\b" if term[:1].isascii() and term[:1].isalnum() else ""
    right = r"\b" if term[-1:].isascii() and term[-1:].isalnum() else ""
    return re.compile(left + esc + right, re.IGNORECASE)


def find_terms(text, glossary):
    """挑出這段文字裡真的出現的術語。回傳 [(英文, 譯文), ...]，長詞在前。

    glossary 是 [{"en": ..., "zh": ...}, ...]。zh 空白的直接跳過——沒有譯文的
    詞條放進 prompt 只會讓模型自由發揮，那正是我們要避免的。
    """
    hits = []
    taken = []          # 已命中的區間，用來擋住被長詞包住的短詞
    for entry in sorted(glossary, key=lambda e: -len(e.get("en") or "")):
        en, zh = (entry.get("en") or "").strip(), (entry.get("zh") or "").strip()
        if not en or not zh:
            continue
        for m in _boundary_pattern(en).finditer(text):
            # `Product SKU` 已命中的話，`Product` 不該再命中同一段文字，
            # 否則模型會收到兩條指向不同譯文的規則。
            if any(a <= m.start() < b or a < m.end() <= b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((en, zh))
            break       # 同一個詞出現多次只列一條
    return hits


def _fmt_samples(samples, limit=6):
    """把樣本排成 few-shot 例子。

    優先選「跟待譯內容形狀相近」的：有 HTML 標籤的段落配有標籤的例子。
    形狀不同的例子會讓模型誤以為輸出格式可以自由選擇。
    """
    out = []
    for s in samples[:limit]:
        out.append("原文：" + s["en"] + "\n譯文：" + s["zh_cn"])
    return "\n\n".join(out)


def pick_samples(block_html, samples, limit=6):
    """挑形狀相近的樣本。有標籤的配有標籤的，純文字的配純文字的。

    **會排除與待譯內容相同的樣本。** 這在評估時是關鍵：拿樣本本身當測試輸入時，
    它會同時出現在 few-shot 範例裡，模型直接抄答案，分數全是假的。
    2026-09-08 組第一版 prompt 時當場看到這個情況。

    生產環境雖然不會發生（要翻的是新內容），但把防線放在這裡而不是評估腳本裡，
    是因為評估腳本以後可能有好幾支，而這個陷阱只要漏掉一次就會得到假結論。
    """
    norm = re.sub(r"\s+", " ", block_html).strip()
    pool = [s for s in samples
            if re.sub(r"\s+", " ", s.get("en") or "").strip() != norm]
    has_tag = "<" in block_html
    same = [s for s in pool if ("<" in s["en"]) == has_tag]
    other = [s for s in pool if ("<" in s["en"]) != has_tag]
    return (same + other)[:limit]


def build_prompt(block_html, glossary, samples, target_name="簡體中文"):
    """組出完整的 prompt。回傳 (system, user)。

    刻意把「硬規則」放 system、「這一段的資料」放 user：規則對每一段都一樣，
    分開放讓它在多段翻譯時保持穩定，也方便之後做 prompt 快取。
    """
    terms = find_terms(block_html, glossary)

    system = (
        "你是 Synctify OMS 產品文件的專業翻譯。把英文技術文件翻成"
        + target_name + "。\n\n"
        "硬性規則（違反任何一條都算失敗）：\n"
        "1. **HTML 標籤原樣保留**——標籤名稱、屬性、巢狀結構、數量都不可更動，"
        "只翻譯標籤之間的文字。特別是 " + _PROTECTED_HINT + " 這些 class，"
        "它們是站上樣式的一部分。\n"
        "2. **術語一律使用下方給定的對照**，不可自行選用同義詞。"
        "沒有列出的詞才由你判斷。\n"
        "3. 產品專有名詞（如 Anchor PO）保留英文，必要時在後面加括號附原文。\n"
        "4. 只輸出譯文本身，不要加說明、不要加引號、不要重複原文。\n"
        "5. 原文沒有的內容不要自己補；原文有的不要省略。\n"
    )

    parts = []
    if terms:
        parts.append("必須使用的術語對照：\n"
                     + "\n".join("- " + en + " → " + zh for en, zh in terms))
    else:
        # 說明「這一段沒有受管術語」比留白好——留白時模型可能以為是漏給了
        parts.append("（這一段沒有出現術語表中的受管術語。）")

    picked = pick_samples(block_html, samples)
    if picked:
        parts.append("以下是本專案已審定的譯文範例，請比照其語氣與句式"
                     "（**只學語氣與結構，術語一律以上方對照為準**）：\n\n"
                     + _fmt_samples(picked))

    parts.append("請翻譯以下內容：\n\n" + block_html)
    return system, "\n\n---\n\n".join(parts)
