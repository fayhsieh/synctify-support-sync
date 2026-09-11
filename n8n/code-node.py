# ══════════════════════════════════════════════════════════════════
#  自動產生，請勿直接編輯
#  來源：converter/notion_blocks.py + notion2elementor.py + translate_prompt.py + term_check.py
#  重新產生：./.venv/bin/python scripts/build_n8n_code_node.py [--target test]
#  修改請改 converter/*.py 並跑 pytest，再重新產生後貼回 n8n
#
#  ⚠️ 這份內容**依 target 而不同**（Post ID 欄位名兩站不一樣），
#     不能兩站共用同一份。要貼哪一站就用該 target 產生，
#     或直接匯入對應的 workflow JSON（裡面已經嵌好了）。
# ══════════════════════════════════════════════════════════════════

# ─── converter/notion_blocks.py ───
#!/usr/bin/env python3
"""
Notion API blocks → Notion-flavored Markdown

把 Notion API `/blocks/{id}/children` 回傳的 block JSON 轉成
`notion2elementor.py` 的 parser 吃得下的 markdown。

這一層與轉換器是綁定的：產出的 markdown 必須精確吻合 parse_blocks() 的預期，
否則會出現「數字清單斷編號」「表格沒被辨識」這類問題。關鍵約束：

  * 連續數字清單項目之間**不可有空行**（有空行會被切成多個 widget，編號重來）
  * 數字清單項目的子內容必須以 **tab 縮排**且**非空行**
  * 表格第一列後必須緊接 `| --- |` 分隔列，中間不可有空行
  * callout 用 `<callout icon="..." color="...">` … `</callout>`，body 以 tab 縮排

輸入可以是巢狀（block 帶 `children`）或扁平（n8n Notion 節點的
「Also Fetch Nested Blocks」會回扁平清單，本模組用 `parent.block_id` 重建樹）。
"""
import re

# Notion 標題層級 → markdown 井字號數，1:1 對應（heading_2 → `##`、heading_4 → `####`）。
# 依真實 block 資料確認（2026-08-02）：文章主章節為 heading_2、FAQ 問題為 heading_4，
# 與 Notion 匯出的 markdown 完全吻合。
#
# 轉換器的 parse_blocks 只認得 `##`～`####`，故層級夾在 [2, 4]：
# heading_1 併入 `##`（同為最上層章節），heading_5/6 併入 `####`，
# 避免產生 `#` 或 `#####` 被當成一般段落而靜默漏掉內容。
MIN_HEADING, MAX_HEADING = 2, 4

# 不同步的段落（CLAUDE.md）：內部審核筆記、SEO Meta、Version History
_SEO_META_PATTERN = re.compile(r"^\s*\**\s*SEO\s*Meta\s*\**\s*$", re.I)
_SKIP_SECTION_PATTERNS = [
    _SEO_META_PATTERN,
    re.compile(r"^\s*Version\s+History\s*$", re.I),
    re.compile(r"Content\s+Review\s+Notes", re.I),
]

# SEO Meta 段不進正文，但要擷取出來寫進 AIOSEO（POST /synctify/v1/seo/{id}）。
# Notion 的寫法是 quote block 內「粗體標籤＋軟換行＋內容」，兩者在同一個
# rich_text 陣列裡，所以純文字會長成：
#     "Title\nNew Order Frozen Period - Synctify Support Center"
# 標籤大小寫、結尾冒號都容忍。
# 寫作端的圖片佔位鷹架：callout 首行為「Image Placeholder」，內含該圖應有的
# 檔名與 caption/alt。那是給寫作者自己看的，絕不可同步到公開站
# （2026-08-11 Fay 決定，比照 Content Review Notes）。
# 它同時也是編號斷掉的元兇——巢狀在步驟底下時會把連續編號切斷。
_IMAGE_PLACEHOLDER = re.compile(r"^\s*\**\s*Image\s*Placeholder\s*\**\s*$", re.I)

_SEO_LABELS = {
    "title": "title",
    "seo title": "title",
    "meta description": "description",
    "description": "description",
}


# ---------- rich text → 行內 markdown ----------

def _rt_items(data):
    """取出 block 的 rich text 陣列。

    Notion API 原生用 `rich_text`；n8n Notion 節點若開啟 Simplify Output 會改用 `text`。
    兩者都接受，避免因上游設定差異導致所有文字靜默變成空字串。
    """
    if not data:
        return []
    return data.get("rich_text") or data.get("text") or []


def rich_text(items):
    """Notion rich_text 陣列 → 行內 markdown（順序：code → bold → italic → link）"""
    out = []
    for t in items or []:
        try:
            s = t.get("plain_text") or (t.get("text") or {}).get("content") or ""
        except AttributeError:
            out.append(str(t))           # Simplify 模式可能直接給字串
            continue
        if not s:
            continue
        a = t.get("annotations") or {}
        if a.get("code"):
            s = f"`{s}`"
        if a.get("bold"):
            s = f"**{s}**"
        if a.get("italic"):
            s = f"*{s}*"
        href = t.get("href") or _mention_href(t)
        if href:
            s = f"[{s}]({href})"
        out.append(s)
    return "".join(out)


def plain_text(items):
    """Notion rich_text 陣列 → **純文字**，不帶任何行內標記。

    給「最終會進純文字欄位」的東西用：圖片的 caption／alt 會寫進 WP 媒體庫的
    Caption（post_excerpt）與 Alt text（post meta），那兩個欄位不解析 markdown。
    走 rich_text() 的話，Notion 上的粗體圖說會在站上顯示成字面的 `**粗體**`
    （2026-08-25 正式站 5601 實際踩到）。
    """
    out = []
    for t in items or []:
        try:
            out.append(t.get("plain_text") or (t.get("text") or {}).get("content") or "")
        except AttributeError:
            out.append(str(t))
    return "".join(out)


def _mention_href(item):
    """頁面提及 → Notion 網址。

    寫作端引用其他文章時最常用的就是 `@頁面` 提及。它在 rich_text 裡是
    type=mention，`href` **不保證存在**——沒有 href 就產不出連結，後面的
    「換成 WP 永久連結」也就無從發生。故從 mention.page.id 自己組出來。
    """
    try:
        if item.get("type") != "mention":
            return ""
        mention = item.get("mention") or {}
        if mention.get("type") != "page":
            return ""
        page_id = (mention.get("page") or {}).get("id") or ""
    except AttributeError:
        return ""
    return f"https://www.notion.so/{page_id.replace('-', '')}" if page_id else ""


def _plain(items):
    out = []
    for t in items or []:
        try:
            out.append(t.get("plain_text") or (t.get("text") or {}).get("content") or "")
        except AttributeError:
            out.append(str(t))
    return "".join(out)


# Notion 的 code block 語言 → 站上 docly_code_syntax_highlighter 的 lng_type。
# 兩個理由必須正規化：
#   1. Notion 有些語言名帶空格（"plain text"、"shell script"），會干擾 fence 解析。
#   2. lng_type 直接決定 Prism 用哪套文法上色。"plain text" 若對到 markdown，
#      Prism 的 markdown 文法會把裸網址變成可點連結——實站的純文字區塊要求不可點
#      （正式站同類區塊用 http，渲染為 <span class="token">，上色但不可點）。
#      故對到 Prism 的中性語言 plaintext：不 tokenize、不產生連結。
_CODE_LANG_MAP = {
    "plain text": "plaintext",
    "text": "plaintext",
    "none": "plaintext",
}


def _code_language(lang):
    lang = (lang or "").strip().lower()
    if lang in _CODE_LANG_MAP:
        return _CODE_LANG_MAP[lang]
    return lang.replace(" ", "") or "markdown"


# Notion API 不提供圖片 alt text（只有 caption），因此寫作端以標記把兩段文字
# 放進同一個圖說：`可見圖說 [alt: 無障礙描述]`。
# 沒有標記時 alt 與 caption 同值，行為與舊文章一致（向下相容）。
_ALT_MARKER = re.compile(r"^(.*?)\s*\[alt:\s*(.*?)\]\s*$", re.S | re.I)


def split_caption_alt(text):
    """圖說 → (可見 caption, alt text)。無標記時兩者相同。"""
    text = (text or "").strip()
    m = _ALT_MARKER.match(text)
    if not m:
        return text, text
    caption = m.group(1).strip()
    alt = m.group(2).strip()
    return caption, (alt or caption)


def _capture_seo(data, report):
    """SEO Meta 段裡的一個 quote／paragraph → report["seo"]。無法辨識的行忽略。

    標籤與內容的分隔在 Notion 是軟換行（plain_text 裡的 \\n）。若寫作者是手打
    `<br>` 而非按 Shift+Enter，純文字裡就會是字面的 <br>——兩種都接受。
    """
    raw = _plain(_rt_items(data))
    sep = "\n" if "\n" in raw else ("<br>" if "<br>" in raw.lower() else None)
    if not sep:
        return
    if sep == "<br>":
        idx = raw.lower().index("<br>")
        label, value = raw[:idx], raw[idx + 4:]
    else:
        label, _, value = raw.partition("\n")
    key = _SEO_LABELS.get(label.strip().rstrip(":：").lower())
    if key and value.strip():
        report["seo"][key] = value.strip()


def _heading_level(btype):
    """heading_N → markdown 井字號數（夾在轉換器認得的 [2, 4]）；非標題回 None。"""
    if not btype or not btype.startswith("heading_"):
        return None
    suffix = btype[len("heading_"):]
    if not suffix.isdigit():
        return None
    return max(MIN_HEADING, min(MAX_HEADING, int(suffix)))


# ---------- 扁平清單 → 樹 ----------

def build_tree(blocks, root_id=None):
    """把（可能扁平的）block 清單重建成巢狀樹。

    已經帶 `children` 的 block 原樣保留；扁平清單則依 `parent.block_id` 掛回父節點，
    同一父節點下維持原本的陣列順序。
    """
    by_id = {}
    for b in blocks:
        if b.get("id"):
            by_id[b["id"]] = b

    # 已是巢狀結構（有 children 且沒有孤兒）→ 直接用
    if any(b.get("children") for b in blocks):
        return [b for b in blocks if _parent_id(b) not in by_id]

    for b in blocks:
        b.setdefault("children", [])
    roots = []
    for b in blocks:
        pid = _parent_id(b)
        if pid and pid in by_id and by_id[pid] is not b:
            by_id[pid]["children"].append(b)
        else:
            roots.append(b)
    return roots


def _parent_id(b):
    p = b.get("parent") or {}
    return p.get("block_id") if p.get("type") == "block_id" else None


# ---------- 主轉換 ----------

