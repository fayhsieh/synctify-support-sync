"""compare_with_prod 的配對與分類邏輯（離線，不連正式站）。"""
import json
import sys

import pytest

import compare_with_prod as cw

H = [{"original": "Click <span class=\"direction_step\">Save</span>.",
      "translated": "点击 <span class=\"direction_step\">保存</span>。", "status": 2, "block_type": 1},
     {"original": "Go to   Settings.", "translated": "前往设置。", "status": 2, "block_type": 1}]


def test_相同_不同_對不上三種分類():
    items = [{"original": H[0]["original"], "translated": H[0]["translated"]},
             {"original": "Go to Settings.", "translated": "前往設定。"},   # 空白不同也要配得上
             {"original": "Brand new paragraph.", "translated": "全新段落。"}]
    same, diff, none = cw.compare(items, H)
    assert len(same) == 1 and len(diff) == 1 and len(none) == 1
    assert diff[0]["human"] == "前往设置。"


def test_譯文只差空白算相同():
    items = [{"original": H[1]["original"], "translated": "前往设置。 "}]
    same, diff, _ = cw.compare(items, H)
    assert len(same) == 1 and not diff


@pytest.mark.parametrize("shape", ["wrapped", "dict", "list"])
def test_吃n8n輸出的幾種形狀(tmp_path, shape):
    items = [{"original": "a", "translated": "甲"}]
    data = {"wrapped": [{"items": items}], "dict": {"items": items}, "list": items}[shape]
    f = tmp_path / "o.json"; f.write_text(json.dumps(data), encoding="utf-8")
    assert cw.load_items(f) == items


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
