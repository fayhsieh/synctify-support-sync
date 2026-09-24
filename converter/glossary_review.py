"""術語審核區：從完整術語表「同步待確認」到審核區，審核後「推送回完整表」。

流程（Fay／心柔 2026-09-15）：心柔請 Claude 翻譯待確認的詞。完整表經 Notion API 讀取很慢，
所以把還沒勾「已確認」的列複製到審核區（另一個資料庫，只放待確認的詞，備註一起帶過去），
改好後手動按「推送回完整表」寫回。不做排程、不做改了就同步。

- 同步待確認是「複製」不是搬走（按鈕原名「取出待確認」，Fay 2026-09-15 改名）：翻譯前的術語閘門、同步時建新詞都讀完整表，搬走會被當成新詞重建。
- 已在審核區的列不整列覆蓋（改到一半的內容要保留），但**沒被動過的欄位會跟著完整表更新**：
  值還等於「取出時內容」就代表這裡沒人改，完整表那邊卻變了（別的地方決定完推回去的），
  這時跟著走比留著舊值有用——不然功能文件會一直停在取出當下的狀態。被改過的欄位完全不碰。
- 另外也收完整表上勾了「待複審」的列（即使已確認）：跨模組用詞要心柔一起決定，但取消「已確認」
  會讓那些詞退出術語閘門、譯法被重跑，所以改用旗標。這些列到審核區時「已確認」先取消勾選——
  審核區的打勾意思是「複審過了」，勾了推送回去才會清掉旗標、把列移出。審核區永遠不會把完整表
  的「已確認」取消掉（要退回未確認請直接在完整表改）。
- 推送只寫 REVIEW_PUSH_FIELDS；參考欄位（一致性、OMS v0 現況、文件現況）不寫回。
- 有改動的列都寫回；勾了「已確認」的寫回後移出審核區（封存，Notion 垃圾桶 30 天內可還原）。
- 衝突：某欄在同步到審核區後被人在完整表改過、審核區的值又跟完整表現在不同 → 整列不寫，標在「推送狀態」。
  審核區的值剛好等於完整表現況（例如上次推送寫到一半）不算衝突，所以推送可以重按。
- 推送紀錄記下每列每欄的原值與新值，附在審核頁底部，改壞了照著改回。

純函式、不 import 任何模組：本機腳本（scripts/glossary_review.py）與 n8n 的 Python Code node 共用。
計畫回傳 phases——幾組依序執行的 Notion API 呼叫 {method, path, body}；前一組全部成功才執行下一組
（完整表還沒寫成功，就不能先把審核區的列移出）。
"""

REVIEW_PUSH_FIELDS = ["English", "简体中文", "繁體中文", "類型", "備註", "已確認"]
REVIEW_REF_FIELDS = ["一致性", "OMS v0 現況", "文件現況", "模組", "功能", "審核群組"]
# 「審核群組」是 Marketing 這一輪的臨時欄位，OMS 功能文件沒有這一欄。Notion 對不存在的屬性
# 一律回 400（整批操作全滅），所以功能文件模式不送它——決定完要刪這欄時也不會波及功能文件。
REVIEW_MARKETING_ONLY = ["審核群組"]
# 完整表上的旗標：勾起來的列即使已確認也要進審核區（跨模組用詞要心柔一起決定，Fay 2026-09-23）。
# 心柔確認並推送回來後由推送清掉。決定完可以整欄刪除——讀不到就當成沒勾，流程照跑。
REVIEW_FLAG = "待複審"
REVIEW_SOURCE = "完整表列"
REVIEW_SNAPSHOT = "取出時內容"
REVIEW_STATUS = "推送狀態"
REVIEW_SUMMARY_PREFIX = "最後動作："
REVIEW_PAGE_URL_PREFIX = "https://app.notion.com/p/"

_GR_KINDS = {"English": "title", "简体中文": "rich_text", "繁體中文": "rich_text", "類型": "select",
             "備註": "rich_text", "已確認": "checkbox", "一致性": "select",
             "OMS v0 現況": "rich_text", "文件現況": "rich_text",
             # 審核群組是 multi_select：一列可能同時撞到兩個議題（3PL Orders 既是中英文空格、
             # 又是選單名要不要加「管理」），用單選會漏掉其中一個（Fay 2026-09-23）
             "模組": "multi_select", "功能": "multi_select", "審核群組": "multi_select",
             REVIEW_FLAG: "checkbox"}


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
    if kind == "multi_select":
        return sorted(o["name"] for o in prop.get("multi_select") or [])
    return ""


