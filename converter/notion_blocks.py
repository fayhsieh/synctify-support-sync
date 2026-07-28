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

# Notion 只有 heading_1/2/3；站上文章的最上層章節是 `##`（h2）。
# 因此 Notion H1 → `##`、H2 → `###`、H3 → `####`。
# ⚠️ 此偏移是依現有文章寫作慣例推定，尚未用真實 block 資料驗證過。
HEADING_OFFSET = 1

# 不同步的段落（CLAUDE.md）：內部審核筆記、SEO Meta、Version History
_SKIP_SECTION_PATTERNS = [
    re.compile(r"^\s*\**\s*SEO\s*Meta\s*\**\s*$", re.I),
    re.compile(r"^\s*Version\s+History\s*$", re.I),
    re.compile(r"Content\s+Review\s+Notes", re.I),
]


# ---------- rich text → 行內 markdown ----------

def rich_text(items):
    """Notion rich_text 陣列 → 行內 markdown（順序：code → bold → italic → link）"""
    out = []
    for t in items or []:
        s = t.get("plain_text", "")
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
    return "".join(t.get("plain_text", "") for t in items or [])


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
        if skip_until_heading_level is not None:
            if btype in ("heading_1", "heading_2", "heading_3"):
                lvl = int(btype[-1])
                if lvl <= skip_until_heading_level:
                    skip_until_heading_level = None
                else:
                    continue
            else:
                continue

        text = _plain(data.get("rich_text"))
        if btype in ("heading_1", "heading_2", "heading_3", "paragraph") and text:
            if any(p.search(text) for p in _SKIP_SECTION_PATTERNS):
                lvl = int(btype[-1]) if btype.startswith("heading_") else 9
                skip_until_heading_level = lvl
                report["skipped_sections"].append(text.strip()[:60])
                continue

        # ── 各 block 類型
        if btype in ("heading_1", "heading_2", "heading_3"):
            level = int(btype[-1]) + HEADING_OFFSET
            _blank(lines)
            lines.append(f"{'#' * level} {rich_text(data.get('rich_text'))}")

        elif btype == "paragraph":
            body = rich_text(data.get("rich_text"))
            if body.strip():
                if indent:
                    lines.append(f"{tab}{body}")   # 數字清單下的接續說明
                else:
                    _blank(lines)
                    lines.append(body)
            _render(children, lines, report, indent)

        elif btype == "bulleted_list_item":
            lines.append(f"{tab}- {rich_text(data.get('rich_text'))}")
            # 項目符號的子項再縮一層（轉換器用 tab 數判斷巢狀層級）
            _render(children, lines, report, indent + 1)

        elif btype == "to_do":
            lines.append(f"{tab}- {rich_text(data.get('rich_text'))}")
            _render(children, lines, report, indent + 1)

        elif btype == "numbered_list_item":
            # 連續編號之間不可有空行；子內容以 tab 縮排接在後面
            lines.append(f"{tab}1. {rich_text(data.get('rich_text'))}")
            _render(children, lines, report, indent + 1)

        elif btype == "code":
            _blank(lines)
            lang = data.get("language") or "markdown"
            lines.append(f"```{lang}")
            lines.extend(_plain(data.get("rich_text")).split("\n"))
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
            first = rich_text(data.get("rich_text"))
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
            lines.append(rich_text(data.get("rich_text")))
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