def blocks_to_markdown(blocks, root_id=None):
    """回傳 (markdown, report)。report 記錄被剔除與不支援的區塊。"""
    report = {"skipped_sections": [], "unsupported": [], "excluded_toggles": 0,
              "excluded_placeholders": 0, "seo": {}}
    tree = build_tree(list(blocks), root_id)
    lines = []
    _render(tree, lines, report, indent=0)
    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md, report


def _render(blocks, lines, report, indent):
    """把 block 串列渲染成行。indent 為 tab 數（數字清單子內容用）。"""
    tab = "\t" * indent
    skip_until_heading_level = None
    capture_seo = False          # 目前跳過的是不是 SEO Meta 段（要邊跳過邊擷取）

    for b in blocks:
        btype = b.get("type")
        data = b.get(btype) or {}
        children = b.get("children") or []

        # ── 段落級剔除：遇到 SEO Meta / Version History / Review Notes 起跳過，
        #    直到出現同級或更高級的標題為止
        heading_level = _heading_level(btype)

        if skip_until_heading_level is not None:
            if heading_level is not None:
                if heading_level <= skip_until_heading_level:
                    skip_until_heading_level = None
                    capture_seo = False
                else:
                    continue
            else:
                if capture_seo and btype in ("quote", "paragraph"):
                    _capture_seo(data, report)
                continue

        text = _plain(_rt_items(data))
        if (heading_level is not None or btype == "paragraph") and text:
            hit = None
            for p in _SKIP_SECTION_PATTERNS:
                if p.search(text):
                    hit = p
                    break
            if hit is not None:
                skip_until_heading_level = heading_level if heading_level is not None else 9
                # SEO Meta 段照樣不進正文，但底下的 quote 要撈出來給 AIOSEO
                capture_seo = (hit is _SEO_META_PATTERN)
                report["skipped_sections"].append(text.strip()[:60])
                continue

        # ── 各 block 類型
        if heading_level is not None:
            _blank(lines)
            lines.append(f"{'#' * heading_level} {rich_text(_rt_items(data))}")

        elif btype == "paragraph":
            body = rich_text(_rt_items(data))
            if body.strip():
                if indent:
                    lines.append(f"{tab}{body}")   # 數字清單下的接續說明
                else:
                    _blank(lines)
                    lines.append(body)
            _render(children, lines, report, indent)

        elif btype == "bulleted_list_item":
            lines.append(f"{tab}- {rich_text(_rt_items(data))}")
            # 項目符號的子項再縮一層（轉換器用 tab 數判斷巢狀層級）
            _render(children, lines, report, indent + 1)

        elif btype == "to_do":
            lines.append(f"{tab}- {rich_text(_rt_items(data))}")
            _render(children, lines, report, indent + 1)

        elif btype == "numbered_list_item":
            # 連續編號之間不可有空行；子內容以 tab 縮排接在後面
            lines.append(f"{tab}1. {rich_text(_rt_items(data))}")
            _render(children, lines, report, indent + 1)

        elif btype == "code":
            _blank(lines)
            lang = _code_language(data.get("language"))
            lines.append(f"```{lang}")
            lines.extend(_plain(_rt_items(data)).split("\n"))
            lines.append("```")

        elif btype == "image":
            url = (data.get("file") or {}).get("url") or (data.get("external") or {}).get("url", "")
            # 圖說走 plain_text 而非 rich_text：它最終進的是純文字欄位，
            # 帶 markdown 的話粗體會在站上顯示成字面的 **粗體**。
            caption, alt = split_caption_alt(plain_text(data.get("caption")))
            # 用 markdown 的 title 欄位（引號那格）帶 alt text：![可見圖說](url "alt")
            # 兩者相同時省略，維持與舊輸出一致
            suffix = f' "{alt}"' if alt and alt != caption else ""
            if indent:
                lines.append(f"{tab}![{caption}]({url}{suffix})")   # 巢狀 → [caption] shortcode
            else:
                _blank(lines)
                lines.append(f"![{caption}]({url}{suffix})")

        elif btype == "callout":
            # 圖片佔位鷹架整個剔除。標記可能寫在 callout 自身，也可能是第一個子區塊，
            # 兩處都認——寫作端兩種寫法都出現過。
            _own = _plain(_rt_items(data))
            _first_child = ""
            for _c in children:
                _ct = _c.get("type")
                _first_child = _plain(_rt_items(_c.get(_ct) or {}))
                break
            if _IMAGE_PLACEHOLDER.match(_own) or _IMAGE_PLACEHOLDER.match(_first_child):
                report["excluded_placeholders"] += 1
                continue
            icon = data.get("icon") or {}
            icon_s = (icon.get("emoji")
                      or (icon.get("external") or {}).get("url")
                      or (icon.get("file") or {}).get("url") or "")
            color = data.get("color") or ""
            body_lines = []
            _render(children, body_lines, report, 0)
            first = rich_text(_rt_items(data))
            _blank(lines)
            lines.append(f'<callout icon="{icon_s}" color="{color}">')
            if first.strip():
                lines.append(f"\t{first}")
            for bl in body_lines:
                if bl.strip():
                    lines.append(f"\t{bl}")
            lines.append("</callout>")

        elif btype == "table":
            _blank(lines)
            lines.extend(_table(b, data))

        elif btype == "divider":
            _blank(lines)
            lines.append("---")

        elif btype == "quote":
            _blank(lines)
            lines.append(rich_text(_rt_items(data)))
            _render(children, lines, report, indent)

        elif btype == "toggle":
            # 內部審核筆記慣例用 toggle；一律不同步（CLAUDE.md）
            report["excluded_toggles"] += 1

        elif btype in ("table_row", "child_page", "child_database", "breadcrumb",
                       "table_of_contents", "column_list", "column", "synced_block"):
            if btype in ("column_list", "column", "synced_block"):
                _render(children, lines, report, indent)   # 容器類：直接展開內容

        else:
            report["unsupported"].append(btype)


def _table(block, data):
    """Notion table → markdown pipe 表格（第一列當表頭，緊接分隔列，中間不可空行）"""
    rows = [c for c in (block.get("children") or []) if c.get("type") == "table_row"]
    if not rows:
        return []
    def cells(r):
        return [rich_text(c).replace("|", "\\|").replace("\n", " ")
                for c in (r.get("table_row") or {}).get("cells", [])]
    out = []
    header = cells(rows[0])
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in header) + " |")
    for r in rows[1:]:
        out.append("| " + " | ".join(cells(r)) + " |")
    return out


def _blank(lines):
    """在區塊之間插入一個空行（避免與前一區塊黏連），但不製造連續空行。"""
    if lines and lines[-1].strip():
        lines.append("")


# ---------- 版本標記（母列的 Current 標示自動維護）----------
#
# 每次發佈新版本後，人工要改四個地方，老闆與小編常忘記（Fay 2026-08-11）：
#   1. 版本子列篇名的 ` (Current)` 後綴
#   2. 母列 Overview 的 `- Current Version: vN (Month Year)`
#   3. 母列 Version History 裡 `### **vN – Month Year (Current)**` 的標記
#   4. 母列的 Version 屬性
# 這裡負責算出 1～3 要改成什麼；4 是單純的屬性寫入，由 workflow 直接處理。
#
# 格式取自實際母列（5-5 Shipment Routing，2026-08-11 讀出）。破折號是 en dash `–`，
# 標題整段帶粗體——改寫時要保留，否則排版會壞掉。

_CURRENT_SUFFIX = " (Current)"
_VER_TOKEN = re.compile(r"^\s*v(\d+)", re.I)
# `v3 – May 2026 (Current)` → 版本、日期、既有標記
_VH_HEADING = re.compile(
    r"^\s*v(\d+)\s*[–—-]\s*(.*?)\s*(?:\(\s*current\s*\))?\s*$", re.I | re.S)
_OVERVIEW_LINE = re.compile(
    r"^(\s*Current\s+Version\s*:\s*)(.*?)$", re.I | re.S)


def short_version(label):
    """`v1 (Initial Version)` → `v1`；已是短格式則原樣回傳。"""
    m = _VER_TOKEN.match(label or "")
    return f"v{m.group(1)}" if m else (label or "").strip()


def _same_version(a, b):
    return short_version(a).lower() == short_version(b).lower()


def _restyle(items, text):
    """用原本第一段的樣式包裝新文字，保留粗體／斜體等標註。"""
    ann = {}
    for t in items or []:
        try:
            ann = t.get("annotations") or {}
        except AttributeError:
            ann = {}
        break
    out = {"type": "text", "text": {"content": text}}
    if ann:
        out["annotations"] = ann
    return [out]


def strip_current(title):
    """去掉篇名結尾的 ` (Current)`（容忍大小寫與多餘空白）。"""
    return re.sub(r"\s*\(\s*current\s*\)\s*$", "", title or "", flags=re.I).rstrip()


def plan_version_marks(rows, blocks, version):
    """算出「vN 成為現行版本」後要做的改動。

    rows   —— 母列底下的版本子列 [{"id":…, "title":…, "version":…}]
    blocks —— 母列頁面的區塊（Notion API 原生格式）
    version—— 剛發佈的版本標籤（`v3` 或 `v3 (Initial Version)` 皆可）

    回傳 {"row_renames": [...], "block_updates": [...]}，兩者都只包含**真的需要
    改動**的項目——沒有變化就不送 API，避免在 Notion 的編輯紀錄裡刷出無意義的版本。
    """
    target = short_version(version)
    renames, updates = [], []

    # ① 子列篇名：目標版本加上 (Current)，其餘拿掉
    for r in rows or []:
        title = r.get("title") or ""
        base = strip_current(title)
        want = base + _CURRENT_SUFFIX if _same_version(r.get("version") or base, target) else base
        if want != title:
            renames.append({"id": r.get("id"), "title": want})

    # 先掃一遍 Version History，取得目標版本的日期字串，供 Overview 沿用
    target_date = ""
    for b in blocks or []:
        if (b.get("type") or "") .startswith("heading_"):
            m = _VH_HEADING.match(_plain(_rt_items(b.get(b["type"]) or {})))
            if m and _same_version("v" + m.group(1), target):
                target_date = (m.group(2) or "").strip()
                break

    for b in blocks or []:
        btype = b.get("type") or ""
        data = b.get(btype) or {}
        items = _rt_items(data)
        text = _plain(items)

        # ② Overview 的 `Current Version: …`
        if btype in ("bulleted_list_item", "paragraph", "numbered_list_item"):
            m = _OVERVIEW_LINE.match(text)
            if m:
                tail = f"{target} ({target_date})" if target_date else target
                new = m.group(1) + tail
                if new != text:
                    updates.append({"id": b.get("id"), "type": btype,
                                    "rich_text": _restyle(items, new)})
                continue

        # ③ Version History 標題的 (Current) 標記
        if btype.startswith("heading_"):
            m = _VH_HEADING.match(text)
            if not m:
                continue
            base = f"v{m.group(1)}" + (f" – {m.group(2).strip()}" if m.group(2).strip() else "")
            new = base + _CURRENT_SUFFIX if _same_version("v" + m.group(1), target) else base
            if new != text:
                updates.append({"id": b.get("id"), "type": btype,
                                "rich_text": _restyle(items, new)})

    return {"row_renames": renames, "block_updates": updates}


