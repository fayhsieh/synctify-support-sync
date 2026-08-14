# ══════════════════════════════════════════════════════════════════
#  自動產生，請勿直接編輯
#  來源：converter/tp_blocks.py
#  重新產生：./.venv/bin/python scripts/build_n8n_code_node.py
#  修改請改 converter/tp_blocks.py 並跑 pytest，再重新產生後貼回 n8n
#
#  Workflow 3｜節點「抽出待翻譯區塊」
#  輸入：任意順序的 items，其中要有
#     (a) 已發佈頁面的 HTML —— 字串欄位（HTTP Request 設 responseFormat=text）
#     (b) GET /synctify/v1/tp/strings 的回應 —— 帶 items 陣列
#  輸出：單一 item，含 pending 清單。要逐段翻譯就在後面接 Split Out。
# ══════════════════════════════════════════════════════════════════

# ─── converter/tp_blocks.py ───
"""從已發佈頁面的 HTML 取出「可翻譯的區塊」——Workflow 3 節點 2／3。

## 為什麼要從 HTML 抽，而不是問 TranslatePress

TP 自動登錄的字串是**片段**：它以行內元素的邊界切分，粗體、inline code、連結、
我們的 shortcode 都是切點。`Click <strong>Submit</strong> to update the stock level.`
在字典裡只剩 `to update the stock level.`。翻這種殘句，產出就是 Support Center
早期那批很生硬的譯文。

字典表的 `block_type` 區分兩代：0＝片段、1＝整句（original 含渲染後 HTML）。
但 **block_type=1 的列原本只有人在 TP 編輯器「上升到外層」才會生成，而且一生成
就已是 status=2**——所以全新文章的字典裡只有片段，沒有任何「待翻譯的整句」可撈。

因此整句必須由我們自己產生，原文取自已發佈頁面的區塊 innerHTML。那正是 TP 看到
的同一份來源，寫回去（POST /synctify/v1/tp/block）TP 就會採用。
2026-08-14 在測試站 post 7251 實測通過。

## 兩個實測得到的規則

**換行不必逐位元復刻**：TP 比對時會正規化。測試站有 6 筆字典列存 `\r\n` 而頁面
現在給 `\n`，那些譯文照樣正常渲染。所以比對一律先過 `normalize()`。

**範圍必須限縮在文章內容容器**：整頁 143k、內容區只有 44k。不限縮就會撈到側邊欄
導覽、頁首頁尾，甚至 Google Tag Manager 的 iframe（那個真的躺在字典裡等著被翻）。

## 只用 re

這份要打包進 n8n 的 Python Code node，那裡只允許 `import re`
（`N8N_RUNNERS_STDLIB_ALLOW=re`），所以底下的標籤掃描是手寫的堆疊，不用 HTML parser。
"""
import re

# 會承載文字、且值得整段翻譯的區塊級元素。
# 標題類雖然實測都沒有行內標記，仍然要收——它們同樣需要翻譯。
BLOCK_TAGS = frozenset((
    "p", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "td", "th", "figcaption", "blockquote", "dd", "dt",
))

# 出現這些就代表這一段是行內混排，片段翻譯會把句子切碎
INLINE_TAGS = frozenset(("strong", "b", "em", "i", "code", "a", "span", "u", "mark", "small"))

# 整段內含這些的一律跳過：不是散文，翻了只會壞掉
JUNK_TAGS = frozenset(("iframe", "script", "style", "noscript", "svg", "form", "input"))

# Notion 的留言／協作標記。規格書（docs/mapping-rules.md §179）要求轉換時剔除，
# 但轉換器目前只處理 discussion span 與 notionvc 註解兩種，
# `notion-enable-hover` 這種漏了——2026-08-14 在測試站掃到 7／36 篇已發佈文章帶著它。
# 這裡**只標記不剔除**：original 必須與頁面逐字相符，動了就對不上 TP 的比對；
# 真正該修的是轉換器與那幾篇既有文章。
_NOISE_RE = re.compile(
    r"<!--\s*notionvc:|notion-enable-hover|discussion-urls=", re.I)

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?>")


def normalize(s):
    """比對用的正規化：只統一換行。

    刻意**不**壓縮空白——shortcode 模板產生的連續空格是原文的一部分，
    壓掉之後寫回去的 original 會跟頁面對不上。
    """
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def text_of(inner):
    """區塊的可見文字（去標籤、去 HTML 實體的空白、壓縮空白）。只用來判斷有沒有內容。"""
    t = strip_tags(inner)
    t = t.replace("&nbsp;", " ").replace("&#160;", " ")
    return re.sub(r"\s+", " ", t).strip()


def detect_post_id(html):
    """從頁面自己認出 post id。

    Elementor 會在容器上留 `data-elementor-id="7251"`。有了這個，n8n 的 Code node
    只需要「HTML ＋ 現有字典列」兩個輸入，不必再從別的節點把 id 傳進來——
    Python Code node 只拿得到 `_items`，跨節點取值一律行不通，能少一個依賴就少一個。
    """
    m = re.search(r'data-elementor-id="(\d+)"', html or "")
    return int(m.group(1)) if m else None


def content_scope(html, post_id):
    """取出 Elementor 內容容器的區段。找不到就回整份（讓呼叫端自己決定要不要用）。

    容器長這樣：<div data-elementor-type="wp-post" data-elementor-id="7251" …>
    用 div 計數找到對應的收尾，不能用非貪婪比對——內容裡有幾百個巢狀 div。
    """
    html = normalize(html)
    m = re.search(r'data-elementor-id="%d"' % int(post_id), html)
    if not m:
        return html
    start = html.rfind("<div", 0, m.start())
    if start < 0:
        return html

    depth = 0
    for t in re.finditer(r"<(/?)div\b[^>]*>", html[start:]):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return html[start:start + t.end()]
    return html[start:]


