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
        href = t.get("href")
        if href:
            s = f"[{s}]({href})"
        out.append(s)
    return "".join(out)


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
