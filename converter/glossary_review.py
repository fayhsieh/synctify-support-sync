"""術語審核區：從完整術語表「取出待確認」，審核後「推送回完整表」。

流程（Fay／心柔 2026-09-15）：心柔請 Claude 翻譯待確認的詞。完整表經 Notion API 讀取很慢，
所以把還沒勾「已確認」的列複製到審核區（另一個資料庫，只放待確認的詞，備註一起帶過去），
改好後手動按「推送回完整表」寫回。不做排程、不做改了就同步。

- 取出是「複製」不是搬走：翻譯前的術語閘門、同步時建新詞都讀完整表，搬走會被當成新詞重建。
- 已在審核區的列不覆蓋（心柔改到一半的內容要保留）。
- 推送只寫 REVIEW_PUSH_FIELDS；參考欄位（一致性、OMS v0 現況、文件現況）不寫回。
- 有改動的列都寫回；勾了「已確認」的寫回後移出審核區（封存，Notion 垃圾桶 30 天內可還原）。
- 衝突：某欄在取出後被人在完整表改過、審核區的值又跟完整表現在不同 → 整列不寫，標在「推送狀態」。
  審核區的值剛好等於完整表現況（例如上次推送寫到一半）不算衝突，所以推送可以重按。
- 推送紀錄記下每列每欄的原值與新值，附在審核頁底部，改壞了照著改回。

純函式、不 import 任何模組：本機腳本（scripts/glossary_review.py）與 n8n 的 Python Code node 共用。
計畫回傳 phases——幾組依序執行的 Notion API 呼叫 {method, path, body}；前一組全部成功才執行下一組
（完整表還沒寫成功，就不能先把審核區的列移出）。
"""

REVIEW_PUSH_FIELDS = ["English", "简体中文", "繁體中文", "類型", "備註", "已確認"]
REVIEW_REF_FIELDS = ["一致性", "OMS v0 現況", "文件現況"]
REVIEW_SOURCE = "完整表列"
REVIEW_SNAPSHOT = "取出時內容"
REVIEW_STATUS = "推送狀態"
REVIEW_SUMMARY_PREFIX = "最後動作："
REVIEW_PAGE_URL_PREFIX = "https://app.notion.com/p/"

_GR_KINDS = {"English": "title", "简体中文": "rich_text", "繁體中文": "rich_text", "類型": "select",
             "備註": "rich_text", "已確認": "checkbox", "一致性": "select",
             "OMS v0 現況": "rich_text", "文件現況": "rich_text"}


# ── 讀值 ─────────────────────────────────────────────────────────────

def review_norm_id(value):
    """頁面 id 或網址 → 32 位小寫十六進位；認不出來回空字串。id 在網址最後面，取最後 32 個十六進位字元。"""
    text = (value or "").lower().split("?")[0].split("#")[0]
    hexes = "".join(c for c in text if c in "0123456789abcdef")
    return hexes[-32:] if len(hexes) >= 32 else ""


def _gr_value(prop):
    kind = (prop or {}).get("type")
    if kind in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in prop.get(kind) or [])
    if kind == "select":
        return (prop.get("select") or {}).get("name") or ""
    if kind == "checkbox":
        return bool(prop.get("checkbox"))
    if kind == "url":
        return prop.get("url") or ""
    return ""


def review_values(page):
    """完整表或審核區的 Notion page → 純值 dict（缺的欄位是空字串，已確認一律是 bool）。"""
    props = page.get("properties") or {}
    out = {"id": review_norm_id(page.get("id", ""))}
    for name in REVIEW_PUSH_FIELDS + REVIEW_REF_FIELDS + [REVIEW_SOURCE, REVIEW_SNAPSHOT, REVIEW_STATUS]:
        out[name] = _gr_value(props.get(name))
    out["已確認"] = bool(out["已確認"])
    return out


def _gr_alive(pages):
    return [p for p in pages if not p.get("archived") and not p.get("in_trash")]


# ── 取出時內容（快照）────────────────────────────────────────────────
# 不能用 json（n8n Python 只允許 re），自己用「欄位<Tab>值」一行一欄，值裡的換行、Tab、反斜線跳脫。

def _gr_escape(text):
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")


