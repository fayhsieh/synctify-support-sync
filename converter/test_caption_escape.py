r"""圖片 alt 屬性的跳脫測試。

2026-09-01 在正式站 5620 實際發生：圖說是「Navigate to Settings > Organization >
Members.」，alt 沿用同一段文字，結果 img 標籤被從中切斷，
`Organization > Members." width="1024" height="605" />` 直接印在頁面上。

原因不在我們的程式，是 WordPress 核心的 `img_caption_shortcode()`：

    #((?:<a [^>]+>\s*)?<img [^>]+>(?:\s*</a>)?)(.*)#is

`<img [^>]+>` 的 `[^>]+` 在第一個 `>` 就停住，於是 alt 裡的 `>` 被當成標籤結束，
剩下的字元全部被當成圖說。UI 路徑用 `>` 分隔是我們的寫作慣例，所以這不是罕見情況。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_caption_escape.py -v
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import notion2elementor as n2e  # noqa: E402

# WordPress 核心 img_caption_shortcode() 的實際正規表達式
WP_CAPTION_RE = re.compile(
    r'((?:<a [^>]+>\s*)?<img [^>]+>(?:\s*</a>)?)(.*)', re.I | re.S)


def test_大於符號會被編碼():
    assert n2e.esc_attr("Settings > Organization") == "Settings &gt; Organization"


def test_四種字元都處理():
    assert n2e.esc_attr('a & b < c > d "e"') == "a &amp; b &lt; c &gt; d &quot;e&quot;"


def test_and_符號先處理才不會重複編碼():
    """順序錯的話 `<` 會先變 `&lt;`，接著那個 `&` 又被編碼成 `&amp;lt;`。"""
    assert n2e.esc_attr("<") == "&lt;"
    assert n2e.esc_attr("&lt;") == "&amp;lt;"      # 原本就是字面值的 & 要被編碼


def test_可逆():
    for s in ("Settings > Organization", 'a & b "c" <d>', "純文字"):
        assert n2e.unesc_attr(n2e.esc_attr(s)) == s


def test_wordpress_的正規表達式不會再切錯():
    """這是整個修正的重點：用 WP 核心的 regex 實際跑一次。"""
    alt = "Navigate to Settings > Organization > Members."
    img = (f'<a href="https://x/a.png"><img class="size-large" src="https://x/a.png" '
           f'alt="{n2e.esc_attr(alt)}" width="1024" height="576" /></a> 圖說文字')
    m = WP_CAPTION_RE.match(img)
    assert m, "regex 應該要比對得到"
    # 圖說只能是 </a> 之後那段，不可以混進 img 標籤的殘骸
    assert m.group(2).strip() == "圖說文字"
    assert "width=" not in m.group(2)


def test_未跳脫時確實會壞掉():
    """反向確認：不跳脫的話，WP 的 regex 真的會把 img 標籤切斷。

    這一項是用來證明上一項不是白測的——它鎖住我們對根因的理解。
    """
    alt = "Navigate to Settings > Organization > Members."
    img = (f'<a href="https://x/a.png"><img class="size-large" src="https://x/a.png" '
           f'alt="{alt}" width="1024" height="576" /></a> 圖說文字')
    m = WP_CAPTION_RE.match(img)
    assert "width=" in m.group(2), "沒跳脫的話 img 屬性應該會漏進圖說"


def test_轉換器產出的_caption_shortcode_是安全的():
    """端到端：走 blocks → markdown → convert，確認實際產物已跳脫。"""
    import notion_blocks as nb
    alt = "Navigate to Settings > Organization > Members."
    blocks = [
        {"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Steps"}]}},
        {"id": "n1", "type": "numbered_list_item", "has_children": True,
         "numbered_list_item": {"rich_text": [{"plain_text": "Open it"}]},
         "children": [{"id": "img", "type": "image",
                       "image": {"external": {"url": "https://x/a.png"},
                                 "caption": [{"plain_text": alt}]}}]},
    ]
    md, _ = nb.blocks_to_markdown(blocks)
    tpl, _faq, _rep = n2e.convert(md, "T", "t", sync_date="July 29, 2026")
    blob = str(tpl)
    assert "[caption" in blob, "應該要有 caption shortcode"
    assert 'alt="Navigate to Settings &gt; Organization &gt; Members."' in blob
    # 圖說本身是文字內容，不需要也不應該被編碼
    assert alt in blob
