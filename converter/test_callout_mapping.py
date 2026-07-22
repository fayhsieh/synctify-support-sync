"""
Callout 類型對映測試（mapping-rules §二）。

鎖住兩件事：
  1. callout_type() 對「Notion API 原生 emoji＋底色」與「舊匯出 icon-path 字串」兩種格式
     分類一致（五種：Message/Info/Success/Warning/Danger）。
  2. 端到端：emoji 形式的 <callout> 經 convert() 後產出正確的 docly_alerts_box alert_type。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_callout_mapping.py -v
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402


# (icon, color, 期望 alert_type)
CASES = [
    # 舊匯出格式（samples/ 用）
    ("/icons/checkmark-square_green.svg", "green_bg", "success"),
    ("/icons/info-alternate_lightgray.svg", "gray_bg", "info"),
    ("/icons/light-bulb_gray.svg", "gray_bg", None),
    ("/icons/warning_yellow.svg", "yellow_bg", "warning"),
    ("/icons/warning_red.svg", "red_bg", "danger"),
    # Notion API 原生 emoji＋底色（n8n Blocks→Markdown 節點輸出）
    ("💡", "gray_background", None),      # Message
    ("ℹ️", "blue_background", "info"),    # Info
    ("✅", "green_background", "success"),  # Success
    ("⚠️", "yellow_background", "warning"),  # Warning（黃底）
    ("⚠️", "red_background", "danger"),   # Danger（紅底）
    # 邊界：無 icon → 保守 Message
    ("", "default", None),
    (None, None, None),
]


@pytest.mark.parametrize("icon,color,expected", CASES)
def test_callout_type(icon, color, expected):
    assert n2e.callout_type(icon, color) == expected


def test_warning_vs_danger_needs_color():
    """同為 ⚠️，黃底=Warning、紅底=Danger（底色是唯一區分依據）。"""
    assert n2e.callout_type("⚠️", "yellow_background") == "warning"
    assert n2e.callout_type("⚠️", "red_background") == "danger"


def test_end_to_end_emoji_callouts():
    """emoji 形式的 callout 經 convert() 後 alert_type 正確。"""
    md = "\n".join([
        "## Section",
        '<callout icon="💡" color="gray_background">',
        "\t**Tip**",
        "\tThis is a message.",
        "</callout>",
        '<callout icon="✅" color="green_background">',
        "\tDone successfully.",
        "</callout>",
        '<callout icon="⚠️" color="yellow_background">',
        "\tBe careful here.",
        "</callout>",
        '<callout icon="⚠️" color="red_background">',
        "\tThis is dangerous.",
        "</callout>",
        '<callout icon="ℹ️" color="blue_background">',
        "\tFor your information.",
        "</callout>",
    ])
    tpl, _faqs, _rep = n2e.convert(md, "T", "t", sync_date="July 22, 2026")

    def widgets(els):
        out = []
        for e in els:
            if e.get("elType") == "widget":
                out.append(e)
            out += widgets(e.get("elements", []))
        return out

    alerts = [w["settings"] for w in widgets(tpl["content"])
              if w["widgetType"] == "docly_alerts_box"]
    types = [s.get("alert_type") for s in alerts]
    assert types == [None, "success", "warning", "danger", "info"]
    # Message callout 首行粗體 → alert_title
    assert alerts[0]["alert_title"] == "Tip"
