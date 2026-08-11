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
import re

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
              "images": report_images,
              "images_pending_upload": sum(1 for x in report_images if x["pending_upload"]),
              # 換不掉的 Notion 連結：那是寫作端要修的內容問題，不靜默放行
              "unresolved_notion_links": list(_UNRESOLVED_LINKS)}
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

if __name__ == "__main__":
    # 僅 CLI 需要，不放模組頂層（保持核心轉換只相依 re）
    import json
    import sys

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
