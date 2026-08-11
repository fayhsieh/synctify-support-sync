# ══════════════════════════════════════════════════════════════════
#  自動產生，請勿直接編輯
#  來源：converter/notion_blocks.py + converter/notion2elementor.py
#  重新產生：./.venv/bin/python scripts/build_n8n_code_node.py
#  修改請改 converter/*.py 並跑 pytest，再重新產生後貼回 n8n
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
            caption, alt = split_caption_alt(rich_text(data.get("caption")))
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


def build_link_map(hub_rows, wp_docs):
    """{Notion 頁面 id: {"url", "title", "doc_name"}}。

    hub_rows —— Content Hub 的查詢結果（Notion API 原生格式）
    wp_docs  —— WP 的 docs 清單，每筆需有 id、link、title

    `doc_name` 是給連結文字判斷用的：Notion 的頁面提及（mention）會把被提及頁面的
    Doc name 當成顯示文字，那個名稱帶編號前綴（`7-1 Reports Center`），站上並沒有。
    連結文字若正好等於 Doc name，就代表它是提及而非作者自訂的字，可以換成 WP 標題。

    WP Post ID 只記在母列，但寫作者可能連到版本子列，所以子列沿用母列的資料。
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
        rt = (props.get("WP Post ID") or {}).get("rich_text") or []
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

def strip_notion_artifacts(text):
    """剔除 Notion 留言標記與雜訊"""
    text = re.sub(r'<span discussion-urls="[^"]*">(.*?)</span>', r"\1", text, flags=re.S)
    text = re.sub(r"<!--\s*notionvc:[^>]*-->", "", text)
    return text

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
                current_q = {"question": b["text"], "answer_html": ""}
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
                                 f'alt="{alt}" width="1024" height="576" /></a> {cap}[/caption]')
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
                                alt = m.group(1)
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
                               meta["wp_docs"] if "wp_docs" in meta else [])
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
    }
    return [{"json": {
        "SELF_TEST": "PASS" if all(_checks.values()) else "FAIL",
        "checks": _checks,
        "containers": len(_out["template"]["content"]),
        "widgets": _out["report"]["widgets"],
        "note": "未收到 blocks 輸入，這是自我測試。接上 Notion 節點後會轉換真實內容。",
    }}]

return [{"json": _run(_blocks, _meta)}]
