"""term_check 的測試。

同步時的術語檢查會自動在術語表建列、在文章留言——判斷錯了不會拋例外，
只會默默建出雜訊列或漏掉該補的詞。每個案例都對應 post 7622 的實際情況。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_term_check.py -v
"""
import builtins
import pathlib
import re
import sys

CONVERTER_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(CONVERTER_DIR))
import term_check as tc  # noqa: E402

# 同步時看到的（shortcode 未渲染）與站上頁面看到的（已渲染）
SC = "[direction]{}[/direction]"
STEP = ('<span class="direction_steps">\n            '
        '<span class="direction_step">{}</span>        </span>\n        ')


def labels(*texts):
    return tc.candidates(texts)


# ── 撈候選 ────────────────────────────────────────────────

def test_shortcode與已渲染兩種形態都收():
    a = labels("Go to " + SC.format("Integrations &gt; Integrated Message Codes") + ".")
    b = labels("Go to " + STEP.format("Integrations &gt; Integrated Message Codes") + ".")
    assert [v["label"] for v in a.values()] == ["Integrations", "Integrated Message Codes"]
    assert a.keys() == b.keys()


def test_粗體也是候選():
    got = labels("<strong>Inbound only</strong> — Synctify receives this code.")
    assert got["inbound only"]["kinds"] == {tc.BOLD}


def test_加號與結尾冒號去掉():
    got = labels("Click " + SC.format("+ Add Code"), "<strong>Direction:</strong> in")
    assert "add code" in got and "direction" in got


def test_實體還原():
    got = labels(SC.format("Preview &#038; Test"), SC.format("Preview &amp; Test"),
                 SC.format("Preview & Test"))
    assert list(got) == ["preview & test"] and got["preview & test"]["n"] == 3


def test_amp不會被解兩次():
    assert tc.unescape("a &amp;gt; b") == "a &gt; b"


def test_代碼值與整句不收():
    assert labels(SC.format("UPS_GR_RES"), "<strong>This cannot be undone.</strong>") == {}


def test_模板裡的字串遞迴撈出():
    tpl = {"content": [{"elements": [{"settings": {
        "editor": "<p>Click " + SC.format("Save Code") + "</p>",
        "tabs": [{"text": "<strong>System</strong>"}]}}]}]}
    got = tc.candidates(tc.strings_in(tpl))
    assert set(got) == {"save code", "system"}


# ── 對照術語表 ────────────────────────────────────────────

G = [
    {"en": "Carrier Code", "zh": "承运商代码", "ok": False, "id": "1"},
    {"en": "Integrations", "zh": "平台对接", "ok": True, "id": "2"},
    {"en": "Channel", "zh": "", "ok": False, "id": "3"},
    {"en": "Channels", "zh": "销售渠道", "ok": True, "id": "4"},
    {"en": "Override", "zh": "", "ok": False, "id": "5"},
]


def test_分類():
    assert tc.classify("Create Override", G)[0] == tc.NEW
    assert tc.classify("Carrier Codes", G)[0] == tc.DRAFT        # 單複數
    assert tc.classify("Integration", G)[0] == tc.CONFIRMED      # 複數詞條、單數原文
    assert tc.classify("Override", G)[0] == tc.EMPTY
    st, row = tc.classify("Channel", G)
    assert st == tc.CONFIRMED and row["en"] == "Channels"        # 多列時已確認優先


def test_check_彙整與可序列化():
    import json
    rep = tc.check(["Go to " + SC.format("Integrations &gt; Create Override"),
                    "<strong>Carrier Codes</strong>", "<strong>Override</strong>"], G)
    assert rep["total"] == 4 and rep["pending"] == 3
    assert [x["label"] for x in rep["new"]] == ["Create Override"]
    assert rep["draft"][0]["zh"] == "承运商代码"
    assert rep["summary"] == "⚠️ 先補術語再翻譯｜待確認 3（本次新增 1）｜已確認 1（UI 詞 4）"
    assert rep["ready"] is False
    json.dumps(rep)          # kinds 不能是 set


def test_沒有待處理才說可以翻譯():
    """沒有新詞不等於可以翻譯：草稿未勾一樣會被翻譯按鈕擋下（Fay 2026-09-11）。"""
    ok = tc.check(["<strong>Integrations</strong>"], G)
    assert ok["ready"] and ok["summary"] == "✅ 可以翻譯｜1 個 UI 詞都已確認"
    none = tc.check(["plain text only"], G)
    assert none["ready"] and none["summary"] == "✅ 可以翻譯｜這篇沒有 UI 詞"
    draft_only = tc.check(["<strong>Carrier Code</strong>"], G)
    assert draft_only["new"] == [] and draft_only["ready"] is False
    assert draft_only["summary"].startswith("⚠️ 先補術語再翻譯")


def test_摘要只給待確認與已確認且加總等於UI詞數():
    """演進：6074 先顯示「新詞 20｜沒簡中 0｜未勾 0」（像新詞已有簡中），改成「待補簡中 20｜
    待勾選 0」後「待勾選 0」資訊量仍低。欄位只給「待確認」一個數字，細分放留言（Fay 2026-09-11）。"""
    rep = tc.check(["<strong>Brand New Label</strong>", "<strong>Override</strong>",
                    "<strong>Carrier Code</strong>", "<strong>Integrations</strong>"], G)
    assert rep["summary"] == "⚠️ 先補術語再翻譯｜待確認 3（本次新增 1）｜已確認 1（UI 詞 4）"
    no_new = tc.check(["<strong>Override</strong>"], G)
    assert no_new["summary"] == "⚠️ 先補術語再翻譯｜待確認 1｜已確認 0（UI 詞 1）"
    txt = tc.comment_text(rep)
    assert "\n• 還沒有簡中（本次新增到術語表）：Brand New Label" in txt
    assert "\n• 還沒有簡中：Override" in txt
    assert "\n• 有簡中、只差勾選：Carrier Code（承运商代码）" in txt
    assert "http" not in txt                      # 網址改成下面那段可點的連結