# ---------- Notion 內部連結 → WP 永久連結 ----------
#
# 寫作端（GPT Skill）引用其他文章時會優先貼 Notion 連結，那個網址對外是私有的，
# 直接同步等於在公開站上放一個讀者打不開的連結（2026-08-11 Fay 回報，6086 文末的
# 「see Reports Center」）。
#
# Content Hub 的母列存著 WP Post ID，站上又查得到每篇的永久連結，兩邊一併就能反查。
# WP 的網址含分類路徑（/docs/synctify-documentation/reports/reports-center/），
# 拼不出來，只能從站上取。

def normalize_notion_id(value):
    return (value or "").replace("-", "").strip().lower()


# WP 的 title.rendered 是 HTML 實體編碼過的（`Add &#038; Edit Categories`）。
# n8n 沙箱只給 re，沒有 html 模組，故自己解最常見的幾種。
_ENTITY_NUM = re.compile(r"&#(\d+);")
_ENTITY_NAMED = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&nbsp;": " "}


def unescape_wp_title(text):
    out = _ENTITY_NUM.sub(lambda m: chr(int(m.group(1))), text or "")
    for ent, ch in _ENTITY_NAMED.items():
        out = out.replace(ent, ch)
    return out.strip()


def build_link_map(hub_rows, wp_docs, post_id_prop="WP Post ID"):
    """{Notion 頁面 id: {"url", "title", "doc_name"}}。

    hub_rows —— Content Hub 的查詢結果（Notion API 原生格式）
    wp_docs  —— WP 的 docs 清單，每筆需有 id、link、title

    `doc_name` 是給連結文字判斷用的：Notion 的頁面提及（mention）會把被提及頁面的
    Doc name 當成顯示文字，那個名稱帶編號前綴（`7-1 Reports Center`），站上並沒有。
    連結文字若正好等於 Doc name，就代表它是提及而非作者自訂的字，可以換成 WP 標題。

    WP Post ID 只記在母列，但寫作者可能連到版本子列，所以子列沿用母列的資料。

    post_id_prop —— 兩站並行時各自有欄位（測試站是「WP Post ID (Test)」），
    讀錯的話連結會對到另一站的文章 ID，永久連結就全部對不上。
    """
    wp = {}
    for d in wp_docs or []:
        if d.get("id") and d.get("link"):
            title = d.get("title") or {}
            wp[str(d["id"])] = {
                "url": d["link"],
                "title": unescape_wp_title(title.get("rendered") if isinstance(title, dict) else title),
            }

    direct, doc_names, parent_of = {}, {}, {}
    for r in hub_rows or []:
        rid = normalize_notion_id(r.get("id"))
        if not rid:
            continue
        props = r.get("properties") or {}
        tt = (props.get("Doc name") or {}).get("title") or []
        doc_names[rid] = (tt[0].get("plain_text") or "").strip() if tt else ""
        rt = (props.get(post_id_prop) or {}).get("rich_text") or []
        post_id = (rt[0].get("plain_text") or "").strip() if rt else ""
        if post_id and post_id in wp:
            direct[rid] = dict(wp[post_id])
        rel = (props.get("Parent item") or {}).get("relation") or []
        if rel:
            parent_of[rid] = normalize_notion_id(rel[0].get("id"))

    out = {k: dict(v) for k, v in direct.items()}
    for rid, parent in parent_of.items():
        if rid not in out and parent in direct:
            out[rid] = dict(direct[parent])
    for rid, entry in out.items():
        entry["doc_name"] = doc_names.get(rid, "")
    return out

# ─── converter/notion2elementor.py ───
#!/usr/bin/env python3
"""
Notion → Elementor 轉換器原型 v0.1
Synctify Support Center 自動上稿流程

輸入：Notion 頁面內容（Notion-flavored Markdown）
輸出：
  1. 可匯入的 Elementor template JSON
  2. faq-items.json（Arconix FAQ 待寫入項目）
  3. conversion-report.json（轉換摘要、待上傳圖片、SEO meta）

映射規則 v1（見 Marketing Wiki 設計文件）
"""
# 核心轉換（convert 及其相依）刻意只用 `re`，不 import 其他模組——
# n8n v2 的 Python task runner 預設封鎖所有 import，相依愈少愈容易通過
# allowlist（只需 N8N_RUNNERS_STDLIB_ALLOW=re）。json／sys 只在 CLI 區塊使用，
# datetime 僅在未給 sync_date 時延遲載入。


# ---------- 工具 ----------

_eid_counter = 0


def eid():
    """Elementor 元素 ID：7 位十六進位。

    以計數器＋固定散列產生，不用 secrets（避免相依 os.urandom）。
    同一份輸入會得到相同 ID，輸出因此可重現、可 diff。
    """
    global _eid_counter
    _eid_counter += 1
    return f"{(_eid_counter * 0x9E3779B1) & 0xFFFFFFF:07x}"

def widget(widget_type, settings):
    return {
        "id": eid(),
        "settings": settings,
        "elements": [],
        "isInner": False,
        "widgetType": widget_type,
        "elType": "widget",
    }

def container(elements):
    return {
        "id": eid(),
        "settings": {"flex_direction": "column"},
        "elements": elements,
        "isInner": False,
        "elType": "container",
    }

# ---------- Icon button 對照表（emoji → [custom_icon] shortcode class）----------
# 2026-07-16 定案：統一輸出 [custom_icon class="..."] shortcode
# （站上的 custom_icon shortcode 會渲染成 <kbd><i class="ti ti-{class}"></i></kbd>，
#   含按鍵樣式外框；原生 <i> 寫法缺外框，屬歷史不一致，不再使用）
ICON_MAP = {
    "✏️": "pencil",
    "⚙️": "settings",
    "⬇️": "chevron-down",
    "⏬": "chevrons-down",
    "🎛️": "adjustments-alt",  # 調整庫存（Notion 端 emoji 待 Fay 最終確認）
    # ⋮ 是 U+22EE VERTICAL ELLIPSIS，不是 emoji 而是一般標點字元。
    # 這裡的比對只看 startswith，沒有「必須是 emoji」的閘門，所以照樣生效。
    "⋮": "dots-vertical",     # More Actions（列表列尾的直式三點選單）
}

# ---------- Notion 內部連結解析 ----------
# 寫作端引用其他文章時會貼 Notion 連結，那對讀者是打不開的私有網址。
# convert() 收到 link_map 時就地換成 WP 永久連結；換不掉的記進報告，
# 不靜默放行也不擅自刪掉連結——那會讓寫作者不知道哪裡要修。
# 網址解析刻意放在這一側：轉換器必須能單獨執行、只依賴 re（n8n 沙箱的限制），
# 不可跨模組取用 notion_blocks 的函式。
_NOTION_HOST = re.compile(r"^https?://(?:[\w-]+\.)*notion\.(?:so|com)/", re.I)
# 32 位十六進位，中間的連字號可有可無（兩種寫法 Notion 都會產出）
_NOTION_ID = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")


def notion_page_id_from_url(url):
    """Notion 頁面連結 → 32 位頁面 id；非 Notion 連結回空字串。

    接受三種形式：
      1. 絕對網址 `https://www.notion.so/...` / `https://app.notion.com/p/...`
      2. **相對路徑** `/3272f2ed…` —— Notion API 對頁面提及給的 href 可能長這樣，
         漏掉的話會被當成外部連結直接放行（2026-08-11 實測踩到）
      3. 裸 id（32 位十六進位）

    網址可能帶標題 slug（`.../Reports-Center-3272f2ed…`）或查詢字串（`?pvs=4`），
    故取**最後一個**符合的 id——slug 裡的英文字不會誤判成十六進位。
    """
    url = (url or "").strip()
    if not url:
        return ""
    is_notion = bool(_NOTION_HOST.match(url))
    if not is_notion:
        # 相對路徑或裸 id：不可以是其他網站的絕對網址
        if "://" in url:
            return ""
        is_notion = url.startswith("/") or bool(re.fullmatch(
            r"[0-9a-fA-F-]{32,36}", url))
    if not is_notion:
        return ""
    found = _NOTION_ID.findall(url)
    return found[-1].replace("-", "").lower() if found else ""


_LINK_MAP = {}
_UNRESOLVED_LINKS = []


def _resolve_link(url, label):
    """回傳 (網址, 連結文字)。不是 Notion 連結或查不到對應時原樣回傳。

    ⚠️ 這兩個 global 宣告不可省。打包進 n8n Code node 時，整份程式會被包在一個
    函式裡（因為頂層有 return），「模組層級」的 _LINK_MAP 於是變成外層函式的區域
    變數。convert() 宣告了 global 所以寫進真正的全域，這裡若不宣告就會沿閉包讀到
    外層那份空的——查得到卻換不掉，而且完全不報錯（2026-08-11 實測踩到）。
    _eid_counter 一直沒事，正是因為它的讀寫兩端都有宣告。
    """
    global _LINK_MAP, _UNRESOLVED_LINKS
    page_id = notion_page_id_from_url(url)
    if not page_id:
        return url, label               # 不是 Notion 連結，完全不動
    entry = _LINK_MAP.get(page_id)
    if not entry:
        if url not in _UNRESOLVED_LINKS:
            _UNRESOLVED_LINKS.append(url)
        return url, label
    # Notion 的頁面提及會把 Doc name 當顯示文字，那個名稱帶編號前綴（`7-1 …`），
    # 站上沒有。文字等於 Doc name ＝ 它是提及而非作者自訂的字，換成 WP 標題；
    # 作者自己打的連結文字（「see the reports guide」）則保留不動。
    text = label
    if entry.get("title") and label.strip() == (entry.get("doc_name") or "").strip():
        text = entry["title"]
    return entry["url"], text


# ---------- 行內格式轉換 ----------

# Notion 會夾帶的協作標記。三種都是「留言／協作」留下的痕跡，
# 一律拆掉外層 span、保留裡面的文字（見 docs/mapping-rules.md §四之三）。
#
# ⚠️ 漏掉任何一種的後果是它會出現在**公開頁面**上。2026-08-14 掃描測試站 36 篇
# 已發佈文章，7 篇帶著 notion-enable-hover 或 notionvc 註解——那些是人工上稿的
# 舊文，但也證明少列一種就會漏出去。
_NOTION_SPAN_MARKERS = (
    r'discussion-urls\s*=',        # 留言錨點
    r'class="[^"]*notion-enable-hover',  # 懸浮提示包裹（曾漏掉）
)


