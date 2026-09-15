"""產品用術語表的備份與還原。

備份（glossary_sync.py --write 寫入前也會自動備份一次）：
    ./.venv/bin/python scripts/glossary_backup.py

還原（預設只列出差異，加 --write 才寫回 Notion；寫回前會先備份當下狀態）：
    ./.venv/bin/python scripts/glossary_backup.py \\
        --restore backups/glossary/glossary-20260915-165300.json \\
        --rows "Products,Discard" --fields "简体中文,備註"

備份檔放在 GLOSSARY_BACKUP_DIR（環境變數或 .env），預設 backups/glossary/。
**這個 repo 是公開的**，術語表是公司內部資料（備註裡有內部討論與待修的產品問題），
所以 backups/ 已列入 .gitignore，不可提交。要雲端備份，把 GLOSSARY_BACKUP_DIR
指到 Google Drive／iCloud 的同步資料夾。
"""
import argparse
import datetime
import json
import os
import pathlib
import sys

import glossary_sync as gs
import wp_env

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "backups" / "glossary"
_KINDS = ("title", "rich_text", "number", "checkbox", "select", "multi_select")


def plain_value(prop):
    """Notion 屬性 → 可比對、可存檔的純值。"""
    kind = (prop or {}).get("type")
    if kind in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in prop.get(kind) or [])
    if kind == "number":
        return prop.get("number")
    if kind == "checkbox":
        return bool(prop.get("checkbox"))
    if kind == "select":
        return (prop.get("select") or {}).get("name")
    if kind == "multi_select":
        return sorted(o["name"] for o in prop.get("multi_select") or [])
    return None


def to_property(kind, value):
    """純值 → Notion 寫入格式。文字超過 2000 字要切段（API 單段上限）。

    只還原文字內容：粗體、連結等格式不會回來。
    """
    if kind in ("title", "rich_text"):
        text = value or ""
        return {kind: [{"text": {"content": text[i:i + 2000]}} for i in range(0, len(text), 2000)]}
    if kind == "number":
        return {"number": value}
    if kind == "checkbox":
        return {"checkbox": bool(value)}
    if kind == "select":
        return {"select": {"name": value} if value else None}
    if kind == "multi_select":
        return {"multi_select": [{"name": n} for n in value or []]}
    raise ValueError(f"不支援還原 {kind} 欄位")


def flatten(rows):
    """fetch_glossary 的列 → 備份用的扁平列，依 English 排序，人讀與比對都方便。"""
    out = []
    for r in rows:
        item = {"id": r["id"]}
        for name, prop in r["props"].items():
            if prop.get("type") in _KINDS:
                item[name] = plain_value(prop)
        out.append(item)
    return sorted(out, key=lambda x: ((x.get("English") or "").lower(), x["id"]))


def backup_dir():
    d = os.environ.get("GLOSSARY_BACKUP_DIR") or wp_env.read_env().get("GLOSSARY_BACKUP_DIR")
    return pathlib.Path(d).expanduser() if d else DEFAULT_DIR


def snapshot(rows, reason, directory=None, now=None):
    """把整張表存成一個 JSON 檔，回傳路徑。"""
    now = now or datetime.datetime.now()
    directory = pathlib.Path(directory) if directory else backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"glossary-{now:%Y%m%d-%H%M%S}.json"
    data = {"taken_at": now.isoformat(timespec="seconds"), "reason": reason,
            "database": gs.GLOSSARY_DB, "rows": flatten(rows)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def restore_plan(snap_rows, live_rows, names=None, fields=None):
    """算出還原要寫哪些欄位，回傳 (patches, deleted)。

    names：要還原的 English（不分大小寫；同名多列——例如拆成兩列的 Cartons——會一起還原）；None＝全部。
    fields：只還原這些欄位；None＝備份裡的所有欄位。
    patches：[(Notion 上的列, {欄位: (現值, 備份值, 型別)})]；deleted：備份裡有、Notion 已不存在的列。
    """
    wanted = {n.strip().lower() for n in names} if names else None
    live = {r["id"]: r for r in live_rows}
    patches, deleted = [], []
    for s in snap_rows:
        if wanted is not None and (s.get("English") or "").lower() not in wanted:
            continue
        row = live.get(s["id"])
        if not row:
            deleted.append(s)
            continue
        diff = {}
        for k, v in s.items():
            if k == "id" or (fields and k not in fields):
                continue
            prop = row["props"].get(k)
            if prop is None or prop.get("type") not in _KINDS:
                continue  # 欄位已被刪除或改名
            cur = plain_value(prop)
            if cur != v:
                diff[k] = (cur, v, prop["type"])
        if diff:
            patches.append((row, diff))
    return patches, deleted


def main():
    ap = argparse.ArgumentParser(description="備份／還原產品用術語表")
    ap.add_argument("--restore", metavar="備份檔", help="從備份檔還原（預設只列出差異）")
    ap.add_argument("--rows", help="要還原的 English，逗號分隔")
    ap.add_argument("--all-rows", action="store_true", help="還原備份裡的所有列（小心使用）")
    ap.add_argument("--fields", help="只還原這些欄位，逗號分隔；不給＝所有欄位")
    ap.add_argument("--write", action="store_true", help="實際寫回 Notion")
    args = ap.parse_args()

    token = wp_env.read_env().get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")
    live = gs.fetch_glossary(token)

    if not args.restore:
        path = snapshot(live, reason="手動備份")
        print(f"✓ 已備份 {len(live)} 列 → {path}")
        return 0

    if not args.rows and not args.all_rows:
        sys.exit('✗ 要指定 --rows（例：--rows "Products,Discard"）或 --all-rows')
    snap = json.loads(pathlib.Path(args.restore).read_text(encoding="utf-8"))
    names = None if args.all_rows else [n for n in args.rows.split(",") if n.strip()]
    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
    patches, deleted = restore_plan(snap["rows"], live, names, fields)

    print(f"備份時間 {snap['taken_at']}（{snap.get('reason', '')}）")
    if names:
        have = {(r.get("English") or "").lower() for r in snap["rows"]}
        missing = [n for n in names if n.strip().lower() not in have]
        if missing:
            print("⚠️ 備份裡沒有這些詞：" + "、".join(missing))
    for row, diff in patches:
        print(f"\n  {row['english']}")
        for k, (cur, old, _) in diff.items():
            print(f"      {k}: {cur!r} → {old!r}")
    for s in deleted:
        print(f"\n  ⚠️ {s.get('English')} 在 Notion 已不存在——請到 Notion 垃圾桶還原（30 天內），這支腳本不重建列")
    if not patches:
        print("\n沒有需要還原的差異")
        return 0
    if not args.write:
        print(f"\n這是 dry-run：{len(patches)} 列會被還原。確認後加 --write")
        return 0

    path = snapshot(live, reason=f"還原前（來源 {pathlib.Path(args.restore).name}）")
    print(f"\n🗂 還原前先備份目前狀態 → {path}")
    for row, diff in patches:
        props = {k: to_property(kind, old) for k, (_, old, kind) in diff.items()}
        gs.notion(f"/pages/{row['id']}", token, "PATCH", {"properties": props})
    print(f"✓ 已還原 {len(patches)} 列")
    return 0


if __name__ == "__main__":
    sys.exit(main())