def test_留言的術語表是可點的連結():
    rep = tc.check(["<strong>Override</strong>"], G)
    rich = tc.comment_rich_text(rep, "https://example.com/glossary")
    # 格式由 Fay 2026-09-11 指定；n8n 會把「建列失敗」提醒插在 [1] 之後，段落順序不能亂動
    assert rich[0] == {"type": "text", "text": {"content": "術語檢查"}, "annotations": {"bold": True}}
    assert rich[1]["text"]["content"].startswith("：⚠️ 先補術語再翻譯")
    assert rich[2]["text"]["content"] == "\n\n👉 "          # 只空一行（Fay 2026-09-11 實看後調整）
    assert rich[3]["text"] == {"content": "開啟產品用術語表", "link": {"url": "https://example.com/glossary"}}
    assert all(len(r["text"]["content"]) <= 2000 for r in rich)


def test_glossary_from_notion():
    pages = [{"id": "p1", "properties": {
        "English": {"title": [{"plain_text": "Override"}]},
        "简体中文": {"rich_text": [{"plain_text": "覆盖"}]},
        "已確認": {"checkbox": True}}},
        {"id": "p2", "properties": {"English": {"title": []}}}]          # 空列略過
    assert tc.glossary_from_notion(pages) == [{"en": "Override", "zh": "覆盖", "ok": True, "id": "p1"}]


def test_glossary_from_notion_也吃精簡過的列():
    """n8n「整理術語表」會先縮成 {en, zh, ok}：原始 1.15 MB 送進 Python 會拖垮 task runner。"""
    slim = [{"en": " Override ", "zh": "覆盖", "ok": True}, {"en": "", "zh": "x", "ok": False}]
    assert tc.glossary_from_notion(slim) == [{"en": "Override", "zh": "覆盖", "ok": True, "id": None}]


# ── 輸出 ──────────────────────────────────────────────────

def test_沒有待處理就不留言():
    rep = tc.check(["<strong>Integrations</strong>"], G)
    assert rep["pending"] == 0 and tc.comment_rich_text(rep, "https://x") == []


def test_留言截斷在上限內():
    many = ["<strong>Label Number %d</strong>" % i for i in range(300)]
    rep = tc.check(many, G)
    txt = tc.comment_text(rep, limit=30)
    assert len(txt) <= 2000 and "等 300 個" in txt


def test_新列一律不勾已確認():
    item = {"label": "Go Back", "n": 1, "kinds": [tc.UI_PATH]}
    props = tc.new_row_properties(item, "Manage Integrated Message Codes", "2026-09-10")
    assert props["已確認"] == {"checkbox": False}
    assert props["English"]["title"][0]["text"]["content"] == "Go Back"


def test_沒新詞不寫變更紀錄():
    rep = tc.check(["<strong>Integrations</strong>"], G)
    assert tc.changelog_rich_text(rep, "T") == []


# ── n8n 相容 ──────────────────────────────────────────────

def test_打包後在只允許re的沙箱能跑():
    """產生器會把 translate_prompt 放前面、移除 import 那行。這裡照做一次。"""
    tp_src = (CONVERTER_DIR / "translate_prompt.py").read_text(encoding="utf-8")
    tc_src = (CONVERTER_DIR / "term_check.py").read_text(encoding="utf-8")
    tc_src = re.sub(r"^from translate_prompt import .*$", "", tc_src, flags=re.M)
    real_import = builtins.__import__

    def guarded(name, *a, **k):
        if name.split(".")[0] not in {"re"} and name not in sys.builtin_module_names:
            raise ImportError("BLOCKED: " + name)
        return real_import(name, *a, **k)

    ns = {"__name__": "sandboxed"}
    builtins.__import__ = guarded
    try:
        exec(compile(tp_src + "\n" + tc_src, "packed", "exec"), ns)
        rep = ns["check"]([SC.format("Create Override")], G)
    finally:
        builtins.__import__ = real_import
    assert rep["new"][0]["label"] == "Create Override"


def test_打包的四個模組頂層名稱不重複():
    """同步節點把 notion_blocks、notion2elementor、translate_prompt、term_check 接成
    同一個檔案。頂層名稱重複時，後定義的會靜默蓋掉前面的，轉換結果悄悄變掉。
    2026-09-10 接上術語檢查前查到 term_check 與 notion_blocks 都有 _plain。"""
    import ast
    seen, dup = {}, []
    for m in ("notion_blocks.py", "notion2elementor.py", "translate_prompt.py", "term_check.py"):
        src = (CONVERTER_DIR / m).read_text(encoding="utf-8")
        src = re.split(r'^if __name__ == "__main__":', src, flags=re.M)[0]
        for n in ast.parse(src).body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                names = [n.name]
            elif isinstance(n, ast.Assign):
                names = [x.id for t in n.targets for x in ast.walk(t) if isinstance(x, ast.Name)]
            else:
                names = []
            for nm in names:
                if nm in seen and seen[nm] != m:
                    dup.append((nm, seen[nm], m))
                seen.setdefault(nm, m)
    assert dup == []


def test_頂層import只有re與translate_prompt():
    src = (CONVERTER_DIR / "term_check.py").read_text(encoding="utf-8")
    top = [ln.split("#")[0].strip() for ln in src.splitlines()
           if ln.startswith("import ") or ln.startswith("from ")]
    assert top == ["import re", "from translate_prompt import _boundary_pattern"]