def _unwrap_span(text, marker):
    """拆掉符合 marker 的 <span>，保留內容。

    不能用 `<span …>(.*?)</span>` ——非貪婪比對遇到巢狀 span 會停在**內層**的
    收尾標籤，把外層的 `</span>` 留在原文裡。這裡改用深度計數找對應的收尾。
    """
    open_re = re.compile(r"<span\b[^>]*?%s[^>]*>" % marker, re.I)
    while True:
        m = open_re.search(text)
        if not m:
            return text
        depth, close = 1, None
        for t in re.finditer(r"<(/?)span\b[^>]*>", text[m.end():]):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                close = (m.end() + t.start(), m.end() + t.end())
                break
        if close is None:      # 沒有對應收尾（來源殘缺）——只拆開始標籤，避免無限迴圈
            text = text[:m.start()] + text[m.end():]
            continue
        text = text[:m.start()] + text[m.end():close[0]] + text[close[1]:]


def esc_attr(text):
    r"""把文字轉成可安全放進 HTML 屬性的形式。

    **`>` 一定要編碼**，原因不是我們自己的程式，是 WordPress 核心：
    `img_caption_shortcode()` 用 `#((?:<a [^>]+>\s*)?<img [^>]+>(?:\s*</a>)?)(.*)#is`
    抓圖片與圖說，而 `<img [^>]+>` 的 `[^>]+` **在第一個 `>` 就停住**。alt 裡只要
    有一個 `>`（UI 路徑很常見，例如「Settings > Organization > Members」），
    img 標籤就會被從中切斷，後半段變成圖說文字印在畫面上。
    2026-09-01 在正式站 5620 實際發生。

    只用 str.replace——這份要打包進 n8n 的 Code node，那裡只允許 import re。
    `&` 必須第一個處理，否則會把後面產生的實體再次編碼。
    """
    return (str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def unesc_attr(text):
    """esc_attr 的反向，給要顯示給人看的報告用（順序與 esc_attr 相反）。"""
    return (str(text or "")
            .replace("&quot;", '"')
            .replace("&gt;", ">")
            .replace("&lt;", "<")
            .replace("&amp;", "&"))


def strip_notion_artifacts(text):
    """剔除 Notion 留言標記與雜訊"""
    for marker in _NOTION_SPAN_MARKERS:
        text = _unwrap_span(text, marker)
    # 註解內容不含 `>`，但用 .*? 比 [^>]* 保險
    text = re.sub(r"<!--\s*notionvc:.*?-->", "", text, flags=re.S)
    return text

_INLINE_MD = [
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),   # [文字](網址) → 文字
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),      # **粗體**
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.S), r"\1"),  # *斜體*
    (re.compile(r"`([^`]*)`"), r"\1"),               # `code`
]


def strip_inline_md(text):
    """去掉行內 markdown 標記，保留文字。

    給「已經是 markdown 字串、但要送進純文字欄位」的東西用（例如 FAQ 題目最終
    是 WP 的文章標題）。只脫成對的標記——單獨的 * 或 ` 原樣保留，避免把
    「What is 2*3?」這種內容改壞。
    """
    s = text or ""
    for pat, rep in _INLINE_MD:
        s = pat.sub(rep, s)
    return s


def inline_md_to_html(text):
    """行內 Markdown → HTML。
    規則順序很重要：先處理 inline code（→ [direction]），再處理連結、粗體、斜體。
    """
    # inline code 處理（Style Guide 5.1）：
    # - 內容以 icon emoji 開頭（寫作慣例：`⏬ (Expand)`）→ [custom_icon] shortcode＋標籤純文字
    # - 其他 → [direction] shortcode（可點擊 UI 路徑）
    def _code(m):
        content = m.group(1).strip()
        for emoji, cls in ICON_MAP.items():
            if content.startswith(emoji):
                rest = content[len(emoji):].strip()
                sc = f'[custom_icon class="{cls}"]'
                return f"{sc} {rest}" if rest else sc
        # 路徑分隔符 `>` 編碼成 &gt;，否則 Docly 的 [direction] shortcode 會把原始
        # `>` 渲染成箭頭圖示；站上要顯示字面 `>`（如 Integrations > Integrated Message Codes）
        return f"[direction]{content.replace('>', '&gt;')}[/direction]"
    text = re.sub(r"`([^`]+)`", _code, text)
    # 連結：對齊站上慣例——連結文字不保留粗體，一律新分頁開啟
    def _link(m):
        label, url = m.group(1), m.group(2)
        url, label = _resolve_link(url, label)
        label = re.sub(r"\*\*(.+?)\*\*", r"\1", label)  # 去除粗體
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, text)
    # 粗體（不可點擊 UI 文字，Style Guide 5.2）
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # 斜體
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    # Icon button：emoji → [custom_icon class="..."]（渲染為 <kbd><i class="ti ti-..."></i></kbd>）
    for emoji, cls in ICON_MAP.items():
        text = text.replace(emoji, f'[custom_icon class="{cls}"]')
    return text

# ---------- Callout 類型判別（mapping-rules §二，五種）----------
# 支援兩種來源格式（判斷結果一致）：
#   1. Notion API 原生：icon 是 emoji（💡ℹ️✅⚠️），color 是 Notion 底色名（*_background）
#      —— n8n Blocks→Markdown 節點直接輸出這種
#   2. 舊匯出格式：icon 是路徑字串（含 light-bulb/info/checkmark/warning），color 如 green_bg
#      —— samples/ 逆向驗證檔用這種
# Warning 與 Danger 同為 ⚠️/warning，靠底色（黃 vs 紅）區分，故底色判斷不可省。

def callout_type(icon, color):
    icon = icon or ""
    color = (color or "").lower()
    is_yellow = "yellow" in color
    is_red = "red" in color or "danger" in color
    # 順序：Message → Info → Success → Warning/Danger（後者需底色區分）
    if "💡" in icon or "light-bulb" in icon:
        return None  # Message：一般 note，無 alert_type
    if "ℹ" in icon or "info" in icon:
        return "info"
    if "✅" in icon or "checkmark" in icon:
        return "success"
    if "⚠" in icon or "warning" in icon:
        if is_red:
            return "danger"
        if is_yellow:
            return "warning"
        return "warning"  # 底色無法判斷時保守歸為 Warning
    return None

# ---------- 區塊解析 ----------

def parse_blocks(md):
    """把 Notion markdown 解析成中間表示（IR）區塊串列"""
    lines = strip_notion_artifacts(md).split("\n")
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped or stripped == "---":
            i += 1
            continue
        # callout（可跨多行）
        if stripped.startswith("<callout"):
            m = re.search(r'icon="([^"]*)"', stripped)
            c = re.search(r'color="([^"]*)"', stripped)
            body = []
            i += 1
            while i < len(lines) and "</callout>" not in lines[i]:
                body.append(lines[i].strip("\t").rstrip())
                i += 1
            i += 1
            joined = "\n".join(body)
            # 內部筆記剔除：含 toggle（<details>）或標題含 Review Notes 的 callout 不同步
            if "<details>" in joined or re.search(r"Review Notes", joined, re.I):
                continue
            blocks.append({"t": "callout", "icon": m.group(1) if m else "",
                           "color": c.group(1) if c else "", "body": body})
            continue
        # 程式碼區塊（fenced code，含語言標記）
        # 語言標記可能含空格（Notion 的 "plain text"、"shell script" 等）。
        # 先前用 ^```(\w*)\s*$ 會漏認這類開頭 fence，結果「結尾的 ```」反而被當成開頭，
        # 一路把文件剩餘內容全吞進程式碼區塊——後半篇文章整個消失。
        cm = re.match(r"^```(.*)$", stripped)
        if cm:
            lang, code = (cm.group(1).strip() or "markdown"), []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append({"t": "code", "lang": lang, "code": "\n".join(code).strip()})
            continue
        # 表格（markdown table）
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i+1].strip()):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append({"t": "table", "header": header, "rows": rows})
            continue
        # 數字清單 → 圓形數字（docly_list_item），整段連續編號收成「同一個」widget。
        # 編號項下 tab 縮排的子內容（巢狀 bullet 或接續說明）收進該項，渲染成
        # 內嵌 <p style="padding-left:40px"> —— 對齊實站結構（範本 7899 逆向確認）：
        # 不可用 <ul><li>，否則主題 CSS counter 會把 <li> 也算進圓圈編號。
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                text = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                i += 1
                sub = []
                while i < len(lines) and lines[i].startswith("\t") and lines[i].strip():
                    sline = lines[i].strip()
                    im = re.match(r'^!\[(.*?)\]\((\S+?)(?:\s+"([^"]*)")?\)$', sline)
                    bm = re.match(r"^-\s+(.*)$", sline)
                    if im:                       # 步驟下的巢狀圖片
                        sub.append(("image", im.group(1), im.group(2),
                                    im.group(3) or im.group(1)))
                    elif bm:                     # 巢狀 bullet
                        sub.append(("text", bm.group(1)))
                    else:                        # 接續說明
                        sub.append(("text", sline))
                    i += 1
                items.append({"text": text, "sub": sub})
            blocks.append({"t": "olist", "items": items})
            continue
        # 標題
        hm = re.match(r"^(#{2,4})\s+(.*)$", stripped)
        if hm:
            level = len(hm.group(1))
            title = re.sub(r"\*\*(.+?)\*\*", r"\1", hm.group(2)).strip()
            blocks.append({"t": "heading", "level": level, "text": title})
            i += 1
            continue
        # 粗體獨立行且為 Step X-Y 格式 → h4（舊文件相容規則）
        bm = re.match(r"^\*\*(Step [\d\-\.]+.*?)\*\*$", stripped)
        if bm:
            blocks.append({"t": "heading", "level": 4, "text": bm.group(1)})
            i += 1
            continue
        # 圖片：![可見圖說](url "alt text")——引號那格為選填的 alt，
        # 因 Notion API 不提供 alt，由上游從圖說的 [alt: ...] 標記拆出後放這裡
        im = re.match(r'^!\[(.*?)\]\((\S+?)(?:\s+"([^"]*)")?\)$', stripped)
        if im:
            cap = im.group(1)
            blocks.append({"t": "image", "caption": cap, "url": im.group(2),
                           "alt": im.group(3) or cap})
            i += 1
            continue
        # 清單（含巢狀，以 tab 縮排）
        if re.match(r"^(\t*)-\s+", line):
            items = []
            while i < len(lines) and re.match(r"^(\t*)-\s+", lines[i].rstrip()):
                lm = re.match(r"^(\t*)-\s+(.*)$", lines[i].rstrip())
                items.append((len(lm.group(1)), lm.group(2)))
                i += 1
            blocks.append({"t": "list", "items": items})
            continue
        # Last updated 行（斜體開頭）→ 取出其中的日期。
        # 這個日期是寫作者手動標記的「內容實質更新日」，不是同步時間，
        # 因此原樣沿用；只有 Notion 上沒寫時才退回同步日期。
        lu = re.match(r"^\*Last updated:\s*(.*?)\*?\s*$", stripped)
        if lu:
            blocks.append({"t": "last_updated", "date": lu.group(1).strip().rstrip("*").strip()})
            i += 1
            continue
        # 一般段落
        blocks.append({"t": "para", "text": stripped})
        i += 1
    return blocks

