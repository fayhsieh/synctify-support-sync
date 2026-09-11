"""glossary_article_terms 的純函式測試（不連網）。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest scripts/test_glossary_article_terms.py -v
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import glossary_article_terms as gat  # noqa: E402

# 跟實站 [direction] shortcode 輸出的空白一模一樣
STEP = ('<span class="direction_steps">\n            '
        '<span class="direction_step">{}</span>        </span>\n        ')


def blk(html):
    return {"original": html, "text": re.sub(r"<[^>]+>", "", html)}


def labels(*htmls):
    return {k: v for k, v in gat.candidates([blk(h) for h in htmls]).items()}


# ── 撈候選 ────────────────────────────────────────────────

def test_導覽路徑按大於號拆開():
    got = labels("You can manage them from " + STEP.format(
        "Integrations &gt; Integrated Message Codes") + ".")
    assert [v["label"] for v in got.values()] == ["Integrations", "Integrated Message Codes"]
    assert got["integrated message codes"]["kinds"] == {"UI 路徑"}


def test_粗體也是候選():
    got = labels("<strong>Inbound only</strong> — Synctify receives this code.")
    assert got["inbound only"]["kinds"] == {"粗體"}


def test_按鈕前的加號去掉():
    assert "add code" in labels("Click " + STEP.format("+ Add Code") + ".")


def test_實體還原():
    """TP 渲染把 & 寫成 &#038;——7622 的 Preview & Test 三種寫法都有。"""
    got = labels("Use " + STEP.format("Preview &#038; Test") + " to check.",
                 "Click " + STEP.format("Preview & Test") + ".")
    assert list(got) == ["preview & test"]
    assert got["preview & test"]["n"] == 2


def test_代碼值不收():
    assert labels("one partner may send " + STEP.format("UPS_GR_RES")) == {}


def test_粗體整句不收():
    assert labels("<strong>This action cannot be undone.</strong>") == {}


def test_同一個詞跨段落累計次數_保留第一次的寫法():
    got = labels("Select " + STEP.format("Carriers") + ".",
                 "Find it in the " + STEP.format("carriers") + " list.",
                 "The " + STEP.format("Carriers") + " list.")
    assert got["carriers"]["label"] == "Carriers"
    # 小寫開頭的那個不收（像內部識別字），只算大寫的兩次
    assert got["carriers"]["n"] == 2


def test_結尾冒號去掉():
    assert "direction" in labels("Select the " + STEP.format("Direction") + ":",
                                 "<strong>Direction:</strong> inbound")


# ── 對照術語表 ────────────────────────────────────────────

G = [
    {"en": "Carrier Code", "zh": "承运商代码", "ok": False},
    {"en": "Integrations", "zh": "平台对接", "ok": True},
    {"en": "Channel", "zh": "", "ok": False},
    {"en": "Channels", "zh": "销售渠道", "ok": True},
    {"en": "Override", "zh": "", "ok": False},
]


def test_沒有的詞是新詞():
    assert gat.classify("Create Override", G)[0] == gat.NEW


def test_單複數兩向都算有():
    """跟翻譯時同一套比對——否則會「檢查說有、翻譯時沒命中」。"""
    assert gat.classify("Carrier Codes", G)[0] == gat.DRAFT
    assert gat.classify("Integration", G)[0] == gat.CONFIRMED


def test_多列並存時已確認優先():
    st, row = gat.classify("Channel", G)
    assert st == gat.CONFIRMED and row["en"] == "Channels"


def test_有列沒簡中():
    assert gat.classify("Override", G)[0] == gat.EMPTY


def test_查_OMS_與人工譯文的單複數變體():
    assert gat.forms("Carriers") == ["carriers", "carrier"]
    assert gat.forms("Address") == ["address", "addresss"]
    assert gat.forms("Override") == ["override", "overrides"]