def review_values(page):
    """完整表或審核區的 Notion page → 純值 dict（缺的欄位是空字串，已確認是 bool、模組是 list）。"""
    props = page.get("properties") or {}
    out = {"id": review_norm_id(page.get("id", ""))}
    for name in (REVIEW_PUSH_FIELDS + REVIEW_REF_FIELDS
                 + [REVIEW_SOURCE, REVIEW_SNAPSHOT, REVIEW_STATUS, REVIEW_FLAG]):
        out[name] = _gr_value(props.get(name))
    out["已確認"] = bool(out["已確認"])
    out[REVIEW_FLAG] = bool(out[REVIEW_FLAG])
    for name in ("模組", "功能", "審核群組"):     # 欄位不存在時 _gr_value 回空字串
        if not isinstance(out[name], list):
            out[name] = []
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
    if kind == "multi_select":
        return {"multi_select": [{"name": name} for name in value or []]}
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


# ── 在審核區建列（同步待確認按鈕、同步 WP 時的新詞共用）───────────────────────────────

def _gr_present_fields(pages):
    """目標資料庫實際有哪些欄位——從既有的列推得。空的資料庫推不出來，回 None＝不過濾。

    Notion 只要收到一個不存在的屬性就整個呼叫 400，而各功能文件的欄位不見得一樣
    （例如審核群組只有 Marketing 審核區有），所以送出去之前先濾掉對方沒有的欄位。
    """
    present = set()
    for page in _gr_alive(pages):
        present |= set((page.get("properties") or {}).keys())
    return present or None


def _gr_create_op(row, review_db, note_prefix, uncheck_flagged=False, fields=None):
    """完整表的一列 → 在審核區建同樣內容的一列。快照＝完整表這一列的值。

    uncheck_flagged：待複審的列在完整表本來就勾著「已確認」，照抄過去審核區會變成
    「還沒看就已經是已確認」，第一次推送就把整批列清掉。所以待辦清單（Marketing 審核區）
    建列時先取消打勾——審核區的打勾意思是「心柔複審過了」，勾了才會清旗標、移出審核區。
    快照仍然是完整表的原值（已確認），推送時不會把這個未勾狀態寫回去。
    """
    props = {name: _gr_prop(_GR_KINDS[name], row[name])
             for name in (fields if fields is not None else REVIEW_PUSH_FIELDS + REVIEW_REF_FIELDS)}
    if uncheck_flagged and row[REVIEW_FLAG] and row["已確認"]:
        props["已確認"] = _gr_prop("checkbox", False)
    props[REVIEW_SOURCE] = _gr_prop("url", REVIEW_PAGE_URL_PREFIX + row["id"])
    props[REVIEW_SNAPSHOT] = _gr_prop("rich_text", encode_snapshot(row))
    return {"method": "POST", "path": "/pages",
            "body": {"parent": {"database_id": review_db}, "properties": props},
            "note": note_prefix + row["English"]}


def review_create_ops(full_pages, review_db):
    """同步把新詞建進完整表後，用 API 回傳的那一列在審核區建同樣的一列（Fay 2026-09-15）。

    快照取自回傳內容——也就是完整表實際存下的值——所以推送時不會被判成衝突。
    這些列剛建立，審核區不可能已經有，不必查重複。建列失敗的回應（沒有 id）與已確認的列都不建。
    """
    rows = sorted((review_values(p) for p in _gr_alive(full_pages) if p.get("object", "page") == "page"),
                  key=_gr_sort_key)
    return [_gr_create_op(row, review_db, "同步新詞：") for row in rows if row["id"] and not row["已確認"]]


# ── 同步待確認（按鈕）───────────────────────────────────────────────────────