def list_to_html(items):
    """巢狀清單 → <ul> HTML"""
    html, stack = "", -1
    for depth, text in items:
        while depth > stack:
            html += "<ul>" if stack == -1 or html.endswith("</li>") is False else "<ul>"
            html = html[:-4] + "<ul>" if False else html
            stack += 1
        while depth < stack:
            html += "</ul></li>"
            stack -= 1
        if html.endswith("</li>"):
            html = html[:-5]  # 重開上一個 li 以巢狀
            html += f"<ul><li>{inline_md_to_html(text)}</li>"
            # 修正：上面處理巢狀進入
        html += f"<li>{inline_md_to_html(text)}</li>"
    while stack >= 0:
        html += "</ul>"
        stack -= 1
    return html

def list_to_html_v2(items):
    """巢狀清單 → HTML（遞迴實作，較可靠）"""
    def build(idx, depth):
        html = "<ul>"
        while idx < len(items):
            d, text = items[idx]
            if d < depth:
                break
            if d == depth:
                html += f"<li>{inline_md_to_html(text)}"
                # 檢查下一項是否為子清單
                if idx + 1 < len(items) and items[idx + 1][0] > depth:
                    sub, idx = build(idx + 1, depth + 1)
                    html += sub
                html += "</li>"
                idx += 1
            else:
                sub, idx = build(idx, d)
                html += sub
        return html + "</ul>", idx
    html, _ = build(0, 0)
    return html

# ---------- 主轉換 ----------

