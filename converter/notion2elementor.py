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
import json
import re
import secrets
import sys
from datetime import date

# ---------- 工具 ----------

def eid():
    """Elementor 元素 ID：7-8 位十六進位"""
    return secrets.token_hex(4)[:7]

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
        cm = re.match(r"^```(\w*)\s*$", stripped)
        if cm:
            lang, code = cm.group(1) or "markdown", []
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
        # 數字清單 → 圓形數字（docly_list_item），連續編號行收成一組
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
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
    sync_date = sync_date or date.today().strftime("%B %d, %Y")
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
            if b["t"] == "heading" and b["level"] == 3:
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
            cur.append(widget("docly_list_item", {"style": "order_list", "steps": "",
                "ul_icon_list": [{"_id": eid(), "text": f"<p>{inline_md_to_html(t)}</p>"}
                                 for t in b["items"]]}))
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

# ---------- CLI ----------

if __name__ == "__main__":
    src, title, slug, outdir = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    with open(src) as f:
        md = f.read()
    template, faqs, report = convert(md, title, slug, sync_date="July 15, 2026")
    base = outdir.rstrip("/")
    with open(f"{base}/elementor-template-output.json", "w") as f:
        json.dump(template, f, ensure_ascii=False, indent=1)
    with open(f"{base}/faq-items.json", "w") as f:
        json.dump({"group": slug, "items": faqs}, f, ensure_ascii=False, indent=1)
    with open(f"{base}/conversion-report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