def review_pull_plan(full_pages, review_pages, page_children, review_db, now,
                     features=None, pending_only=True, label="同步待確認"):
    """完整表的列 → 在審核區／功能文件建立還沒有的那些。已經有的列不覆蓋，只標出來源有異狀的。

    features：只收「功能」與這份清單有交集的列。功能是詞彙表的最小單位（Fay 2026-09-22），
    名稱以 OMS DB 上的頁面為準；一頁可以列多個（例：Sales Orders 與該模組的共用詞）。
    每個功能一個**獨立資料庫**——篩選檢視擋不住工程師用 AI 讀到別的功能的詞。None＝全收（Marketing 審核區）。
    pending_only：True＝只收還沒勾「已確認」的（待辦清單）；False＝全部的詞（給工程的完整清單）。
    label：按鈕名稱，寫進狀態列與每個操作的說明。Marketing 是「同步待確認」；
    OMS 的 Glossary 收全部的詞，叫「同步待確認」會誤導，用「從完整表同步」（Fay 2026-09-22）。
    """
    full = [review_values(p) for p in _gr_alive(full_pages)]
    review = [review_values(p) for p in _gr_alive(review_pages)]
    full_by_id = {row["id"]: row for row in full}
    in_review = {review_norm_id(row[REVIEW_SOURCE]) for row in review}

    # 功能文件沒有 Marketing 這一輪的臨時欄位；再濾掉目標資料庫實際沒有的欄位（各文件不見得一樣）。
    # Notion 只要收到一個不存在的屬性就整個呼叫 400，這一關沒守住會整批失敗。
    present = _gr_present_fields(review_pages)
    def usable(names):
        return [f for f in names
                if not (features and f in REVIEW_MARKETING_ONLY) and (present is None or f in present)]
    ref_fields = usable(REVIEW_REF_FIELDS)

    def wanted(row):
        if row["id"] in in_review:
            return False
        # 勾了「待複審」的列即使已確認也要收：跨模組用詞要心柔一起看（Fay 2026-09-23）
        if pending_only and row["已確認"] and not row[REVIEW_FLAG]:
            return False
        return not features or any(f in row["功能"] for f in features)

    creates = [_gr_create_op(row, review_db, label + "：", uncheck_flagged=pending_only,
                             fields=usable(REVIEW_PUSH_FIELDS + REVIEW_REF_FIELDS))
               for row in sorted(full, key=_gr_sort_key) if wanted(row)]

    flags, refresh, synced = [], [], 0
    for row in review:
        source = full_by_id.get(review_norm_id(row[REVIEW_SOURCE]))
        if source is not None:
            # 參考欄位（一致性、模組、審核群組…）是腳本算出來的，不是人改的內容，一律跟完整表對齊
            props = {f: _gr_prop(_GR_KINDS[f], source[f])
                     for f in ref_fields if row[f] != source[f]}
            # 推送欄位只有「整列都還等於取出時的快照」才跟著完整表走——那代表這裡沒人動過，
            # 而完整表已經變了（多半是別的地方決定完推回去的）。**只要有一欄被改過就整列不碰**：
            # 那一列正在等著推送回去，這時候動它任何一欄（包括把「已確認」蓋成 true）
            # 都可能讓未推送的修改被當成已定案的內容寫回完整表。
            snapshot = decode_snapshot(row[REVIEW_SNAPSHOT])
            untouched = snapshot is not None and all(row[f] == snapshot[f] for f in REVIEW_PUSH_FIELDS)
            fresh = [f for f in usable(REVIEW_PUSH_FIELDS) if row[f] != source[f]] if untouched else []
            if fresh:
                props.update({f: _gr_prop(_GR_KINDS[f], source[f]) for f in fresh})
                props[REVIEW_SNAPSHOT] = _gr_prop("rich_text", encode_snapshot(source))
                synced += 1
            if props:
                refresh.append({"method": "PATCH", "path": "/pages/" + row["id"],
                                "body": {"properties": props},
                                "note": ("跟上完整表：" if fresh else "更新參考欄位：") + row["English"]})
        if source is None:
            message = "⚠️ 完整表已經沒有這一列（可能被刪除或合併），不會推送；確認後可以刪掉這列"
        elif fresh:
            continue        # 這一列剛跟完整表對齊過，沒有東西要提醒
        elif pending_only and source["已確認"] and not source[REVIEW_FLAG] and not row["已確認"]:
            # 功能文件本來就收已確認的列、待複審的列也是刻意收進來的，這提醒只對待辦清單有意義
            message = "⚠️ 完整表這一列已經被勾「已確認」，這裡的修改推送時會被當成衝突"
        else:
            continue
        if row[REVIEW_STATUS] != message:
            flags.append(_gr_status_op(row, message))

    total = len(review) + len(creates)
    scope = f"（{'、'.join(features)}）" if features else ""
    summary = f"{REVIEW_SUMMARY_PREFIX}{now} {label}{scope}｜新增 {len(creates)} 列｜共 {total} 列"
    if synced:
        summary += f"｜{synced} 列跟上完整表最新內容"
    if len(refresh) - synced:
        summary += f"｜{len(refresh) - synced} 列更新參考欄位"
    if flags:
        summary += f"｜{len(flags)} 列需要注意（看「推送狀態」）"
    return {"action": "pull", "summary": summary,
            "counts": {"created": len(creates), "refreshed": len(refresh), "synced": synced,
                       "flagged": len(flags), "total": total},
            "phases": [creates + refresh + flags,
                       _gr_summary_ops(review_summary_block(page_children), summary)]}


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