def convert(md, article_title, faq_group_slug, sync_date=None, link_map=None):
    global _eid_counter, _LINK_MAP, _UNRESOLVED_LINKS
    _eid_counter = 0          # 每次轉換重置，確保同輸入產生同 ID
    _LINK_MAP = link_map or {}
    _UNRESOLVED_LINKS = []
    blocks = parse_blocks(md)

    # 抽出 accordion 段落。**每個符合的 h2 各自成為一組**——先前所有段落共用同一個
    # group，一篇文章若同時有 FAQ 與 Troubleshooting，兩段會插入完全相同的
    # shortcode，前台各自展開全部題目（2026-08-11 以 4-10 BigCommerce 實測確認）。
    faq_sections, page_blocks = [], []
    bad_markers = []          # 疑似打錯的標記，回報而非靜默忽略
    section = None            # 目前所在的 accordion 段（None ＝ 一般內文）
    current_q = None
    for b in blocks:
        if b["t"] == "heading" and b["level"] == 2:
            _bad = near_miss_marker(b["text"])
            if _bad:
                bad_markers.append({"heading": b["text"], "marker": _bad})
            display, wants = _accordion_mode(b["text"])
            b = dict(b, text=display)     # 標記不進站上的標題
            if wants:
                section = {"title": display, "items": []}
                faq_sections.append(section)
                current_q = None
                page_blocks.append(b)     # 保留 h2，後面接 shortcode
                continue
            section = None
        if section is not None:
            # 問題標題接受 h3 與 h4：Style Guide 寫 h3，但實際文章多用 h4。
            # 若只認 h3，h4 的問答會既不進 faq_items 也不進頁面——整段靜默消失。
            if b["t"] == "heading" and b["level"] in (3, 4):
                # FAQ 題目最終是 WP 的文章標題（純文字），b["text"] 卻已經是
                # markdown——粗體題目會變成字面的 **粗體**。
                current_q = {"question": strip_inline_md(b["text"]), "answer_html": ""}
                section["items"].append(current_q)
            elif current_q is not None:
                if b["t"] == "para":
                    current_q["answer_html"] += f"<p>{inline_md_to_html(b['text'])}</p>"
                elif b["t"] == "list":
                    current_q["answer_html"] += list_to_html_v2(b["items"])
            continue
        page_blocks.append(b)

    # 群組命名：只有一段時沿用文章 slug（與站上既有的兩組完全相同，不受影響）；
    # 兩段以上才各自加後綴。命名只看內容不看順序，作者調換段落不會讓題目搬家。
    for sec in faq_sections:
        sec["group"] = (faq_group_slug if len(faq_sections) == 1
                        else f"{faq_group_slug}-{_slugify(sec['title'])}")
    faq_items = [q for sec in faq_sections for q in sec["items"]]
    faq_section_title = faq_sections[0]["title"] if faq_sections else None

    # SEO Meta / Version History 段剔除（此篇無，規則保留）
    # （偵測 '**SEO Meta**' 與 '### vN - ' 標記段，路由至 conversion report）

    # 組裝 Elementor 結構：每個 h2 起新 container
    containers, cur, report_images = [], [], []
    def flush():
        nonlocal cur
        if cur:
            containers.append(container(cur))
            cur = []

    # 開頭：Last updated container。
    # 取值優先序：Notion 文章上手動標記的日期（內容實質更新日）
    #           → 呼叫端傳入的 sync_date → 今天。
    # datetime 只在前兩者都沒有時才載入——實際文章都有 Last updated 行，
    # 因此正常路徑完全不需要 datetime（n8n runner 的 allowlist 只給 re）。
    doc_date = None
    for _b in blocks:
        if _b["t"] == "last_updated" and _b.get("date"):
            doc_date = _b["date"]
            break
    if not doc_date:
        doc_date = sync_date
    if not doc_date:
        from datetime import date
        doc_date = date.today().strftime("%B %d, %Y")
    containers.append(container([widget("text-editor", {
        "editor": f"<p><em>Last updated: {doc_date}</em></p>"})]))

    for b in page_blocks:
        if b["t"] == "last_updated":
            continue  # 已由自動生成取代
        if b["t"] == "heading":
            if b["level"] == 2:
                flush()
                cur.append(widget("heading", {"title": b["text"]}))
                _g = _group_for(faq_sections, b["text"])
                if _g:
                    cur.append(widget("shortcode", {"shortcode":
                        f'[faq group="{_g}" groupby="date" style="accordion"]'}))
            else:
                cur.append(widget("heading", {"title": b["text"],
                                              "header_size": f"h{b['level']}"}))
        elif b["t"] == "para":
            cur.append(widget("text-editor", {"editor": f"<p>{inline_md_to_html(b['text'])}</p>"}))
        elif b["t"] == "list":
            cur.append(widget("text-editor", {"editor": list_to_html_v2(b["items"])}))
        elif b["t"] == "olist":
            ul_items = []
            for it in b["items"]:
                html = f"<p>{inline_md_to_html(it['text'])}</p>"
                for sub in it["sub"]:
                    if sub[0] == "image":
                        # 步驟下的巢狀圖片 → 內嵌 [caption] shortcode（保留圖說＋lightbox，
                        # 且不佔圓圈編號）。結構逆向自實站範本 7915。
                        cap, iurl = sub[1], sub[2]
                        alt = sub[3] if len(sub) > 3 else cap
                        pending = "prod-files-secure" in iurl
                        if not pending:
                            iurl = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", iurl)
                        report_images.append({"url": iurl, "alt": alt, "caption": cap,
                                              "pending_upload": pending})
                        # 標準：Link To = Media File（<a href> 包 img）、Size = Large 1024x576
                        # （size-large class＋width/height）。對齊實站 7915 與站方統一規範。
                        html += (f'[caption align="alignnone" width="1024"]'
                                 f'<a href="{iurl}"><img class="size-large" src="{iurl}" '
                                 f'alt="{esc_attr(alt)}" width="1024" height="576" /></a> {cap}[/caption]')
                    else:
                        # 巢狀 bullet／接續說明 → 內嵌縮排段落（非 <li>，不被編號 counter 計入）
                        html += f'<p style="padding-left: 40px;">{inline_md_to_html(sub[1])}</p>'
                ul_items.append({"_id": eid(), "text": html})
            cur.append(widget("docly_list_item", {"style": "order_list", "steps": "",
                                                   "ul_icon_list": ul_items}))
        elif b["t"] == "code":
            cur.append(widget("docly_code_syntax_highlighter",
                              {"lng_type": b["lang"], "source_code": b["code"]}))
        elif b["t"] == "table":
            th = "".join(f"<th>{inline_md_to_html(h)}</th>" for h in b["header"])
            trs = "".join("<tr>" + "".join(f"<td>{inline_md_to_html(c)}</td>" for c in r) + "</tr>"
                          for r in b["rows"])
            cur.append(widget("text-editor",
                {"editor": f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"}))
        elif b["t"] == "image":
            url = b["url"]
            pending = "prod-files-secure" in url  # Notion 暫存圖，正式版由 n8n 上傳媒體庫
            if not pending:
                # 已在 WP 媒體庫：還原原始檔（去掉 -WxH 尺寸後綴）
                url = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", url)
            report_images.append({"url": url, "alt": b["alt"],
                                  "caption": b.get("caption", b["alt"]),
                                  "pending_upload": pending})
            cur.append(widget("image", {
                "image": {"url": url, "size": "", "alt": b["alt"], "source": "library"},
                "caption_source": "attachment", "link_to": "file", "open_lightbox": "yes"}))
        elif b["t"] == "callout":
            atype = callout_type(b["icon"], b["color"])
            title, body_lines = "", []
            for ln in b["body"]:
                tm = re.match(r"^\*\*(.+?)\*\*$", ln.strip())
                if tm and not title and not body_lines:
                    title = tm.group(1)
                else:
                    body_lines.append(ln)
            # body 內清單與段落
            desc = ""
            li_items = [(0, re.sub(r"^-\s+", "", l.strip())) for l in body_lines if l.strip().startswith("- ")]
            paras = [l for l in body_lines if l.strip() and not l.strip().startswith("- ")]
            for p in paras:
                desc += f"<p>{inline_md_to_html(p.strip())}</p>"
            if li_items:
                desc += list_to_html_v2(li_items)
            s = {"display_type": "note", "alert_title": title, "alert_description": desc}
            if atype:
                s["alert_type"] = atype
            cur.append(widget("docly_alerts_box", s))
    flush()

    template = {"content": containers, "page_settings": [],
                "version": "0.4", "title": article_title, "type": "page"}
    report = {"widgets": sum(len(c["elements"]) for c in containers),
              "containers": len(containers),
              "faq_items": len(faq_items), "faq_group": faq_group_slug,
              "faq_section": faq_section_title,
              # 每一段各自的判定結果——逐篇控制靠標記，這裡把結果攤開供核對，
              # 不必等前台出錯才發現標記寫錯
              # 疑似打錯的段落標記——不修正、只回報，讓寫作端知道哪裡要改
              "unrecognized_section_markers": bad_markers,
              "faq_sections": [{"title": s_["title"], "group": s_["group"],
                                "count": len(s_["items"]), "items": s_["items"]}
                               for s_ in faq_sections],
              "images": report_images,
              "images_pending_upload": sum(1 for x in report_images if x["pending_upload"]),
              # 換不掉的 Notion 連結：那是寫作端要修的內容問題，不靜默放行
              "unresolved_notion_links": list(_UNRESOLVED_LINKS)}
    return template, faq_items, report

# 預設會變成 accordion 的段落標題。維持既有行為，所以現有文章一個標記都不用加。
_ACCORDION_TITLES = ("faqs", "faq", "troubleshooting")
# 逐篇控制用的標記（Fay 2026-08-11 決定）。寫在 h2 標題結尾，不會進站上的標題。
_MARK_ACCORDION = re.compile(r"\s*[（(]\s*accordion\s*[)）]\s*$", re.I)
_MARK_PLAIN = re.compile(r"\s*[（(]\s*plain\s*[)）]\s*$", re.I)


# 標記打錯字時的偵測。`(Accordian)`／`(Plian)` 這類近似字若靜默忽略，該段就會
# 落回預設行為而沒人發現——把它變成大聲的回報。
#
# 用編輯距離而非「開頭幾個字母」：實測 `Plian` 是字母換位（p-l-i-a-n），
# 前綴比對抓不到。距離 ≤ 2 能涵蓋換位、漏字、多字，又不會把 `(Beta)`、
# `(Optional)`、`(v2)`、`(Plus)` 這些正常括號誤判。
_TRAILING_PAREN = re.compile(r"[（(]\s*([^（()）]{1,20}?)\s*[)）]\s*$")


def _edit_distance(a, b):
    """Levenshtein 距離。刻意手寫——n8n 沙箱只允許 import re。"""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def near_miss_marker(title):
    """標題結尾像是打錯的標記時回傳那段文字，否則空字串。"""
    if _MARK_ACCORDION.search(title) or _MARK_PLAIN.search(title):
        return ""
    m = _TRAILING_PAREN.search(title or "")
    if not m:
        return ""
    word = m.group(1).strip().lower()
    for target in ("accordion", "plain"):
        if word != target and _edit_distance(word, target) <= 2:
            return m.group(0).strip()
    return ""


def _accordion_mode(title):
    """h2 標題 → (顯示用標題, 是否折成 accordion)。

    判斷順序：明確標記優先，其次才看標題是不是 FAQ／Troubleshooting。
    無標記時行為與加這個功能之前完全一致——既有文章不必改動。
    """
    if _MARK_ACCORDION.search(title):
        return _MARK_ACCORDION.sub("", title).strip(), True
    if _MARK_PLAIN.search(title):
        return _MARK_PLAIN.sub("", title).strip(), False
    return title, title.strip().lower() in _ACCORDION_TITLES


def _slugify(text):
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", (text or "").lower()))


def _group_for(sections, title):
    for s in sections:
        if s["title"] == title:
            return s["group"]
    return ""


def in_faq_title(text):
    return _accordion_mode(text)[1]

# ---------- 圖片：佔位圖模式 ----------

# Elementor 內建佔位圖的站內路徑。**必須用文章所在站台自己的網址**——
# 跨站取圖會被 CDN／WAF 擋掉（正式站此路徑已回 403，圖會變破圖）。
PLACEHOLDER_PATH = "/wp-content/plugins/elementor/assets/images/placeholder.png"


def placeholder_url_for(wp_base):
    """依目標站台組出佔位圖網址。"""
    return (wp_base or "").rstrip("/") + PLACEHOLDER_PATH


def apply_placeholder_images(template, report, placeholder_url=PLACEHOLDER_PATH):
    """把尚未上傳的圖片換成 Elementor 佔位圖，並標上「待補圖 N」。

    第一階段（自動產出草稿、圖片人工補）用。來源網址是 Notion S3 預簽章網址，
    一小時後失效——直接寫進 WP 會在一小時內變破圖；換成佔位圖則是乾淨的灰底，
    人工一眼就知道哪幾張要補。

    回傳待補圖清單 [{"index", "alt"}]，供呼叫端產生補圖對照表。
    """
    pending = {img["url"] for img in report.get("images", []) if img.get("pending_upload")}
    todo = []

    def walk(elements):
        for el in elements:
            s = el.get("settings") or {}
            if el.get("widgetType") == "image":
                img = s.get("image") or {}
                if img.get("url") in pending:
                    alt = img.get("alt", "")
                    todo.append({"index": len(todo) + 1, "alt": alt})
                    img["url"] = placeholder_url
                    s["caption_source"] = "custom"
                    s["caption"] = f"🖼 待補圖 {len(todo)}：{alt}"
            if el.get("widgetType") == "docly_list_item":
                for it in s.get("ul_icon_list") or []:
                    for src in pending:
                        if src in it.get("text", ""):
                            alt = ""
                            m = re.search(r'alt="([^"]*)"', it["text"])
                            if m:
                                alt = unesc_attr(m.group(1))
                            todo.append({"index": len(todo) + 1, "alt": alt})
                            it["text"] = it["text"].replace(src, placeholder_url)
                            it["text"] = it["text"].replace(
                                "[/caption]", f" 🖼 待補圖 {len(todo)}[/caption]")
            walk(el.get("elements") or [])

    walk(template.get("content") or [])
    return todo


# ---------- 圖片上傳後回填版面 ----------

def apply_media_map(template, media_map):
    """把版面中的來源圖片網址換成 WP 媒體庫網址（上傳完成後呼叫）。

    media_map: { 來源網址: {"id", "full_url", "large_url", "width", "height"} }
      —— 即 `POST /synctify/v1/media/sideload` 回傳的每張圖資訊。

    處理兩種圖片形態：
      1. image widget → `settings.image.url` 換成原圖，並補上 media `id`
      2. 數字清單步驟內嵌的 [caption] shortcode → `<a href>` 指原圖（Link To = Media File）、
         `<img src>` 指 large 尺寸、補 `wp-image-{id}` class 與 `attachment_{id}`，
         寬高改用實際值（非 16:9 的圖高度不是 576）

    Notion S3 網址帶預簽章查詢字串且一小時後失效，故必須在寫入 WP 前完成替換。
    回傳實際替換的圖片數。
    """
    replaced = 0

    def patch_caption(text):
        nonlocal replaced
        for src, m in media_map.items():
            if src not in text:
                continue
            w = m.get("width") or 1024
            h = m.get("height") or 576
            mid = m.get("id")
            # <a href="來源"> → 原圖
            text = text.replace(f'<a href="{src}">', f'<a href="{m["full_url"]}">')
            # <img ... src="來源" ...> → large 尺寸＋wp-image class＋實際寬高
            text = text.replace(f'src="{src}"', f'src="{m["large_url"]}"')
            text = text.replace('<img class="size-large"',
                                f'<img class="wp-image-{mid} size-large"')
            text = re.sub(r'width="1024" height="576"', f'width="{w}" height="{h}"', text)
            text = text.replace('[caption align=', f'[caption id="attachment_{mid}" align=')
            text = re.sub(r'(\[caption[^\]]*?)width="1024"', rf'\g<1>width="{w}"', text)
            replaced += 1
        return text

    def walk(elements):
        nonlocal replaced
        for el in elements:
            s = el.get("settings") or {}
            if el.get("widgetType") == "image":
                img = s.get("image") or {}
                m = media_map.get(img.get("url"))
                if m:
                    img["url"] = m["full_url"]
                    img["id"] = m["id"]
                    replaced += 1
            if el.get("widgetType") == "docly_list_item":
                for it in s.get("ul_icon_list") or []:
                    if "[caption" in it.get("text", ""):
                        it["text"] = patch_caption(it["text"])
            if s.get("editor") and "[caption" in s["editor"]:
                s["editor"] = patch_caption(s["editor"])
            walk(el.get("elements") or [])

    walk(template.get("content") or [])
    return replaced

# ---------- CLI ----------

# ─── converter/translate_prompt.py ───
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

# ─── converter/term_check.py ───
"""術語檢查：從文章撈 UI 詞、對照術語表——Workflow 1（同步）轉換節點＋本機腳本共用。

## 為什麼在同步時做

2026-09-10 post 7622 事後才發現一批 UI 詞不在術語表，模型只好自己猜
（「仅入库」「仅入站」並存、Override 留英文），補詞、重跑、再比對多繞一輪。
Fay 定的流程是：同步成功 → 顯示撈到多少 UI 詞與比對結果 → 新詞寫進術語表 →
補完譯文、勾選已確認 → 才按翻譯。這支是那條流程的判斷核心。

## 候選詞靠寫作標記，不靠模型猜

    inline code → [direction]…[/direction]（同步時看到的）
                → <span class="direction_step">…</span>（已渲染頁面看到的）
    粗體        → <strong>…</strong>

兩種形態都收：同步節點拿到的是轉換結果（shortcode 還沒渲染），
本機腳本 scripts/glossary_article_terms.py 拿到的是站上頁面。

## 比對規則跟翻譯時同一套

用 translate_prompt 的 `_boundary_pattern`。另寫一套的話會出現「檢查說術語表有、
翻譯時卻沒命中」——那種落差看起來像模型不聽話，很難察覺。

打包進 n8n 時，產生器會把 translate_prompt.py 放在這份前面、並移除下面那行
import（同一個檔案裡已經有定義）。

## 只用 re

n8n Python Code node 只允許 `import re`。
"""




_DIRECTION_RE = re.compile(r"\[direction\](.*?)\[/direction\]", re.S)
_STEP_RE = re.compile(r'<span class="direction_step">(.*?)</span>', re.S)
_BOLD_RE = re.compile(r"<(strong|b)\b[^>]*>(.*?)</\1>", re.S)
_PATH_SEP = re.compile(r"\s*(?:>|&gt;)\s*")
_ANY_TAG_RE = re.compile(r"<[^>]+>")
# 代碼值不是術語：UPS_GR_RES、FEDEX_2DAY
_CODE_VALUE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")

# &amp; 放最後：先解它的話「&amp;gt;」會被解兩次變成「>」
_ENTITIES = (("&#038;", "&"), ("&#38;", "&"), ("&gt;", ">"), ("&lt;", "<"),
             ("&quot;", '"'), ("&#039;", "'"), ("&#39;", "'"), ("&#8217;", "’"),
             ("&nbsp;", " "), ("&#160;", " "), ("&amp;", "&"))

UI_PATH, BOLD = "UI 路徑", "粗體"
NEW, EMPTY, DRAFT, CONFIRMED = "new", "empty", "draft", "confirmed"


def unescape(s):
    for a, b in _ENTITIES:
        s = s.replace(a, b)
    return s


def clean_label(raw):
    """去標籤、還原實體、壓空白；去掉按鈕前的加號與結尾冒號。"""
    s = unescape(_ANY_TAG_RE.sub("", raw or ""))
    s = re.sub(r"\s+", " ", s).strip()
    # 「+ Add Code」「＋添加代码」按鈕前的加號是圖示，不是標籤的一部分
    s = s.lstrip("+＋").strip()
    return s.rstrip(":：").strip()


def keep(label):
    """像不像值得進術語表的 UI 詞。刻意保守：清單混進雜訊就沒人會看完。"""
    if not label or len(label) > 40 or len(label.split()) > 5:
        return False
    if not re.search(r"[A-Za-z]", label) or _CODE_VALUE.match(label):
        return False
    if re.search(r"[.?!。？！]$", label):
        return False                       # 粗體的整句話
    return label[0].isupper() or label[0] in "[("


def labels_in(text):
    """一段文字裡的 (種類, 標籤)。UI 路徑按 > 拆開。"""
    out = []
    for rx in (_DIRECTION_RE, _STEP_RE):
        for m in rx.finditer(text or ""):
            for part in _PATH_SEP.split(m.group(1)):
                out.append((UI_PATH, clean_label(part)))
    for m in _BOLD_RE.finditer(text or ""):
        out.append((BOLD, clean_label(m.group(2))))
    return [(k, l) for k, l in out if keep(l)]


def candidates(texts):
    """撈候選詞。回傳 {小寫: {label, kinds(set), n, example}}，保留出現順序。

    同一個詞大小寫不同只算一個，顯示第一次出現的寫法。
    """
    out = {}
    for t in texts:
        for kind, label in labels_in(t):
            key = label.lower()
            if key not in out:
                out[key] = {"label": label, "kinds": set(), "n": 0,
                            "example": clean_label(t)[:60]}
            out[key]["kinds"].add(kind)
            out[key]["n"] += 1
    return out


def strings_in(obj):
    """Elementor 模板裡所有字串（遞迴）。文字散在各層 settings 裡，逐欄列舉會漏。"""
    out = []
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def classify(label, glossary):
    """對照術語表。回傳 (狀態, 列)。

    同一個詞可能有多列（Channel／Channels 並存），有已確認且有簡中的就算已確認。
    """
    hits = [g for g in glossary
            if g["en"] and _boundary_pattern(g["en"]).fullmatch(label)]
    if not hits:
        return NEW, None
    for g in hits:
        if g["ok"] and g["zh"]:
            return CONFIRMED, g
    for g in hits:
        if g["zh"]:
            return DRAFT, g
    return EMPTY, hits[0]


def _rich_plain(rich):
    return "".join((t.get("plain_text") or "") for t in (rich or [])).strip()


def glossary_from_notion(pages):
    """Notion API 查詢結果（results 陣列）→ [{en, zh, ok, id}]。

    也吃**已精簡過**的列（有 en 鍵）：n8n 同步工作流的「整理術語表」會先把原始頁面
    （316 列約 1.15 MB）縮成 {en, zh, ok}（約 18 KB）再送進 Python 節點——
    2026-09-11 同步 6074（146 個區塊）時 task runner 因資料量過大逾時被中止。
    """
    rows = []
    for p in pages or []:
        if "en" in p:
            rows.append({"en": (p.get("en") or "").strip(), "zh": (p.get("zh") or "").strip(),
                         "ok": bool(p.get("ok")), "id": p.get("id")})
            continue
        props = p.get("properties") or {}
        rows.append({
            "en": _rich_plain((props.get("English") or {}).get("title")),
            "zh": _rich_plain((props.get("简体中文") or {}).get("rich_text")),
            "ok": bool((props.get("已確認") or {}).get("checkbox")),
            "id": p.get("id"),
        })
    return [r for r in rows if r["en"]]


def check(texts, glossary, when="同步時"):
    """整篇的比對結果。可直接序列化（n8n 輸出不接受 set）。

    when 是「這次檢查發生在什麼時候」，寫進摘要開頭（同步時／翻譯前）。
    Fay 2026-09-11：同步寫的術語檢查欄是「同步當下」的結果，補完術語後欄位還是舊的，
    寫「先補術語再翻譯」會讓人以為現在還不能翻——其實直接按翻譯就會重新檢查。
    """
    found = candidates(texts)
    groups = {NEW: [], EMPTY: [], DRAFT: [], CONFIRMED: []}
    for rec in found.values():
        st, row = classify(rec["label"], glossary)
        item = {"label": rec["label"], "n": rec["n"], "kinds": sorted(rec["kinds"])}
        if row:
            item["row"] = row["en"]
            item["zh"] = row["zh"]
        groups[st].append(item)
    pending = len(groups[NEW]) + len(groups[EMPTY]) + len(groups[DRAFT])
    total = len(found)
    # 「術語檢查」欄給按同步的人看的一句話，能不能按翻譯寫在最前面。
    # **沒有新詞不等於可以翻譯**——草稿未勾、有列沒簡中一樣會被翻譯按鈕擋下，
    # 所以「可以翻譯」只看 pending 是不是 0（Fay 2026-09-11 要求成功時也要顯示）。
    if not total:
        summary = "✅ 可以翻譯｜這篇沒有 UI 詞"
    elif not pending:
        summary = f"✅ 可以翻譯｜{total} 個 UI 詞都已確認"
    else:
        # 欄位只給一個數字：「待確認」＝還不能用在翻譯的詞（沒簡中＋有簡中沒勾），
        # 細分留給留言清單（Fay 2026-09-11）。演進：6074 最初顯示「新詞 20｜沒簡中 0｜
        # 未勾 0」，讀起來像新詞已有簡中；改成「待補簡中 20｜待勾選 0」後，「待勾選 0」
        # 資訊量仍然很低。要做的事都是「去術語表處理完、勾已確認」，合成一個數字最不會誤讀。
        # 待確認＋已確認＝UI 詞數。
        added = f"（本次新增 {len(groups[NEW])}）" if groups[NEW] else ""
        summary = (f"⚠️ {when}有詞待確認｜待確認 {pending}{added}｜"
                   f"已確認 {len(groups[CONFIRMED])}（UI 詞 {total}）")
    return {
        "total": total,
        "new": groups[NEW], "empty": groups[EMPTY],
        "draft": groups[DRAFT], "confirmed": groups[CONFIRMED],
        "pending": pending,
        "ready": not pending,
        "summary": summary,
        "when": when,
    }


def _join(labels, limit):
    if len(labels) <= limit:
        return "、".join(labels)
    return "、".join(labels[:limit]) + f"…等 {len(labels)} 個"


def comment_text(report, limit=30):
    """留言本文：接在粗體「術語檢查」之後的部分（以「：」開頭，不含術語表連結）。

    Notion 留言只有 rich_text，沒有清單區塊，所以項目符號用「•」字元（Fay 2026-09-11
    指定的格式）。單段 rich_text 上限 2000 字，清單過長就截斷。
    """
    if not report["pending"]:
        return ""
    # 補完不必再同步：翻譯前會用最新的術語表重新檢查。再同步反而會把新內容寫成
    # WP 草稿、讓前台又落後，按翻譯會被擋成「尚無法開始翻譯」（Fay 2026-09-11 釐清流程）。
    guide = "補上簡中、勾「已確認」後，直接按「翻譯」即可——翻譯前會用最新的術語表重新檢查，不用再同步。"
    if report.get("when", "同步時") == "同步時":
        guide += "發佈到 WP 後才能翻譯。"
    lines = [f"：{report['summary']}", guide, "", "待確認的詞：", ""]
    if report["new"]:
        lines.append("• 還沒有簡中（本次新增到術語表）："
                     + _join([x["label"] for x in report["new"]], limit))
    if report["empty"]:
        lines.append("• 還沒有簡中："
                     + _join([x["label"] for x in report["empty"]], limit))
    if report["draft"]:
        lines.append("• 有簡中、只差勾選："
                     + _join([f"{x['label']}（{x['zh']}）" for x in report["draft"]], limit))
    # 留 100 字給 n8n 端附加的「建列失敗」提醒，合起來仍在 2000 字內
    return "\n".join(lines)[:1900]


def comment_rich_text(report, glossary_url, limit=30):
    """留言的 rich_text。沒有待確認的詞回空陣列。

    固定四段：[0] 粗體「術語檢查」[1] 本文 [2] 空行＋👉 [3] 可點的「開啟產品用術語表」。
    n8n 的「Notion：留言術語檢查」會把「建列失敗」提醒插在 [1] 之後——改段落順序要一起改。
    """
    body = comment_text(report, limit)
    if not body:
        return []
    return [{"type": "text", "text": {"content": "術語檢查"}, "annotations": {"bold": True}},
            {"type": "text", "text": {"content": body}},
            {"type": "text", "text": {"content": "\n\n👉 "}},
            {"type": "text", "text": {"content": "開啟產品用術語表", "link": {"url": glossary_url}}}]


def new_row_properties(item, title, date):
    """新詞 → 術語表草稿列的 properties（POST /v1/pages）。已確認一律不勾。"""
    note = (f"{date} 同步〈{title}〉時自動建立：{'／'.join(item['kinds'])} "
            f"出現 {item['n']} 次。簡中、繁中待填；填好並勾「已確認」後才會用於翻譯。")
    return {
        "English": {"title": [{"text": {"content": item["label"]}}]},
        "類型": {"select": {"name": "UI 標籤"}},
        "一致性": {"select": {"name": "待比對"}},
        "備註": {"rich_text": [{"text": {"content": note}}]},
        "已確認": {"checkbox": False},
    }


def changelog_rich_text(report, title):
    """變更紀錄的一條（bulleted_list_item 的 rich_text）。沒有新詞回空陣列。"""
    labels = [x["label"] for x in report["new"]]
    if not labels:
        return []
    return [
        {"type": "text", "text": {"content": "／".join(labels)},
         "annotations": {"bold": True}},
        {"type": "text", "text": {"content":
            f" — 新增 {len(labels)} 列。同步〈{title}〉時由術語檢查自動加入"
            "（文章的 UI 路徑或粗體中出現、術語表沒有）。簡繁中待填，已確認未勾"}},
    ]


# ══════════════════════════════════════════════════════════════════
#  n8n 介面層
# ══════════════════════════════════════════════════════════════════

_SELFTEST_BLOCKS = [
    {"id": "h", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Overview"}]}},
    {"id": "p", "type": "paragraph", "paragraph": {"rich_text": [
        {"plain_text": "Go to "},
        {"plain_text": "Orders > Exception Orders", "annotations": {"code": True}},
        {"plain_text": " to begin."},
    ]}},
    {"id": "n1", "type": "numbered_list_item",
     "numbered_list_item": {"rich_text": [{"plain_text": "First step"}]}},
    {"id": "n2", "type": "numbered_list_item",
     "numbered_list_item": {"rich_text": [{"plain_text": "Second step"}]}},
]


def _collect(payloads):
    """從 n8n 輸入取出 (blocks, meta)。支援兩種上游接法：

      A) 一個 block 一個 item —— n8n Notion 節點 Get Child Blocks 的原生輸出
      B) 單一 item 帶 blocks 陣列 —— 上游接了 Aggregate／Code 整併過

    title／faq_group 由 Set 節點加在 item 上（A 的情況會加在每個 item，取第一個即可）。
    """
    if not payloads:
        return [], {}
    first = payloads[0]
    if "blocks" in first:
        return first["blocks"], first
    if "type" in first:          # item 本身就是 Notion block
        return payloads, first
    return [], first


def _hrefs_in(template):
    """從產出的 Elementor JSON 撈出所有 href——這是真正寫進 WP 的東西。"""
    found = []
    for _c in template["content"]:
        for _w in _c["elements"]:
            _st = _w["settings"] if "settings" in _w else {}
            for _k in _st:
                _v = _st[_k]
                if isinstance(_v, str):
                    found.extend(re.findall(r'href="([^"]+)"', _v))
                elif isinstance(_v, list):
                    for _item in _v:
                        if isinstance(_item, dict) and "text" in _item:
                            found.extend(re.findall(r'href="([^"]+)"', str(_item["text"])))
    return found


def _run(blocks, meta):
    title = meta["title"] if "title" in meta else "Untitled"
    faq_group = meta["faq_group"] if "faq_group" in meta else "untitled"
    sync_date = meta["sync_date"] if "sync_date" in meta else None
    # image_mode：placeholder（預設）＝ 未上傳的圖換成佔位圖，人工補
    #             keep         ＝ 保留來源網址（Notion S3 預簽章，一小時後失效，僅除錯用）
    image_mode = meta["image_mode"] if "image_mode" in meta else "placeholder"

    markdown, blocks_report = blocks_to_markdown(blocks)
    # Notion 內部連結 → WP 永久連結。對照表由上游兩個節點提供；沒給就跳過解析，
    # 行為與加這個功能之前一致。
    _link_map = build_link_map(meta["hub_rows"] if "hub_rows" in meta else [],
                               meta["wp_docs"] if "wp_docs" in meta else [],
                               "WP Post ID")
    template, faq_items, report = convert(markdown, title, faq_group,
                                          sync_date=sync_date, link_map=_link_map)
    report["blocks"] = blocks_report

    images_todo = []
    if image_mode == "placeholder":
        # 佔位圖必須取自文章所在站台；跨站會被 CDN／WAF 擋掉而變破圖
        wp_base = meta["wp_base"] if "wp_base" in meta else ""
        images_todo = apply_placeholder_images(
            template, report, placeholder_url_for(wp_base))
    report["images_todo"] = images_todo

    # 術語檢查：撈出 UI 詞（[direction]、粗體）對照術語表（Fay 2026-09-10 的流程）。
    # 術語表沒讀到就整段跳過——空術語表會把每個詞都判成新詞，自動建出幾十列雜訊。
    _gloss = glossary_from_notion(meta["glossary_rows"] if "glossary_rows" in meta else [])
    if _gloss:
        _terms = check(strings_in([template, faq_items]), _gloss)
        _today = meta["today"] if "today" in meta else ""
        _term_rows = [{"parent": {"database_id": "1ab2891d5ddd48db97d1f1c1afeefcf5"},
                       "properties": new_row_properties(_x, title, _today)}
                      for _x in _terms["new"]]
        _term_comment = comment_rich_text(_terms, "https://app.notion.com/p/3bc2f2ede27d81238c4fd63c958ac9fc")
        _term_changelog = changelog_rich_text(_terms, title)
    else:
        _gerr = meta["glossary_error"] if "glossary_error" in meta else ""
        _terms = {"skipped": True, "pending": 0, "ready": False, "new": [],
                  "summary": "⚠️ 未檢查：術語表沒有讀到"
                             + ("（" + _gerr + "）" if _gerr else "")
                             + "，暫時無法判斷能不能翻譯"}
        _term_rows, _term_comment, _term_changelog = [], [], []

    return {
        "template": template,
        "faq_items": faq_items,
        # 每個 accordion 段各自一組。下游用 splitOut 逐組呼叫 /faq/sync。
        "faq_sections": report["faq_sections"],
        # 疑似打錯的段落標記——逐篇控制靠標記，這裡讓打錯變成看得見的回報
        "unrecognized_section_markers": report["unrecognized_section_markers"],
        "report": report,
        "markdown": markdown,
        # 方便下游 HTTP 節點直接取用
        "elementor_data": template["content"],
        "title": title,
        # SEO Meta 段不進正文，改寫進 AIOSEO（POST /synctify/v1/seo/{id}）
        "seo": blocks_report["seo"],
        # 換不掉的 Notion 連結——寫作端要修的內容問題，往上帶方便回報
        "unresolved_notion_links": report["unresolved_notion_links"],
        # 診斷用：連結沒被換掉時，一眼看出是對照表沒進來還是查不到這一篇
        "link_map_size": len(_link_map),
        "link_inputs": {"hub_rows": len(meta["hub_rows"]) if "hub_rows" in meta else 0,
                        "wp_docs": len(meta["wp_docs"]) if "wp_docs" in meta else 0},
        # links_seen  = 中介 markdown 裡的連結（**解析前**）
        # links_written = 最終 Elementor JSON 裡的連結（**解析後**）
        # 兩者一比就知道解析有沒有發生，不必再猜。
        # 每個連結逐一說明：原始網址、解出的 page_id、對照表裡有沒有這一筆。
        # 這樣一欄就能分辨「認不出是 Notion 連結」與「認得出但查不到」。
        "links_seen": [{"url": _u,
                        "page_id": notion_page_id_from_url(_u),
                        "in_map": notion_page_id_from_url(_u) in _link_map}
                       for _u in re.findall(r"\]\(([^)]+)\)", markdown)],
        # 最終寫進 WP 的連結（解析後）——與上面一比就知道解析有沒有發生
        "links_written": _hrefs_in(template),
        # 對照表的前幾個 key，用來確認鍵值格式是否如預期
        "link_map_keys_sample": list(_link_map)[:3],
        # 術語檢查結果，以及下游直接用的現成內容（建列 body、留言、變更紀錄條目）
        "term_check": _terms,
        "term_new_rows": _term_rows,
        "term_comment": _term_comment,
        "term_changelog": _term_changelog,
    }


def _apply_media(payload):
    """mode=apply_media：把上傳結果回填進版面。

    上傳失敗的圖仍是會過期的 Notion S3 網址，直接寫進 WP 會在一小時內變破圖，
    因此回填後再對「仍未替換的圖」套一次佔位圖當安全網。
    """
    template = payload["template"]
    report = payload["report"] if "report" in payload else {"images": []}
    wp_base = payload["wp_base"] if "wp_base" in payload else ""

    media_map = {}
    failed = []
    for m in (payload["media"] if "media" in payload else []):
        if m.get("ok") and m.get("source_url"):
            media_map[m["source_url"]] = m
        else:
            failed.append(m)

    replaced = apply_media_map(template, media_map)
    fallback = apply_placeholder_images(template, report, placeholder_url_for(wp_base))

    return {
        "template": template,
        "elementor_data": template["content"],
        "title": payload["title"] if "title" in payload else "Untitled",
        "faq_items": payload["faq_items"] if "faq_items" in payload else [],
        "media_replaced": replaced,
        "media_failed": failed,
        "still_placeholder": fallback,
    }


_payloads = []
for _it in _items:
    _payloads.append(_it["json"])

if _payloads and "mode" in _payloads[0] and _payloads[0]["mode"] == "apply_media":
    return [{"json": _apply_media(_payloads[0])}]

# mode=version_marks：算出「vN 成為現行版本」後，母列與子列要改哪些字
if _payloads and "mode" in _payloads[0] and _payloads[0]["mode"] == "version_marks":
    _p = _payloads[0]
    _plan = plan_version_marks(_p["rows"] if "rows" in _p else [],
                               _p["blocks"] if "blocks" in _p else [],
                               _p["version"] if "version" in _p else "")
    # 兩個清單都可能是空的（已經是正確狀態）——下游用 splitOut 會自然跳過
    return [{"json": {"row_renames": _plan["row_renames"],
                      "block_updates": _plan["block_updates"],
                      "version": short_version(_p["version"] if "version" in _p else ""),
                      "nothing_to_do": (not _plan["row_renames"]
                                        and not _plan["block_updates"])}}]

_blocks, _meta = _collect(_payloads)

if not _blocks:
    # 沒有真實輸入 → 跑自我測試，確認執行環境與程式本身都正常
    _out = _run(_SELFTEST_BLOCKS, {"title": "Self Test", "faq_group": "self-test",
                                   "sync_date": "July 29, 2026"})
    _steps = 0
    _direction_ok = False
    for _c in _out["template"]["content"]:
        for _w in _c["elements"]:
            if _w["widgetType"] == "docly_list_item":
                _steps = len(_w["settings"]["ul_icon_list"])
            if _w["widgetType"] == "text-editor":
                if "[direction]Orders &gt; Exception Orders[/direction]" in _w["settings"]["editor"]:
                    _direction_ok = True
    _checks = {
        "數字清單 2 步（單一 widget、編號連續）": _steps == 2,
        "inline code → [direction] 且 > 轉成 &gt;": _direction_ok,
        "標題 Notion H1 → h2": _out["markdown"].startswith("## Overview"),
        # 打包後 term_check 與 translate_prompt 真的在同一個命名空間裡可用
        "術語檢查撈得到 UI 路徑": len(candidates(strings_in(_out["template"]))) == 2,
    }
    return [{"json": {
        "SELF_TEST": "PASS" if all(_checks.values()) else "FAIL",
        "checks": _checks,
        "containers": len(_out["template"]["content"]),
        "widgets": _out["report"]["widgets"],
        "note": "未收到 blocks 輸入，這是自我測試。接上 Notion 節點後會轉換真實內容。",
    }}]

return [{"json": _run(_blocks, _meta)}]
