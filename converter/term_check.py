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
import re

from translate_prompt import _boundary_pattern  # 打包進 n8n 時由產生器移除

_DIRECTION_RE = re.compile(r"\[direction\](.*?)\[/direction\]", re.S)
_STEP_RE = re.compile(r'<span class="direction_step">(.*?)</span>', re.S)
_BOLD_RE = re.compile(r"<(strong|b)\b[^>]*>(.*?)</\1>", re.S)
_PATH_SEP = re.compile(r"\s*(?:>|&gt;)\s*")
_TAG_RE = re.compile(r"<[^>]+>")
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
    s = unescape(_TAG_RE.sub("", raw or ""))
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
    """Notion API 查詢結果（results 陣列）→ [{en, zh, ok, id}]。"""
    rows = []
    for p in pages or []:
        props = p.get("properties") or {}
        rows.append({
            "en": _rich_plain((props.get("English") or {}).get("title")),
            "zh": _rich_plain((props.get("简体中文") or {}).get("rich_text")),
            "ok": bool((props.get("已確認") or {}).get("checkbox")),
            "id": p.get("id"),
        })
    return [r for r in rows if r["en"]]


def check(texts, glossary):
    """整篇的比對結果。可直接序列化（n8n 輸出不接受 set）。"""
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
        summary = (f"⚠️ 先補術語再翻譯｜新詞 {len(groups[NEW])}｜"
                   f"沒簡中 {len(groups[EMPTY])}｜未勾 {len(groups[DRAFT])}｜"
                   f"已確認 {len(groups[CONFIRMED])}（UI 詞 {total}）")
    return {
        "total": total,
        "new": groups[NEW], "empty": groups[EMPTY],
        "draft": groups[DRAFT], "confirmed": groups[CONFIRMED],
        "pending": pending,
        "ready": not pending,
        "summary": summary,
    }


def _join(labels, limit):
    if len(labels) <= limit:
        return "、".join(labels)
    return "、".join(labels[:limit]) + f"…等 {len(labels)} 個"


def comment_text(report, glossary_url, limit=30):
    """留言內容。Notion 單段 rich_text 上限 2000 字，清單過長就截斷。"""
    if not report["pending"]:
        return ""
    lines = [f"🔤 術語檢查：{report['summary']}",
             "翻譯前請到術語表補上譯文並勾「已確認」——沒勾的詞翻譯時不會使用，"
             "按「翻譯」也會被擋下。", ""]
    if report["new"]:
        lines.append("新詞（已自動加入術語表，簡繁中待填）："
                     + _join([x["label"] for x in report["new"]], limit))
    if report["empty"]:
        lines.append("術語表有列但沒有簡中："
                     + _join([x["label"] for x in report["empty"]], limit))
    if report["draft"]:
        lines.append("有簡中但還沒勾："
                     + _join([f"{x['label']}（{x['zh']}）" for x in report["draft"]], limit))
    lines += ["", "術語表：" + glossary_url]
    # 留 100 字給 n8n 端附加的「建列失敗」提醒，合起來仍在 2000 字內
    return "\n".join(lines)[:1900]


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
