#!/usr/bin/env python3
"""
錯誤代碼表的測試。

最重要的一項是 `test_js_與_python_判斷完全一致`：這份表的價值建立在
「n8n 上跑的 JS」與「這裡測的 Python」是同一套判斷。兩邊各寫一次的話，
遲早會有一邊改了另一邊沒改，而且不會有人發現——因為沒人會去比對。
所以測試直接拿產生出來的 JS 丟給 node 跑，逐筆比對。

    ./.venv/bin/python -m pytest scripts/test_error_codes.py -q
"""
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import error_codes as ec


# 每一筆都是「實際會從 WP／n8n 收到的原始錯誤」→ 期望代碼。
# 不要拿關鍵字本身當樣本——那只是在測 in 運算子。要用真的訊息長相。
真實樣本 = [
    # A：小編自己改
    ('422 - {"code":"category_not_found","message":"No category page titled '
     '\\"Automation\\" under \\"Synctify Documentation\\""}', "A1"),
    ('404 - {"code":"rest_post_invalid_id","message":"Invalid post ID.",'
     '"data":{"status":404}}', "A2"),

    # B：直接重按
    ('403 - <?xml version="1.0"?><Error><Code>AccessDenied</Code>'
     '<Message>Request has expired</Message></Error>', "B1"),
    ("connect ETIMEDOUT 10.0.0.5:443", "B2"),
    ("Error: socket hang up", "B2"),

    # C：找 Fay
    ('401 - {"code":"rest_forbidden","message":"Sorry, you are not allowed '
     'to do that.","data":{"status":401}}', "C1"),
    ('400 - {"code":"rest_invalid_param","message":"Invalid parameter(s): '
     'status","data":{"status":400}}', "C3"),
    ('500 - {"code":"autosave_failed","message":"Could not create autosave '
     'document"}', "C4"),
    ('501 - {"code":"no_aioseo","message":"AIOSEO is not active"}', "C5"),
    ('404 - {"code":"root_not_found","message":"Doc root not found"}', "C6"),
    ('404 - {"code":"rest_no_route","message":"No route was found matching '
     'the URL and request method"}', "C7"),

    # 沒命中 → 安全地退回 C0
    ("something nobody has ever seen before", "C0"),
    ("", "C0"),
]


@pytest.mark.parametrize("raw,expected", 真實樣本)
def test_歸類正確(raw, expected):
    assert ec.classify(raw)[0] == expected


def test_未知錯誤退回找_Fay_而不是叫小編自己改():
    """預設值必須落在 C。把未知錯誤說成『你自己改』會害小編亂改。"""
    code, _, _ = ec.classify("完全沒見過的東西")
    assert code.startswith("C")


def test_每個分類都有條目():
    """三格都要有東西——只有一格有內容的話，分類就沒有意義。"""
    有的 = {c[0] for c, _, _, _ in ec.CODES}
    assert 有的 == {"A", "B", "C"}


def test_訊息含代碼與動作():
    msg = ec.format_reason(
        '422 - {"code":"category_not_found"}', pretty="422 No category page")
    assert msg.startswith("[A1]")
    assert "→" in msg                      # 一定要有「該做什麼」那行
    assert "原始訊息：" in msg              # 原文要保留，否則我無法診斷


def test_None_不會炸():
    assert ec.classify(None)[0] == "C0"


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node 才能比對 JS")
def test_js_與_python_判斷完全一致():
    """把產生出來的 JS 丟給 node 跑，逐筆與 Python 比對。

    這是整份測試的重點。兩邊只要有一邊改了，這裡就會紅。
    """
    樣本 = [r for r, _ in 真實樣本] + [k
                                      for _, keys, _, _ in ec.CODES
                                      for k in keys] + [
        "CATEGORY_NOT_FOUND",          # 大小寫不敏感
        "x-amzn-waf-action: challenge",
        None,
    ]
    js = (f"const f = {ec.to_js()};\n"
          f"const cases = {json.dumps(樣本, ensure_ascii=False)};\n"
          "console.log(JSON.stringify(cases.map(c => f(c)[0])));\n")
    out = subprocess.run([shutil.which("node"), "-e", js],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    js結果 = json.loads(out.stdout)
    py結果 = [ec.classify(c)[0] for c in 樣本]
    不一致 = [(c, p, j) for c, p, j in zip(樣本, py結果, js結果) if p != j]
    assert not 不一致, f"JS 與 Python 判斷不同：{不一致}"


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node 才能比對 JS")
def test_產生的_js_語法正確():
    out = subprocess.run([shutil.which("node"), "--check", "-"],
                         input=f"const f = {ec.to_js()};", capture_output=True,
                         text=True, timeout=30)
    assert out.returncode == 0, out.stderr


def _run_reason(raw):
    """把 to_reason_js() 丟給 node 跑，回傳它產生的訊息。"""
    js = (f"const f = {ec.to_reason_js()};\n"
          f"console.log(f({json.dumps(raw, ensure_ascii=False)}));\n")
    out = subprocess.run([shutil.which("node"), "-e", js],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


需要node = pytest.mark.skipif(not shutil.which("node"), reason="需要 node")

# 端點回分類找不到時的**實際**回應長相（雙引號是跳脫的，available 在 data 裡）
分類錯誤 = ('422 - {"code":"category_not_found","message":"No category page '
            'titled \\"Automation\\" under \\"Synctify Documentation\\"",'
            '"data":{"status":422,"available":["Getting Started","Settings"]}}')


@需要node
def test_訊息不會被跳脫的引號腰斬():
    """2026-09-05 實際踩到：先剝反斜線再抓 message，會在分類名前面就截斷。

    截斷後訊息變成「No category page titled」——最關鍵的分類名反而不見了。
    """
    msg = _run_reason(分類錯誤)
    assert "Automation" in msg, f"分類名被截掉了：{msg}"
    assert "Synctify Documentation" in msg


@需要node
def test_可用分類清單有被抓出來():
    """available 在 data 裡而不是 message 裡，不特別抓就永遠看不到。

    這是小編遇到 A1 時唯一真正需要的資訊：那到底有哪些分類可以選。
    """
    msg = _run_reason(分類錯誤)
    assert "可用分類：" in msg
    assert "Getting Started" in msg and "Settings" in msg
    assert '\\"' not in msg and '"Getting Started"' not in msg   # 引號要清掉


@需要node
def test_reason_訊息三段俱全():
    msg = _run_reason('404 - {"code":"rest_no_route","message":"No route"}')
    assert msg.startswith("[C7]")
    assert "→" in msg
    assert "原始訊息：" in msg


@需要node
def test_reason_對非_json_的錯誤也不會炸():
    for raw in ("connect ETIMEDOUT 1.2.3.4:443", "", "Error: socket hang up"):
        msg = _run_reason(raw)
        assert msg.startswith("["), f"{raw!r} → {msg!r}"


@需要node
def test_reason_的分類與_python_一致():
    for raw, expected in 真實樣本:
        assert _run_reason(raw).startswith(f"[{expected}]"), raw


def test_markdown_列出所有代碼():
    md = ec.to_markdown()
    for code, _, _, _ in ec.CODES:
        assert f"**{code}**" in md
    assert f"**{ec.FALLBACK[0]}**" in md


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
