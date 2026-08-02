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
            lang = data.get("language") or "markdown"
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
