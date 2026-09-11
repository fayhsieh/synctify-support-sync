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

    # 容許英文複數。2026-09-09 踩到：詞彙表收「Tracking Number」（單數），
    # 原文寫「review the tracking numbers」，\b 在 Number 與 s 之間沒有邊界，
    # 整個術語就沒進 prompt——三個模型各自猜，看起來像模型不照規則翻，
    # 其實是根本沒收到規則。這種失敗**看起來像模型的問題**，很難察覺。
    #
    # 詞彙表裡 Channel 與 Channels 兩列並存，就是有人踩過同一個坑之後
    # 手動補的。150+ 個詞都這樣補不現實，所以修在比對這一層。
    #
    # 中文不受影響：中文沒有複數形，單複數共用同一個譯文。
    if right:
        esc = _stem_pattern(term)

    # 連字號要能對上空格。2026-09-09 踩到：詞彙表收「On-Hold」，原文句中寫
    # 「still in the frozen period... remain on hold」，連字號版對不上空格版，
    # 於是導覽路徑譯成「保留」（命中）、句中卻譯成「暂停状态」（沒命中、
    # 模型自己發揮）。同一篇文章兩種說法。
    esc = esc.replace(r"\-", r"[-\s]").replace("\\ ", r"[-\s]")
    return re.compile(left + esc + right, re.IGNORECASE)


def _stem_pattern(term):
    r"""單複數都要能命中，**兩個方向都要**。

    2026-09-09 的教訓：上一輪只做了「單數詞條 → 複數原文」（Tracking Number
    對上 tracking numbers），當時判斷反向罕見、不值得做。錯了——詞彙表收的是
    `Integrations`（複數），原文句中寫的是單數 `integration`，於是沒命中，
    模型自己猜成「集成」，而同一篇的導覽路徑因為寫複數而命中「平台对接」。
    **同一篇文章裡同一個詞兩種譯法**，比完全沒有術語表更糟。

    `ss` 結尾不剝（Address／Class／Process 不是複數）。
    """
    if term.endswith("ies") and len(term) > 4:
        return re.escape(term[:-3]) + "(?:y|ies)"
    if term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        return re.escape(term[:-1]) + "(?:e?s)?"
    if term.endswith("y") and not term.endswith(("ay", "ey", "oy", "uy")):
        return re.escape(term[:-1]) + "(?:y|ies)"
    return re.escape(term) + "(?:e?s)?"


# TP 渲染會把 & 寫成 &#038; 或 &amp;。post 7622 裡「Preview & Test」三種寫法都有，
# 不先還原的話詞條 `Preview & Test` 只命中其中一種。
_AMP_RE = re.compile(r"&(?:amp|#0*38);")

# 不是 -ed 結尾、但同樣「句中小寫就不是狀態」的詞。
_STATUS_WORDS = frozenset(("processing",))


def _label_only(term):
    """這個詞是否「大寫開頭才算術語，句中小寫不套用」。

    2026-09-10 post 7622 踩到：已確認的狀態標籤 Selected＝已选择、Resolved＝已解决，
    比對不分大小寫，於是「the selected context」譯成「已选择的上下文」（12 段）、
    「resolved to an unexpected value」譯成「已解决为意外值」。心柔在正式站 7889
    寫的是「所选」「映射／解析」。

    **不能**把「UI 標籤」類整批改成分大小寫：已確認的 141 筆幾乎全是 UI 標籤，
    Order（小寫出現 13 次）、Warehouse（12 次）、integration（見 _stem_pattern
    的教訓）小寫時都要套用。會出事的是過去分詞形的狀態詞——當標籤時是狀態名，
    放進句子就變回動詞或形容詞。所以只挑單字、-ed 結尾，加上 _STATUS_WORDS。
    """
    low = term.lower()
    return (" " not in term and "-" not in term and term[:1].isupper()
            and (low.endswith("ed") or low in _STATUS_WORDS))


