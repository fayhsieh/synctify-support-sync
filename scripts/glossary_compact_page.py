"""把 Notion「產品用術語表（翻譯用精簡版）」更新成完整術語表的最新狀態。

    ./.venv/bin/python scripts/glossary_compact_page.py            # 更新頁面
    ./.venv/bin/python scripts/glossary_compact_page.py --dry-run  # 只印出內容

精簡頁是給用 Notion 連接器翻譯的 Claude 讀的：只收「已確認」的詞、四個欄位，一次讀完。
**單向產生，不要手動編輯精簡頁**——要改譯法改完整術語表，再重新產生。

頁面結構（2026-09-15 建立）：
- 第一個以「最後更新：」開頭的段落 → 寫更新時間與詞數
- 第一個 code block → 詞表
只更新這兩個 block，說明 callout 不動。產生邏輯在 converter/glossary_compact.py（n8n 也可沿用）。
"""
import argparse
import datetime
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "converter"))
import glossary_compact as gc  # noqa: E402
import glossary_sync as gs  # noqa: E402
import wp_env  # noqa: E402

COMPACT_PAGE = "3dc2f2ed-e27d-8160-9ffa-e4e44ee1d02e"
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def find_blocks(children):
    """回傳 (摘要段落, 詞表 code block)；找不到就是 None。"""
    summary = code = None
    for b in children:
        kind = b.get("type")
        if kind == "paragraph" and summary is None:
            text = "".join(t.get("plain_text", "") for t in b["paragraph"].get("rich_text", []))
            if text.startswith("最後更新："):
                summary = b
        elif kind == "code" and code is None:
            code = b
    return summary, code


def main():
    ap = argparse.ArgumentParser(description="更新翻譯用精簡術語表")
    ap.add_argument("--dry-run", action="store_true", help="只印出內容，不寫入 Notion")
    args = ap.parse_args()

    token = wp_env.read_env().get("NOTION_API_KEY")
    if not token:
        sys.exit("✗ .env 缺 NOTION_API_KEY")
    page_id = os.environ.get("GLOSSARY_COMPACT_PAGE_ID") or COMPACT_PAGE

    rows = gc.compact_rows(gs.fetch_glossary(token))
    text = "\n".join(gc.compact_lines(rows))
    summary = gc.compact_summary(rows, datetime.datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"))
    chunks = gc.compact_chunks(text)
    print(summary)
    print(f"詞表 {len(text)} 字元，分 {len(chunks)} 段")
    if args.dry_run:
        print("\n" + text)
        return 0

    children = gs.notion(f"/blocks/{page_id}/children?page_size=100", token)["results"]
    para, code = find_blocks(children)
    if not para or not code:
        sys.exit("✗ 精簡頁結構不對：要有一段以「最後更新：」開頭的文字和一個 code block。"
                 "頁面被改過的話，照 docstring 的結構補回來")
    if len(chunks) > 100:
        sys.exit(f"✗ 詞表分成 {len(chunks)} 段，超過 Notion 單一 block 的 100 段上限，要改成多個 code block")

    gs.notion(f"/blocks/{code['id']}", token, "PATCH", {"code": {
        "language": "plain text",
        "rich_text": [{"type": "text", "text": {"content": c}} for c in chunks],
    }})
    gs.notion(f"/blocks/{para['id']}", token, "PATCH", {"paragraph": {
        "rich_text": [{"type": "text", "text": {"content": summary}}],
    }})
    print(f"✓ 已更新 https://app.notion.com/p/{page_id.replace('-', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
