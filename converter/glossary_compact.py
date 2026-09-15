"""翻譯用精簡術語表：從完整術語表產生只含「已確認」詞的純文字。

給用 Notion 連接器翻譯的 Claude 一次讀完。完整表經 Notion API 讀取約 92 萬字元
（大多是 Notion 的格式資訊，一半的純文字又是備註），這份只有幾千字元。

刻意不 import 任何模組（連 re 都不用），之後可以原樣打包進 n8n 的 Python Code node；
名稱都加 compact／_gc 前綴，避免打包後與其他模組撞名（見 term_check 的 _plain 事件）。
"""

COMPACT_HEADER = "English | 简体中文 | 繁體中文 | 類型"
COMPACT_PLACEHOLDER = "（尚未產生）"


def _gc_text(prop):
    if not prop:
        return ""
    kind = prop.get("type")
    if kind not in ("title", "rich_text"):
        return ""
    return "".join(t.get("plain_text", "") for t in prop.get(kind) or []).strip()


def _gc_cell(value):
    """欄位內的「|」會讓一行看起來多一欄，換成全形斜線。"""
    return value.replace("|", "／").replace("\n", " ").strip()


def compact_rows(pages):
    """Notion 原始 page（properties）或 glossary_sync.fetch_glossary 的列（props）→ 精簡列。"""
    out = []
    for p in pages:
        props = p.get("properties") or p.get("props") or {}
        kind = (props.get("類型") or {}).get("select") or {}
        out.append({
            "en": _gc_text(props.get("English")),
            "zh": _gc_text(props.get("简体中文")),
            "tw": _gc_text(props.get("繁體中文")),
            "kind": kind.get("name") or "",
            "key": _gc_text(props.get("i18n key")),
            "ok": bool((props.get("已確認") or {}).get("checkbox")),
        })
    return out


def compact_usable(rows):
    """能放進精簡表的列：已確認、有英文、有簡中（沒有簡中就沒東西可套）。"""
    return [r for r in rows if r["ok"] and r["en"] and r["zh"]]


def compact_lines(rows):
    """一行一詞，依英文排序。

    同一個英文有多列、而且譯法不同時（例如 Cartons 拆成「數量」「單位」兩列）附上第一個 i18n key，
    否則分不出來；譯法完全一樣的重複列（例如 Merchant SKU 有兩列相同的）只列一行。
    """
    usable = sorted(compact_usable(rows), key=lambda r: (r["en"].lower(), r["key"]))
    variants = {}
    for r in usable:
        variants.setdefault(r["en"].lower(), set()).add((r["zh"], r["tw"], r["kind"]))
    lines, printed = [COMPACT_HEADER], set()
    for r in usable:
        cells = [r["en"], r["zh"], r["tw"] or "（繁中未定）", r["kind"] or "—"]
        line = " | ".join(_gc_cell(c) for c in cells)
        if len(variants[r["en"].lower()]) > 1 and r["key"]:
            first_key = r["key"].split("、")[0].split(" ")[0]
            line += " | 用於 " + _gc_cell(first_key)
        if line not in printed:
            printed.add(line)
            lines.append(line)
    return lines


def compact_summary(rows, updated_at):
    usable = compact_usable(rows)
    skipped = sum(1 for r in rows if r["ok"] and not r["zh"])
    text = f"最後更新：{updated_at}｜已確認 {len(usable)} 詞（完整表共 {len(rows)} 列）"
    if skipped:
        text += f"｜另有 {skipped} 個已確認但沒有簡中的詞未收"
    return text + "｜由完整術語表自動產生，請勿手動編輯"


def compact_chunks(text, size=2000):
    """切成 Notion 單段 rich_text 的上限（2000 字）。盡量在換行處切，單行超長才硬切。"""
    chunks, buf = [], ""
    for i, line in enumerate(text.split("\n")):
        # 換行符跟著下一行走：各段直接接起來就和原文一模一樣
        piece = line if i == 0 else "\n" + line
        if buf and len(buf) + len(piece) > size:
            chunks.append(buf)
            buf = ""
        buf += piece
        while len(buf) > size:
            chunks.append(buf[:size])
            buf = buf[size:]
    if buf:
        chunks.append(buf)
    return chunks
