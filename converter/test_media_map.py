"""
圖片上傳後回填版面測試（apply_media_map）。

Notion 的 S3 網址帶預簽章、一小時後失效，寫入 WP 前必須換成媒體庫網址，
否則文章的圖會在一小時後全部變破圖。

同時驗證 [caption] shortcode 被補成實站格式（範本 7915）：
  [caption id="attachment_N" ...]<a href="原圖"><img class="wp-image-N size-large"
  src="large 尺寸" width="實際寬" height="實際高" /></a> 圖說[/caption]

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_media_map.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402
import notion_blocks as nb  # noqa: E402

S3 = ("https://prod-files-secure.s3.us-west-2.amazonaws.com/x/y/shot.png"
      "?X-Amz-Expires=3600&X-Amz-Signature=abc")

MEDIA = {
    "id": 7142,
    "full_url": "https://assets.synctify.net/support/2026/08/shot.png",
    "large_url": "https://assets.synctify.net/support/2026/08/shot-1024x576.png",
    "width": 1024,
    "height": 576,
}


def _widgets(els):
    out = []
    for e in els:
        if e.get("elType") == "widget":
            out.append(e)
        out += _widgets(e.get("elements", []))
    return out


def _build(blocks):
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _f, rep = n2e.convert(md, "T", "t", sync_date="August 2, 2026")
    return tpl, rep


def test_standalone_image_widget_url_replaced():
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "S"}]}},
        {"id": "i", "type": "image",
         "image": {"external": {"url": S3}, "caption": [{"plain_text": "A screenshot"}]}},
    ]
    tpl, rep = _build(blocks)
    assert rep["images_pending_upload"] == 1

    n = n2e.apply_media_map(tpl, {S3: MEDIA})
    assert n == 1
    img = [w for w in _widgets(tpl["content"]) if w["widgetType"] == "image"][0]
    assert img["settings"]["image"]["url"] == MEDIA["full_url"]
    assert img["settings"]["image"]["id"] == 7142
    assert "X-Amz" not in str(tpl)          # 預簽章網址完全清乾淨


def test_nested_caption_shortcode_patched_to_live_format():
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Steps"}]}},
        {"id": "n1", "type": "numbered_list_item",
         "numbered_list_item": {"rich_text": [{"plain_text": "Open menu"}]}},
        {"id": "n2", "type": "numbered_list_item", "has_children": True,
         "numbered_list_item": {"rich_text": [{"plain_text": "Select it"}]},
         "children": [{"id": "img", "type": "image",
                       "image": {"external": {"url": S3},
                                 "caption": [{"plain_text": "The menu"}]}}]},
    ]
    tpl, _rep = _build(blocks)
    n2e.apply_media_map(tpl, {S3: MEDIA})

    step = [w for w in _widgets(tpl["content"])
            if w["widgetType"] == "docly_list_item"][0]["settings"]["ul_icon_list"][1]["text"]
    assert 'id="attachment_7142"' in step
    assert f'<a href="{MEDIA["full_url"]}">' in step        # Link To = Media File
    assert f'src="{MEDIA["large_url"]}"' in step            # Size = Large
    assert 'class="wp-image-7142 size-large"' in step
    assert "X-Amz" not in step


def test_non_16_9_image_uses_actual_dimensions():
    """直式圖的 large 高度不是 576，必須用 WP 回傳的實際尺寸。"""
    portrait = dict(MEDIA, width=1024, height=1365,
                    large_url="https://assets.synctify.net/support/2026/08/tall-1024x1365.png")
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "S"}]}},
        {"id": "n1", "type": "numbered_list_item", "has_children": True,
         "numbered_list_item": {"rich_text": [{"plain_text": "Step"}]},
         "children": [{"id": "img", "type": "image",
                       "image": {"external": {"url": S3}, "caption": [{"plain_text": "Tall"}]}}]},
    ]
    tpl, _rep = _build(blocks)
    n2e.apply_media_map(tpl, {S3: portrait})
    step = [w for w in _widgets(tpl["content"])
            if w["widgetType"] == "docly_list_item"][0]["settings"]["ul_icon_list"][0]["text"]
    assert 'width="1024" height="1365"' in step
    assert 'height="576"' not in step


def test_unmapped_image_left_alone():
    """上傳失敗（media_map 沒有該圖）時不動它，讓呼叫端能偵測到仍有未替換的圖。"""
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "S"}]}},
        {"id": "i", "type": "image",
         "image": {"external": {"url": S3}, "caption": [{"plain_text": "A"}]}},
    ]
    tpl, _rep = _build(blocks)
    n = n2e.apply_media_map(tpl, {})
    assert n == 0
    img = [w for w in _widgets(tpl["content"]) if w["widgetType"] == "image"][0]
    assert img["settings"]["image"]["url"] == S3      # 保持原樣