def find_terms(text, glossary):
    """挑出這段文字裡真的出現的術語。回傳 [(英文, 譯文), ...]，長詞在前。

    glossary 是 [{"en": ..., "zh": ...}, ...]。zh 空白的直接跳過——沒有譯文的
    詞條放進 prompt 只會讓模型自由發揮，那正是我們要避免的。
    """
    text = _AMP_RE.sub("&", text or "")
    hits = []
    taken = []          # 已命中的區間，用來擋住被長詞包住的短詞
    for entry in sorted(glossary, key=lambda e: -len(e.get("en") or "")):
        en, zh = (entry.get("en") or "").strip(), (entry.get("zh") or "").strip()
        if not en or not zh:
            continue
        strict = _label_only(en)
        for m in _boundary_pattern(en).finditer(text):
            if strict and not m.group(0)[:1].isupper():
                continue        # 句中小寫的 selected／resolved 不是狀態標籤
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
        "## 硬性規則（違反任何一條都算失敗）\n"
        "1. **HTML 標籤原樣保留**——標籤名稱、屬性、巢狀結構、數量都不可更動，"
        "只翻譯標籤之間的文字。特別是 " + _PROTECTED_HINT + " 這些 class，"
        "它們是站上樣式的一部分。\n"
        "2. **術語一律使用下方給定的對照**，不可自行選用同義詞。"
        "沒有列出的詞才由你判斷。\n"
        "3. 只輸出譯文本身，不要加說明、不要加引號、不要重複原文。\n"
        "4. 原文沒有的內容不要自己補；原文有的不要省略。\n"
        "5. **非散文內容原樣輸出、不要翻譯**：iframe、script、錨點連結"
        "（如 #31-etsy）、URL、程式碼。\n"
        "6. **巢狀標籤之間不要留空白**。英文原文寫成 "
        "`<span A> <span B>Create</span> </span>`，那些空白是英文的詞距；"
        "中文不需要字距，留著會變成「点击 创建 按钮」。譯文請寫成 "
        "`<span A><span B>创建</span></span>`。標籤本身仍然一字不改。\n"
        # Fay 2026-09-10：post 7251 的譯文裡 UI 路徑前後有時留空格、有時不留
        # （「前往 <span>…」「前往：<span>…」「点击 <span>…</span>不会」），
        # 同一篇文章體例不一。規則 6 只管巢狀標籤之間，沒管中文與 UI 路徑之間。
        "7. **UI 路徑（direction_steps）前後各留一個半形空格**，與中文或英文隔開："
        "`点击 <span A><span B>创建</span></span> 按钮`。"
        "**緊鄰全形標點（，。：；、！？）的那一側不加空格**："
        "`前往：<span A>…</span></span>`、`点击 <span A>…</span></span>。`\n"
        # Fay 2026-09-10：FAQ 的「Yes.」同一篇裡譯成「是的。」與「是。」兩種。
        "8. **固定譯法**：句首的「Yes.」一律譯為「是的。」\n\n"
        # 以下規則來自 skill/SKILL.md（Support Article Writer），是已在實際
        # 寫作與翻譯中累積驗證過的房規。移植過來而不是重新發明，
        # 才不會讓自動翻譯跟人工翻譯長出兩種風格。
        #
        # 2026-09-10 拿掉 SKILL.md 的「已核可的英文 UI label 保持英文」。那條是寫
        # **英文**文件用的（不要改寫截圖上的字），搬進翻譯 prompt 後變成「沒進術語表
        # 的 UI label 就留英文」：post 7622 的 49 個 UI 路徑有 14 個留英文
        # （Override、Direction、Carriers…），而且同一個 Direction 翻 3 次、留 3 次。
        # 對照正式站 7889，心柔的做法是全部照譯（覆盖、方向、承运商）。
        "## 簡中在地化（承自 Support Article Writer 的規則）\n"
        "- 用自然的簡體中文表達。\n"
        "- **避免繁體中文句法與台灣用語**。\n"
        "- **UI label 一律翻成簡中**：術語對照有給的用對照，沒給的由你翻；"
        "不要因為它是按鈕或欄位名稱就保留英文。\n"
        "- 同一個術語在標題、步驟、FAQ 中必須一致。\n"
        "- 來源不明確時用保守用詞，**不要臆測產品行為**。\n\n"
        "## UI 用詞\n"
        "- **可見的 UI label 照字面翻，不可改寫、正規化或意譯**"
        "（例：Create Override → 创建覆盖，不要寫成「新建覆盖设置」）。\n"
        "- 「the selected …」這類句中形容詞譯為「所选…」，不要套用狀態標籤的「已选择」；"
        "code resolves to … 指代碼對應／解析到某個值，不是「已解决」。\n"
        "- 導覽路徑維持在同一段內、用 `>` 分隔（例：Integration > Connect）。\n"
        "- 圖示控制項沿用 emoji＋英文動作名的寫法（例：✏️(Edit)），不要翻譯"
        "括號裡的動作名，也不要自行猜測不熟悉的圖示含義。\n"
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