def _gr_unescape(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def encode_snapshot(values):
    lines = []
    for field in REVIEW_PUSH_FIELDS:
        value = values.get(field)
        if field == "已確認":
            value = "1" if value else "0"
        lines.append(field + "\t" + _gr_escape(value or ""))
    return "\n".join(lines)


def decode_snapshot(text):
    """解不出來（欄位缺漏，多半是被手動改壞）回 None。"""
    got = {}
    for line in (text or "").split("\n"):
        if "\t" in line:
            key, value = line.split("\t", 1)
            got[key] = _gr_unescape(value)
    if any(field not in got for field in REVIEW_PUSH_FIELDS):
        return None
    out = {field: got[field] for field in REVIEW_PUSH_FIELDS}
    out["已確認"] = got["已確認"] == "1"
    return out


# ── 寫值 ─────────────────────────────────────────────────────────────

def _gr_rich(text):
    """Notion 單段 rich_text 上限 2000 字，切段。"""
    text = text or ""
    return [{"type": "text", "text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)]


def _gr_prop(kind, value):
    if kind == "title":
        return {"title": _gr_rich(value)}
    if kind == "rich_text":
        return {"rich_text": _gr_rich(value)}
    if kind == "select":
        return {"select": {"name": value} if value else None}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    if kind == "url":
        return {"url": value or None}
    raise ValueError("不支援的欄位型別：" + str(kind))


def _gr_status_op(row, message):
    return {"method": "PATCH", "path": "/pages/" + row["id"],
            "body": {"properties": {REVIEW_STATUS: _gr_prop("rich_text", message)}},
            "note": "推送狀態：" + row["English"]}


def review_summary_block(children):
    """審核頁上以「最後動作：」開頭的段落 id；找不到回 None（那就不寫狀態列）。"""
    for block in children:
        if block.get("type") == "paragraph":
            text = "".join(t.get("plain_text", "") for t in block["paragraph"].get("rich_text", []))
            if text.startswith(REVIEW_SUMMARY_PREFIX):
                return block.get("id")
    return None


def _gr_summary_ops(block_id, summary):
    if not block_id:
        return []
    return [{"method": "PATCH", "path": "/blocks/" + block_id,
             "body": {"paragraph": {"rich_text": _gr_rich(summary)}}, "note": "狀態列"}]


def _gr_sort_key(row):
    return (row["English"].lower(), row["id"])


# ── 取出待確認 ───────────────────────────────────────────────────────

def review_pull_plan(full_pages, review_pages, page_children, review_db, now):
    """完整表未確認、審核區還沒有的列 → 在審核區建立。已在審核區的列不覆蓋，只標出來源有異狀的。"""
    full = [review_values(p) for p in _gr_alive(full_pages)]
    review = [review_values(p) for p in _gr_alive(review_pages)]
    full_by_id = {row["id"]: row for row in full}
    in_review = {review_norm_id(row[REVIEW_SOURCE]) for row in review}

    creates = []
    for row in sorted(full, key=_gr_sort_key):
        if row["已確認"] or row["id"] in in_review:
            continue
        props = {name: _gr_prop(_GR_KINDS[name], row[name])
                 for name in REVIEW_PUSH_FIELDS + REVIEW_REF_FIELDS}
        props[REVIEW_SOURCE] = _gr_prop("url", REVIEW_PAGE_URL_PREFIX + row["id"])
        props[REVIEW_SNAPSHOT] = _gr_prop("rich_text", encode_snapshot(row))
        creates.append({"method": "POST", "path": "/pages",
                        "body": {"parent": {"database_id": review_db}, "properties": props},
                        "note": "取出：" + row["English"]})

    flags = []
    for row in review:
        source = full_by_id.get(review_norm_id(row[REVIEW_SOURCE]))
        if source is None:
            message = "⚠️ 完整表已經沒有這一列（可能被刪除或合併），不會推送；確認後可以刪掉這列"
        elif source["已確認"] and not row["已確認"]:
            message = "⚠️ 完整表這一列已經被勾「已確認」，這裡的修改推送時會被當成衝突"
        else:
            continue
        if row[REVIEW_STATUS] != message:
            flags.append(_gr_status_op(row, message))

    total = len(review) + len(creates)
    summary = f"{REVIEW_SUMMARY_PREFIX}{now} 取出待確認｜新增 {len(creates)} 列｜審核區共 {total} 列"
    if flags:
        summary += f"｜{len(flags)} 列需要注意（看「推送狀態」）"
    return {"action": "pull", "summary": summary,
            "counts": {"created": len(creates), "flagged": len(flags), "total": total},
            "phases": [creates + flags, _gr_summary_ops(review_summary_block(page_children), summary)]}


# ── 推送回完整表 ─────────────────────────────────────────────────────

def _gr_show(field, value):
    if field == "已確認":
        return "☑ 已確認" if value else "☐ 未確認"
    return value if value else "（空白）"


def review_log_blocks(headline, entries, per_toggle=90):
    """推送紀錄：每次推送一個 toggle，裡面一列一個項目，記下每欄的原值 → 新值（完整內容，不截斷）。

    entries：[(English, [(欄位, 原值, 新值), ...]), ...]。一個 toggle 最多放 per_toggle 項（API 上限 100）。
    """
    if not entries:
        return []
    parts = [entries[i:i + per_toggle] for i in range(0, len(entries), per_toggle)]
    blocks = []
    for n, part in enumerate(parts, 1):
        title = headline + (f"（第 {n}／{len(parts)} 段）" if len(parts) > 1 else "")
        items = []
        for english, changes in part:
            rich = [{"type": "text", "text": {"content": english}, "annotations": {"bold": True}}]
            for field, before, after in changes:
                rich += _gr_rich(f"\n{field}：{_gr_show(field, before)} → {_gr_show(field, after)}")
            items.append({"object": "block", "type": "bulleted_list_item",
                          "bulleted_list_item": {"rich_text": rich[:100]}})
        blocks.append({"object": "block", "type": "toggle",
                       "toggle": {"rich_text": _gr_rich(title), "children": items}})
    return blocks


def review_push_plan(full_pages, review_pages, page_children, review_page_id, now):
    full_by_id = {row["id"]: row for row in (review_values(p) for p in _gr_alive(full_pages))}
    review = sorted((review_values(p) for p in _gr_alive(review_pages)), key=_gr_sort_key)

    full_ops, review_ops, entries = [], [], []
    pushed = confirmed = conflicts = missing = broken = 0
    for row in review:
        source = full_by_id.get(review_norm_id(row[REVIEW_SOURCE]))
        snapshot = decode_snapshot(row[REVIEW_SNAPSHOT])
        if source is None:
            missing += 1
            message = "⚠️ 完整表已經沒有這一列（可能被刪除或合併），沒有推送"
            if row[REVIEW_STATUS] != message:
                review_ops.append(_gr_status_op(row, message))
            continue
        if snapshot is None:
            broken += 1
            message = "⚠️「取出時內容」被改過，無法判斷完整表有沒有被別人改，沒有推送。刪掉這列再按「取出待確認」"
            if row[REVIEW_STATUS] != message:
                review_ops.append(_gr_status_op(row, message))
            continue

        clash = [f for f in REVIEW_PUSH_FIELDS if source[f] != snapshot[f] and row[f] != source[f]]
        if clash:
            conflicts += 1
            message = ("⚠️ 完整表在取出後被改過（" + "、".join(clash) + "），這列沒有推送。"
                       "對照完整表後，刪掉這列再按「取出待確認」")
            if row[REVIEW_STATUS] != message:
                review_ops.append(_gr_status_op(row, message))
            continue

        writes = [f for f in REVIEW_PUSH_FIELDS if row[f] != source[f]]
        if writes:
            pushed += 1
            full_ops.append({"method": "PATCH", "path": "/pages/" + source["id"],
                             "body": {"properties": {f: _gr_prop(_GR_KINDS[f], row[f]) for f in writes}},
                             "note": "寫回：" + row["English"]})
            entries.append((row["English"], [(f, source[f], row[f]) for f in writes]))

        if row["已確認"]:
            confirmed += 1
            review_ops.append({"method": "PATCH", "path": "/pages/" + row["id"],
                               "body": {"archived": True}, "note": "移出審核區：" + row["English"]})
        elif writes or any(source[f] != snapshot[f] for f in REVIEW_PUSH_FIELDS):
            # 寫回後完整表＝審核區的值，快照要跟著更新，下次推送才不會誤判成衝突
            props = {REVIEW_SNAPSHOT: _gr_prop("rich_text", encode_snapshot(row))}
            if writes:
                props[REVIEW_STATUS] = _gr_prop("rich_text", f"✓ {now} 已推送：" + "、".join(writes))
            review_ops.append({"method": "PATCH", "path": "/pages/" + row["id"],
                               "body": {"properties": props}, "note": "更新快照：" + row["English"]})

    summary = f"{REVIEW_SUMMARY_PREFIX}{now} 推送回完整表｜寫回 {pushed} 列｜{confirmed} 列已確認、移出審核區"
    if not pushed and not confirmed:
        summary += "｜沒有需要寫回的改動"
    problems = conflicts + missing + broken
    if problems:
        summary += f"｜{problems} 列沒有推送（看「推送狀態」）"

    final_ops = []
    headline = f"{now}｜寫回 {pushed} 列、移出 {confirmed} 列"
    log = review_log_blocks(headline, entries)
    if log:
        final_ops.append({"method": "PATCH", "path": "/blocks/" + review_page_id + "/children",
                          "body": {"children": log}, "note": "推送紀錄"})
    final_ops += _gr_summary_ops(review_summary_block(page_children), summary)

    return {"action": "push", "summary": summary,
            "counts": {"pushed": pushed, "confirmed": confirmed, "conflicts": conflicts,
                       "missing": missing, "broken": broken},
            "phases": [full_ops, review_ops, final_ops]}
