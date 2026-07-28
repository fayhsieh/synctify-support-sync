"""
相依面測試：核心轉換只能依賴 `re`。

n8n v2（我們的實例為 2.25.7）的 Python task runner **預設封鎖所有 import**，
包含標準函式庫；要放行必須由維運端設定 `N8N_RUNNERS_STDLIB_ALLOW`。
把相依壓到只剩 `re`，可讓 allowlist 的要求降到最小（`N8N_RUNNERS_STDLIB_ALLOW=re`），
也讓容器路徑的映像更精簡。

這支測試在「只允許 re」的沙箱裡實際跑一次完整轉換，防止日後不小心又加回相依。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_import_surface.py -v
"""
import builtins
import pathlib
import sys

import pytest

CONVERTER_DIR = pathlib.Path(__file__).resolve().parent
REPO = CONVERTER_DIR.parent

MODULES = ["notion2elementor.py", "notion_blocks.py"]


@pytest.mark.parametrize("filename", MODULES)
def test_module_toplevel_imports_only_re(filename):
    """模組頂層不得 import 除 `re` 以外的東西（其餘需延遲載入或放在 CLI 區塊）。"""
    src = (CONVERTER_DIR / filename).read_text(encoding="utf-8")
    toplevel = [ln.strip() for ln in src.splitlines()
                if ln.startswith("import ") or ln.startswith("from ")]
    assert toplevel == ["import re"], f"{filename} 頂層 import 應只有 re，實際：{toplevel}"


def _run_in_sandbox(filename, allow):
    """在只允許 `allow` 內模組的 import 沙箱中載入模組，回傳其命名空間。"""
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        root = name.split(".")[0]
        if root not in allow and root not in sys.builtin_module_names:
            raise ImportError(f"BLOCKED: {name}（模擬 n8n v2 runner 封鎖 import）")
        return real_import(name, *a, **k)

    src = (CONVERTER_DIR / filename).read_text(encoding="utf-8")
    ns = {"__name__": "sandboxed_module"}   # 非 __main__，跳過 CLI 區塊
    builtins.__import__ = guarded
    try:
        exec(compile(src, filename, "exec"), ns)
    finally:
        builtins.__import__ = real_import
    return ns


def test_convert_runs_with_only_re_allowed():
    """在只允許 re 的沙箱下，完整轉換真實樣本仍成功且結果不變。"""
    ns = _run_in_sandbox("notion2elementor.py", allow={"re"})
    md = (REPO / "samples" / "amazon-sc-v2-notion.md").read_text(encoding="utf-8")
    tpl, faqs, rep = ns["convert"](md, "Test", "test", sync_date="July 15, 2026")
    assert len(tpl["content"]) == 6
    assert rep["widgets"] == 55
    assert len(faqs) == 3


def test_blocks_to_markdown_runs_with_only_re_allowed():
    ns = _run_in_sandbox("notion_blocks.py", allow={"re"})
    blocks = [
        {"id": "a", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "T"}]}},
        {"id": "b", "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Body"}]}},
    ]
    md, report = ns["blocks_to_markdown"](blocks)
    assert "## T" in md and "Body" in md


def test_output_is_deterministic():
    """元素 ID 改用計數器後，同一份輸入應產生完全相同的輸出（可 diff）。"""
    sys.path.insert(0, str(CONVERTER_DIR))
    import notion2elementor as n2e

    md = (REPO / "samples" / "amazon-sc-v2-notion.md").read_text(encoding="utf-8")
    first = n2e.convert(md, "T", "t", sync_date="July 15, 2026")[0]
    second = n2e.convert(md, "T", "t", sync_date="July 15, 2026")[0]
    assert first == second
