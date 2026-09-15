"""術語審核區（本機版）：取出待確認／推送回完整表。

    ./.venv/bin/python scripts/glossary_review.py pull            # 只列出會做什麼
    ./.venv/bin/python scripts/glossary_review.py pull --write
    ./.venv/bin/python scripts/glossary_review.py push --write

平常由心柔在 Notion 審核頁按按鈕觸發 n8n，做的是同一件事；這支給測試，或 n8n 壞掉時備用。
判斷邏輯在 converter/glossary_review.py（與 n8n 共用）。推送前會先備份整張完整表。
"""
import argparse
import datetime
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "converter"))
import glossary_backup as gb  # noqa: E402
import glossary_review as gr  # noqa: E402
import glossary_sync as gs  # noqa: E402
import wp_env  # noqa: E402

REVIEW_PAGE = "3dc2f2ede27d81609ffae4e44ee1d02e"   # 審核頁（原「翻譯用精簡版」）
REVIEW_DB = "0caf57e29f4a4831b93b7c5766a97fa4"     # 待確認詞彙（審核區）
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def query_all(database_id, token):
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = gs.notion(f"/databases/{database_id}/query", token, "POST", body)
        pages += data["results"]
        if not data.get("has_more"):
            return pages
        cursor = data["next_cursor"]


def children_of(page_id, token):
    blocks, cursor = [], None
    while True:
        qs = "?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        data = gs.notion(f"/blocks/{page_id}/children{qs}", token)
        blocks += data["results"]
        if not data.get("has_more"):
            return blocks
        cursor = data["next_cursor"]


def main():
    ap = argparse.ArgumentParser(description="術語審核區：取出待確認／推送回完整表")
    ap.add_argument("action", choices=["pull", "push"], help="pull＝取出待確認、push＝推送回完整表")
    ap.add_argument("--write", action="store_true", help="實際寫入 Notion（預設只列出會做什麼）")
    args = ap.parse_args()

    token = wp_env.read_env().get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")

    full = query_all(gs.GLOSSARY_DB, token)
    review = query_all(REVIEW_DB, token)
    kids = children_of(REVIEW_PAGE, token)
    now = datetime.datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    if args.action == "pull":
        plan = gr.review_pull_plan(full, review, kids, REVIEW_DB, now)
    else:
        plan = gr.review_push_plan(full, review, kids, REVIEW_PAGE, now)

    print(f"完整表 {len(full)} 列、審核區 {len(review)} 列")
    print(plan["summary"])
    print("計數：", plan["counts"])
    for i, phase in enumerate(plan["phases"], 1):
        print(f"\n第 {i} 階段：{len(phase)} 個操作")
        for op in phase[:15]:
            print(f"   {op['method']:5s} {op['note']}")
        if len(phase) > 15:
            print(f"   …另外 {len(phase) - 15} 個")

    if not args.write:
        print("\n這是 dry-run。確認後加 --write")
        return 0

    if args.action == "push" and plan["phases"][0]:
        path = gb.snapshot([{"id": p["id"], "props": p["properties"]} for p in full],
                           reason="glossary_review push 推送前")
        print(f"\n🗂 推送前已備份完整表 → {path}")

    total = sum(len(p) for p in plan["phases"])
    done = 0
    for phase in plan["phases"]:
        for op in phase:
            gs.notion(op["path"], token, op["method"], op["body"])   # 失敗會直接結束，後面的階段不會跑
            done += 1
            if done % 20 == 0 or done == total:
                print(f"  已完成 {done}／{total}")
    print("✓ 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
