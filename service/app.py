#!/usr/bin/env python3
"""
Notion → Elementor 轉換 HTTP microservice

薄薄一層 HTTP wrapper，包住 converter/notion2elementor.py 的 convert()。
n8n 用 HTTP Request 節點呼叫此服務；映射邏輯的單一真實來源永遠是
converter/notion2elementor.py，這裡不重寫、不改任何轉換規則。

啟動（本機）：
    uvicorn service.app:app --host 127.0.0.1 --port 8800 --reload
或：
    python3.12 -m uvicorn service.app:app --port 8800

端點：
    GET  /health   健康檢查（n8n 可用來探活）
    POST /convert  收 Notion markdown＋標題＋faq group，回傳轉換後 JSON

選用認證：設環境變數 CONVERTER_API_KEY 後，/convert 需帶
    Authorization: Bearer <key>
未設時不驗證（本機開發用）。
"""
import os
import pathlib
import sys
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

# ---- 匯入轉換器（單一真實來源，不重寫）----
# converter/ 是同層目錄且無 __init__.py，直接把它加進 sys.path 再 import。
_CONVERTER_DIR = pathlib.Path(__file__).resolve().parent.parent / "converter"
sys.path.insert(0, str(_CONVERTER_DIR))
import notion2elementor as n2e  # noqa: E402

app = FastAPI(
    title="Synctify Notion→Elementor Converter",
    description="把 Notion 教學文件轉成 Elementor template JSON 的 HTTP 服務。",
    version="0.1.0",
)


# ---- 認證（選用）----
def require_api_key(authorization: Optional[str] = Header(default=None)) -> None:
    expected = os.environ.get("CONVERTER_API_KEY")
    if not expected:
        return  # 未設金鑰 → 本機開發模式，不驗證
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---- 請求／回應模型 ----
class ConvertRequest(BaseModel):
    markdown: str = Field(..., description="Notion 頁面內容（Notion-flavored Markdown）")
    title: str = Field(..., description="文章標題（寫入 template.title）")
    faq_group: str = Field(..., description="FAQ group slug（＝文章 slug）")
    sync_date: Optional[str] = Field(
        default=None,
        description='同步日期，例 "July 15, 2026"；不給則用今天',
    )


class ConvertResponse(BaseModel):
    template: dict[str, Any] = Field(..., description="可匯入 Elementor 的 template JSON")
    faq_items: list[dict[str, Any]] = Field(..., description="Arconix FAQ 待寫入項目")
    report: dict[str, Any] = Field(..., description="轉換摘要（圖片、FAQ 統計等）")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "converter_version": "notion2elementor"}


@app.post("/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest, _auth: None = Depends(require_api_key)) -> ConvertResponse:
    try:
        template, faq_items, report = n2e.convert(
            req.markdown, req.title, req.faq_group, sync_date=req.sync_date
        )
    except Exception as exc:  # 轉換失敗 → 400，把訊息帶回給 n8n 方便除錯
        raise HTTPException(status_code=400, detail=f"Conversion failed: {exc}") from exc
    return ConvertResponse(template=template, faq_items=faq_items, report=report)
