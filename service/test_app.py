"""
service/app.py 的本機測試。

用 FastAPI TestClient 走完整 HTTP 層（不是直接呼叫 convert()），
把 samples/amazon-sc-v2-notion.md 這篇實測樣本丟進 /convert，
驗證回傳結構與關鍵映射，確保 HTTP wrapper 沒有動到轉換結果。

執行：
    cd <repo root> && python3.12 -m pytest service/test_app.py -v
"""
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from service.app import app

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "samples" / "amazon-sc-v2-notion.md"

client = TestClient(app)


def _flatten_widgets(elements: list) -> list:
    """遞迴收集所有 elType == 'widget' 的節點。"""
    out = []
    for el in elements:
        if el.get("elType") == "widget":
            out.append(el)
        if el.get("elements"):
            out.extend(_flatten_widgets(el["elements"]))
    return out


@pytest.fixture(scope="module")
def sample_md() -> str:
    return SAMPLE.read_text(encoding="utf-8")


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_convert_sample(sample_md):
    r = client.post(
        "/convert",
        json={"markdown": sample_md, "title": "Test", "faq_group": "test", "sync_date": "July 15, 2026"},
    )
    assert r.status_code == 200
    body = r.json()

    # 三個頂層區塊都在
    assert set(body.keys()) == {"template", "faq_items", "report"}

    # template schema 與站上匯出檔一致
    t = body["template"]
    assert set(t.keys()) == {"content", "page_settings", "version", "title", "type"}
    assert t["title"] == "Test"
    assert t["type"] == "page"
    assert len(t["content"]) > 0
    container = t["content"][1]
    assert container["elType"] == "container"
    assert {"id", "settings", "elements", "isInner", "elType"} <= set(container.keys())

    # FAQ 段被抽出（樣本有 3 題）
    assert len(body["faq_items"]) == 3
    assert body["report"]["faq_group"] == "test"

    # 關鍵映射抽查：inline code → [direction]、callout → docly_alerts_box、FAQ shortcode
    # 走訪整棵樹收集所有 widget，避免用 json.dumps 比對時被引號轉義干擾
    widgets = _flatten_widgets(t["content"])
    blob = json.dumps(t, ensure_ascii=False)
    assert "[direction]Authorize Now[/direction]" in blob  # inline code（無引號，可直接比對）
    assert any(w["widgetType"] == "docly_alerts_box" for w in widgets)  # callout
    shortcodes = [w["settings"].get("shortcode", "") for w in widgets if w["widgetType"] == "shortcode"]
    assert any('[faq group="test"' in sc for sc in shortcodes)  # FAQ shortcode（原字串，未轉義）

    # 圖片路由：Notion S3 暫存圖標記待上傳
    assert body["report"]["images_pending_upload"] == 6


def test_convert_missing_field():
    # 少了必填欄位 → FastAPI 回 422
    r = client.post("/convert", json={"markdown": "# hi"})
    assert r.status_code == 422


def test_convert_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("CONVERTER_API_KEY", "secret-123")
    # 沒帶 key → 401
    r = client.post("/convert", json={"markdown": "## X", "title": "T", "faq_group": "g"})
    assert r.status_code == 401
    # 帶對的 key → 200
    r = client.post(
        "/convert",
        headers={"Authorization": "Bearer secret-123"},
        json={"markdown": "## X", "title": "T", "faq_group": "g"},
    )
    assert r.status_code == 200
