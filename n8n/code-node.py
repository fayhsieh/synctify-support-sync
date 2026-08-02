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
_SKIP_SECTION_PATTERNS = [
    re.compile(r"^\s*\**\s*SEO\s*Meta\s*\**\s*$", re.I),
    re.compile(r"^\s*Version\s+History\s*$", re.I),
    re.compile(r"Content\s+Review\s+Notes", re.I),
]


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
# Notion 有些語言名帶空格（"plain text"、"shell script"），直接寫進 fence 會讓
# markdown parser 難以辨識，故一律正規化。"plain text" 對到 markdown 是站上慣例
# （實站範本 7978 的同一段程式碼區塊即為 lng_type=markdown）。
_CODE_LANG_MAP = {
    "plain text": "markdown",
    "plaintext": "markdown",
}


def _code_language(lang):
    lang = (lang or "").strip().lower()
    if lang in _CODE_LANG_MAP:
        return _CODE_LANG_MAP[lang]
    return lang.replace(" ", "") or "markdown"


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
    report = {"skipped_sections": [], "unsupported": [], "excluded_toggles": 0}
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
                else:
                    continue
            else:
                continue

        text = _plain(_rt_items(data))
        if (heading_level is not None or btype == "paragraph") and text:
            if any(p.search(text) for p in _SKIP_SECTION_PATTERNS):
                skip_until_heading_level = heading_level if heading_level is not None else 9
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
            caption = rich_text(data.get("caption"))
            if indent:
                lines.append(f"{tab}![{caption}]({url})")   # 巢狀在步驟下 → [caption] shortcode
            else:
                _blank(lines)
                lines.append(f"![{caption}]({url})")

        elif btype == "callout":
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
}

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
                    im = re.match(r"^!\[(.*?)\]\((.*?)\)$", sline)
                    bm = re.match(r"^-\s+(.*)$", sline)
                    if im:                       # 步驟下的巢狀圖片
                        sub.append(("image", im.group(1), im.group(2)))
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
        # 圖片
        im = re.match(r"^!\[(.*?)\]\((.*?)\)$", stripped)
        if im:
            blocks.append({"t": "image", "alt": im.group(1), "url": im.group(2)})
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
        # Last updated 行（斜體開頭）→ 標記，輸出時以同步日期重生
        if re.match(r"^\*Last updated:", stripped):
            blocks.append({"t": "last_updated"})
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

def convert(md, article_title, faq_group_slug, sync_date=None):
    if not sync_date:
        # 延遲載入：呼叫端（n8n／service）通常會帶入 sync_date，
        # 不帶時才需要 datetime，避免核心路徑多一個 import 相依。
        from datetime import date
        sync_date = date.today().strftime("%B %d, %Y")
    global _eid_counter
    _eid_counter = 0          # 每次轉換重置，確保同輸入產生同 ID
    blocks = parse_blocks(md)

    # 抽出 FAQ 段（## FAQs / ## Troubleshooting 之後的 ###+段落）
    faq_items, page_blocks, in_faq = [], [], False
    faq_section_title = None
    current_q = None
    for b in blocks:
        if b["t"] == "heading" and b["level"] == 2:
            if b["text"].lower() in ("faqs", "faq", "troubleshooting"):
                in_faq = True
                faq_section_title = b["text"]
                page_blocks.append(b)  # 保留 h2，後面接 shortcode
                continue
            in_faq = False
        if in_faq:
            # 問題標題接受 h3 與 h4：Style Guide 寫 h3，但實際文章多用 h4。
            # 若只認 h3，h4 的問答會既不進 faq_items 也不進頁面——整段靜默消失。
            if b["t"] == "heading" and b["level"] in (3, 4):
                current_q = {"question": b["text"], "answer_html": ""}
                faq_items.append(current_q)
            elif current_q is not None:
                if b["t"] == "para":
                    current_q["answer_html"] += f"<p>{inline_md_to_html(b['text'])}</p>"
                elif b["t"] == "list":
                    current_q["answer_html"] += list_to_html_v2(b["items"])
            continue
        page_blocks.append(b)

    # SEO Meta / Version History 段剔除（此篇無，規則保留）
    # （偵測 '**SEO Meta**' 與 '### vN - ' 標記段，路由至 conversion report）

    # 組裝 Elementor 結構：每個 h2 起新 container
    containers, cur, report_images = [], [], []
    def flush():
        nonlocal cur
        if cur:
            containers.append(container(cur))
            cur = []

    # 開頭：Last updated container
    containers.append(container([widget("text-editor", {
        "editor": f"<p><em>Last updated: {sync_date}</em></p>"})]))

    for b in page_blocks:
        if b["t"] == "last_updated":
            continue  # 已由自動生成取代
        if b["t"] == "heading":
            if b["level"] == 2:
                flush()
                cur.append(widget("heading", {"title": b["text"]}))
                if in_faq_title(b["text"]):
                    cur.append(widget("shortcode", {"shortcode":
                        f'[faq group="{faq_group_slug}" groupby="date" style="accordion"]'}))
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
                        alt, iurl = sub[1], sub[2]
                        pending = "prod-files-secure" in iurl
                        if not pending:
                            iurl = re.sub(r"-\d+x\d+(\.\w+)$", r"\1", iurl)
                        report_images.append({"url": iurl, "alt": alt, "pending_upload": pending})
                        # 標準：Link To = Media File（<a href> 包 img）、Size = Large 1024x576
                        # （size-large class＋width/height）。對齊實站 7915 與站方統一規範。
                        html += (f'[caption align="alignnone" width="1024"]'
                                 f'<a href="{iurl}"><img class="size-large" src="{iurl}" '
                                 f'alt="{alt}" width="1024" height="576" /></a> {alt}[/caption]')
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
            report_images.append({"url": url, "alt": b["alt"], "pending_upload": pending})
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
              "images": report_images,
              "images_pending_upload": sum(1 for x in report_images if x["pending_upload"])}
    return template, faq_items, report

def in_faq_title(text):
    return text.lower() in ("faqs", "faq", "troubleshooting")

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


def _run(blocks, meta):
    title = meta["title"] if "title" in meta else "Untitled"
    faq_group = meta["faq_group"] if "faq_group" in meta else "untitled"
    sync_date = meta["sync_date"] if "sync_date" in meta else None
    # image_mode：placeholder（預設）＝ 未上傳的圖換成佔位圖，人工補
    #             keep         ＝ 保留來源網址（Notion S3 預簽章，一小時後失效，僅除錯用）
    image_mode = meta["image_mode"] if "image_mode" in meta else "placeholder"

    markdown, blocks_report = blocks_to_markdown(blocks)
    template, faq_items, report = convert(markdown, title, faq_group, sync_date=sync_date)
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
        "report": report,
        "markdown": markdown,
        # 方便下游 HTTP 節點直接取用
        "elementor_data": template["content"],
        "title": title,
    }


_payloads = []
for _it in _items:
    _payloads.append(_it["json"])

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