def review_push_plan(full_pages, review_pages, page_children, log_page_id, now,
                     archive_confirmed=True):
    """page_children：放「最後動作：」狀態列那一頁的區塊（術語審核區）；log_page_id：推送紀錄寫在哪一頁。

    2026-09-15 起兩者是不同頁：按鈕與審核區檢視在「術語審核區」，紀錄留在「術語審核區推送紀錄」。
    archive_confirmed：True＝勾了已確認的列推完移出（Marketing 的待辦清單）；
    False＝列留著（OMS 模組文件是該模組的完整清單，Fay 2026-09-22）。
    """
    full_by_id = {row["id"]: row for row in (review_values(p) for p in _gr_alive(full_pages))}
    review = sorted((review_values(p) for p in _gr_alive(review_pages)), key=_gr_sort_key)

    full_ops, review_ops, entries = [], [], []
    pushed = reviewed = confirmed = conflicts = missing = broken = 0
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
            message = "⚠️「取出時內容」被改過，無法判斷完整表有沒有被別人改，沒有推送。刪掉這列再按「同步待確認」"
            if row[REVIEW_STATUS] != message:
                review_ops.append(_gr_status_op(row, message))
            continue

        clash = [f for f in REVIEW_PUSH_FIELDS if source[f] != snapshot[f] and row[f] != source[f]]
        if clash:
            conflicts += 1
            message = ("⚠️ 完整表在同步到審核區後被改過（" + "、".join(clash) + "），這列沒有推送。"
                       "對照完整表後，刪掉這列再按「同步待確認」")
            if row[REVIEW_STATUS] != message:
                review_ops.append(_gr_status_op(row, message))
            continue

        writes = [f for f in REVIEW_PUSH_FIELDS if row[f] != source[f]]
        # 審核區不會把完整表的「已確認」取消掉：待複審的列取出時就是未勾的，那是「還沒複審」
        # 不是「取消確認」。要退回未確認請直接在完整表改（Fay 的規則：確認過的列不隨意改）
        if "已確認" in writes and source["已確認"] and not row["已確認"]:
            writes.remove("已確認")
        # 心柔確認完就把「待複審」清掉，那一列才會退出審核區（旗標不是人工內容，不算 push 欄位）
        clear_flag = source[REVIEW_FLAG] and row["已確認"]
        if writes or clear_flag:
            props = {f: _gr_prop(_GR_KINDS[f], row[f]) for f in writes}
            if clear_flag:
                props[REVIEW_FLAG] = _gr_prop("checkbox", False)
            full_ops.append({"method": "PATCH", "path": "/pages/" + source["id"],
                             "body": {"properties": props},
                             "note": "寫回：" + row["English"]})
        if writes:
            pushed += 1
            entries.append((row["English"], [(f, source[f], row[f]) for f in writes]))
        elif clear_flag:
            # 複審過但覺得原譯法就好：沒有欄位要寫，紀錄裡也要留一筆，才知道這列是看過才退出的
            reviewed += 1
            entries.append((row["English"], [(REVIEW_FLAG, "待複審", "複審通過，維持原譯")]))

        if row["已確認"]:
            confirmed += 1
        if row["已確認"] and archive_confirmed:
            review_ops.append({"method": "PATCH", "path": "/pages/" + row["id"],
                               "body": {"archived": True}, "note": "移出審核區：" + row["English"]})
        elif writes or any(source[f] != snapshot[f] for f in REVIEW_PUSH_FIELDS):
            # 寫回後完整表＝這一列的值，快照要跟著更新，下次推送才不會誤判成衝突
            props = {REVIEW_SNAPSHOT: _gr_prop("rich_text", encode_snapshot(row))}
            if writes:
                props[REVIEW_STATUS] = _gr_prop("rich_text", f"✓ {now} 已推送：" + "、".join(writes))
            review_ops.append({"method": "PATCH", "path": "/pages/" + row["id"],
                               "body": {"properties": props}, "note": "更新快照：" + row["English"]})

    summary = f"{REVIEW_SUMMARY_PREFIX}{now} 推送回完整表｜寫回 {pushed} 列"
    if reviewed:
        summary += f"｜{reviewed} 列複審通過（維持原譯）"
    if archive_confirmed:
        summary += f"｜{confirmed} 列已確認、移出審核區"
    if not pushed and not reviewed and not (archive_confirmed and confirmed):
        summary += "｜沒有需要寫回的改動"
    problems = conflicts + missing + broken
    if problems:
        summary += f"｜{problems} 列沒有推送（看「推送狀態」）"

    final_ops = []
    headline = (f"{now}｜寫回 {pushed} 列" + (f"、複審通過 {reviewed} 列" if reviewed else "")
                + (f"、移出 {confirmed} 列" if archive_confirmed else ""))
    log = review_log_blocks(headline, entries)
    if log:
        final_ops.append({"method": "PATCH", "path": "/blocks/" + log_page_id + "/children",
                          "body": {"children": log}, "note": "推送紀錄"})
    final_ops += _gr_summary_ops(review_summary_block(page_children), summary)

    return {"action": "push", "summary": summary,
            "counts": {"pushed": pushed, "reviewed": reviewed, "confirmed": confirmed,
                       "conflicts": conflicts, "missing": missing, "broken": broken},
            "phases": [full_ops, review_ops, final_ops]}
