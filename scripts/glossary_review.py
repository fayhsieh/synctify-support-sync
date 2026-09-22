"""術語審核區（本機版）：同步待確認／推送回完整表。

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

WORK_PAGE = "3dc2f2ede27d80d9aa01cf56910ec8b1"     # 「術語審核區」：按鈕、審核區檢視、頁首狀態列（Fay 2026-09-15 搬過來）
LOG_PAGE = "3dc2f2ede27d81609ffae4e44ee1d02e"      # 「術語審核區推送紀錄」：說明與推送紀錄；審核區資料庫掛在這頁底下
REVIEW_DB = "0caf57e29f4a4831b93b7c5766a97fa4"     # 待確認詞彙（審核區）；術語審核區頁上是它的連結檢視
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
    ap = argparse.ArgumentParser(description="術語審核區／OMS 模組術語：同步待確認／推送回完整表")
    ap.add_argument("action", choices=["pull", "push"], help="pull＝同步待確認、push＝推送回完整表")
    ap.add_argument("--write", action="store_true", help="實際寫入 Notion（預設只列出會做什麼）")
    ap.add_argument("--features", help="OMS 功能模式：只收這些功能的詞（逗號分隔，名稱以 OMS DB 為準）。"
                                       "收全部的詞（含已確認）且推送後不移出")
    ap.add_argument("--page", help="Glossary 頁 id（狀態列寫這頁）；不給就用 Marketing 的兩頁")
    ap.add_argument("--log-page", help="推送紀錄寫哪一頁；不給就寫在 --page 那頁")
    ap.add_argument("--review-db", help="該頁底下的術語資料庫 id；不給就用 Marketing 的審核區")
    args = ap.parse_args()
    if args.features and not (args.page and args.review_db):
        ap.error("--features 要一起給 --page 與 --review-db（Glossary 頁與那頁底下的術語資料庫）")

    token = wp_env.read_env().get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")

    features = [f.strip() for f in args.features.split(",") if f.strip()] if args.features else None
    review_db = args.review_db or REVIEW_DB
    work_page = args.page or WORK_PAGE
    log_page = args.log_page or args.page or LOG_PAGE
    full = query_all(gs.GLOSSARY_DB, token)
    review = query_all(review_db, token)
    kids = children_of(work_page, token)
    now = datetime.datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")
    if args.action == "pull":
        # 功能文件收該功能全部的詞（含已確認），它同時是交付給工程的清單
        plan = gr.review_pull_plan(full, review, kids, review_db, now,
                                   features=features, pending_only=not features,
                                   label="從完整表同步" if features else "同步待確認")
    else:
        plan = gr.review_push_plan(full, review, kids, log_page, now,
                                   archive_confirmed=not features)

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