def extract_blocks(html, post_id=None):
    """抽出「最內層」的區塊級元素。

    最內層是關鍵：實測有 `<li><p>…</p></li>` 這種結構，那時該翻的是 `<p>`。
    只要某個區塊元素裡面還有別的區塊元素，它自己就不是翻譯單位。

    回傳 [{original, tag, has_inline, text}]，順序即頁面順序，重複的原文只留第一次。
    """
    scope = content_scope(html, post_id) if post_id is not None else normalize(html)

    stack = []          # [tag, 內容起點, 是否含子區塊]
    out, seen = [], set()

    for m in _TAG_RE.finditer(scope):
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in BLOCK_TAGS:
            continue
        if not closing:
            for fr in stack:
                fr[2] = True          # 外層有子區塊了，它自己不是葉節點
            stack.append([tag, m.end(), False])
            continue

        # 收尾：往回找同名的那一層，中間對不上的一律丟棄（頁面 HTML 不保證完美）
        idx = None
        for i in range(len(stack) - 1, -1, -1):
            if stack[i][0] == tag:
                idx = i
                break
        if idx is None:
            continue
        frame = stack[idx]
        del stack[idx:]
        if frame[2]:
            continue                  # 有子區塊，不是翻譯單位

        inner = scope[frame[1]:m.start()]
        if not text_of(inner):
            continue                  # 空的、或只有圖片與標籤
        low = inner.lower()
        if any(("<" + j) in low for j in JUNK_TAGS):
            continue                  # iframe / script 之類，不是散文

        original = inner.strip()
        key = normalize(original)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "original": original,
            "tag": frame[0],
            "has_inline": bool(re.search(
                r"<(%s)\b" % "|".join(sorted(INLINE_TAGS)), inner, re.I)),
            "has_notion_residue": bool(_NOISE_RE.search(inner)),
            "text": text_of(inner),
        })
    return out


def pending_blocks(html, post_id=None, existing=None):
    """扣掉字典裡已經有的，回傳這篇還缺哪些區塊。

    existing 收 GET /synctify/v1/tp/strings 回來的 items（或純字串清單）。
    **只有 status=2（人工精修）才算「已完成」不必再送**——status=0/1 的列送去
    /tp/block 會被更新成新譯文，那是我們要的（重跑可以修正舊機翻）。
    """
    if post_id is None:
        post_id = detect_post_id(html)
    done = set()
    for e in existing or []:
        if isinstance(e, dict):
            if int(e.get("status", 0)) != 2:
                continue
            e = e.get("original", "")
        done.add(normalize(e).strip())

    blocks = extract_blocks(html, post_id)
    pending = [b for b in blocks if normalize(b["original"]).strip() not in done]
    return {
        "post_id": post_id,
        "total_blocks": len(blocks),
        "already_human": len(blocks) - len(pending),
        "pending": pending,
        # 帶著 Notion 留言標記的區塊：這篇文章的來源就是髒的，翻譯只會把髒東西
        # 一起帶進簡中版。呼叫端應該把它報出來，而不是靜靜地翻下去。
        "notion_residue": [b["original"] for b in pending if b["has_notion_residue"]],
    }


# ══════════════════════════════════════════════════════════════════
#  n8n 介面層
# ══════════════════════════════════════════════════════════════════

def _unwrap(_it):
    if isinstance(_it, dict):
        _j = _it.get("json")
        return _j if isinstance(_j, dict) else _it
    return {}


def _pick_inputs(_items):
    """從 items 裡認出「HTML」與「字典列」，不管它們的先後或來自哪個節點。

    Python Code node 只拿得到 `_items`（跨節點取值一律行不通），而上游是兩個
    HTTP 節點。與其把接線方式寫死，不如靠形狀判斷——Merge 成一個 item、或兩條
    線直接接進來，兩種接法都能吃。
    """
    _html, _existing = "", []
    for _raw in _items or []:
        _j = _unwrap(_raw)
        for _k in ("data", "body", "html", "page", "content"):
            _v = _j.get(_k)
            # 頁面 HTML 一定夠長且含標籤；避免把短字串誤認成頁面
            if isinstance(_v, str) and len(_v) > 500 and "<" in _v:
                _html = _v
        _rows = _j.get("items")
        if isinstance(_rows, list) and _rows and isinstance(_rows[0], dict) \
                and "original" in _rows[0]:
            _existing = _rows
    return _html, _existing


_html, _existing = _pick_inputs(_items)

if not _html:
    return [{"json": {
        "ok": False,
        "error": "沒有收到頁面 HTML。上游的 HTTP Request 節點要設 "
                 "Response Format = text，並確認它的輸出有進到這個節點。",
        "received_items": len(_items or []),
    }}]

_out = pending_blocks(_html, None, _existing)

if not _out["post_id"]:
    return [{"json": {
        "ok": False,
        "error": "頁面裡找不到 data-elementor-id，抓到的可能不是文章頁"
                 "（例如被導去登入頁或 WAF 的驗證頁）。",
        "html_head": _html[:200],
    }}]

return [{"json": {
    "ok": True,
    "post_id": _out["post_id"],
    "total_blocks": _out["total_blocks"],
    "already_human": _out["already_human"],
    "pending_count": len(_out["pending"]),
    "pending": _out["pending"],
    # 這篇的來源帶著 Notion 留言標記——翻譯會把髒東西一起帶進簡中版。
    # 不中止流程，但要讓人看得到。
    "notion_residue_count": len(_out["notion_residue"]),
    "notion_residue": _out["notion_residue"][:5],
}}]
