"""可翻譯區塊抽取測試（tp_blocks）。

翻譯單位必須是「整個區塊」而不是 TranslatePress 自動切出的片段——片段是以行內
元素的邊界切開的殘句，翻出來會很生硬。這裡的每個案例都對應一個實測發現。

執行：
    cd <repo root> && ./.venv/bin/python -m pytest converter/test_tp_blocks.py -v
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tp_blocks as tb  # noqa: E402


def wrap(inner, post_id=7251):
    """把片段包成跟實站一樣的 Elementor 容器。"""
    return (
        '<html><body><header><p>側邊欄導覽不該被抽到</p></header>'
        '<div data-elementor-type="wp-post" data-elementor-id="%d" '
        'class="elementor elementor-%d">%s</div>'
        '<footer><p>頁尾也不該被抽到</p></footer></body></html>'
    ) % (post_id, post_id, inner)


def originals(html, post_id=7251):
    return [b["original"] for b in tb.extract_blocks(html, post_id)]


# ── 範圍限縮 ──────────────────────────────────────────────

def test_只抽內容容器內的區塊():
    """整頁 143k、內容區只有 44k。不限縮會撈到側邊欄、頁尾，甚至 GTM 的 iframe。"""
    got = originals(wrap("<p>Enable the feature.</p>"))
    assert got == ["Enable the feature."]


def test_容器用div計數收尾而非非貪婪比對():
    """內容裡有幾百個巢狀 div，非貪婪比對會在第一個 </div> 就停住。"""
    inner = '<div class="a"><div class="b"><p>Deep content.</p></div></div>'
    assert originals(wrap(inner)) == ["Deep content."]


def test_找不到容器時退回整份():
    html = "<html><body><p>No elementor wrapper.</p></body></html>"
    assert tb.extract_blocks(html, 9999) == tb.extract_blocks(html)


# ── 最內層區塊 ────────────────────────────────────────────

def test_li包著p時該翻的是p():
    """實測 post 7251 有這種結構；把 li 當單位會連 <p> 標籤一起送去翻。"""
    inner = "<ul><li><p>Synctify places the order in <strong>On-Hold</strong> status.</p></li></ul>"
    assert originals(wrap(inner)) == [
        "Synctify places the order in <strong>On-Hold</strong> status."]


def test_li直接含文字時li就是單位():
    inner = "<ul><li><strong>New Order Frozen Period</strong> is configured per integration.</li></ul>"
    got = tb.extract_blocks(wrap(inner), 7251)
    assert len(got) == 1
    assert got[0]["tag"] == "li"
    assert got[0]["has_inline"] is True


def test_巢狀清單各自成塊():
    inner = "<ul><li>Outer text<ul><li>Inner text</li></ul></li></ul>"
    # 外層 li 含子區塊（內層 li），所以不是翻譯單位；只有內層 li 是
    assert originals(wrap(inner)) == ["Inner text"]


# ── 該跳過的 ──────────────────────────────────────────────

def test_跳過非散文():
    """GTM 的 iframe 真的躺在測試站字典裡（id 3020，status=0）等著被翻。"""
    inner = ('<p><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe></p>'
             "<p>Real prose.</p>")
    assert originals(wrap(inner)) == ["Real prose."]


def test_跳過沒有文字的區塊():
    inner = '<p><img src="a.png" /></p><p>&nbsp;</p><p>Has text.</p>'
    assert originals(wrap(inner)) == ["Has text."]


def test_重複原文只留一次():
    inner = "<p>Same sentence.</p><p>Same sentence.</p>"
    assert originals(wrap(inner)) == ["Same sentence."]


# ── 空白與換行 ────────────────────────────────────────────

def test_不壓縮區塊內的空白():
    """shortcode 模板產生的連續空格是原文的一部分，壓掉就跟頁面對不上。"""
    inner = ('<p>Click         <span class="direction_steps">\n'
             '            <span class="direction_step">Release Order</span>        </span>\n'
             "         to release the order.</p>")
    got = originals(wrap(inner))[0]
    assert "         <span" in got
    assert "\n            <span" in got


def test_normalize只統一換行():
    assert tb.normalize("a\r\nb") == "a\nb"
    assert tb.normalize("a  \n  b") == "a  \n  b"   # 空白原封不動


def test_比對時換行差異不影響():
    """TP 比對會正規化：測試站有 6 筆存 \\r\\n 而頁面給 \\n，譯文照樣生效。"""
    inner = "<p>Line one.\nLine two.</p>"
    existing = [{"original": "Line one.\r\nLine two.", "status": 2}]
    out = tb.pending_blocks(wrap(inner), 7251, existing)
    assert out["pending"] == []
    assert out["already_human"] == 1


# ── pending_blocks ────────────────────────────────────────

def test_只有人工精修才算完成():
    """status=0/1 的列要重送——重跑才能修正舊的機器翻譯。"""
    inner = "<p>Alpha.</p><p>Beta.</p><p>Gamma.</p>"
    existing = [
        {"original": "Alpha.", "status": 2},   # 人工，跳過
        {"original": "Beta.", "status": 1},    # 機翻，重送
        {"original": "Gamma.", "status": 0},   # 未翻，重送
    ]
    out = tb.pending_blocks(wrap(inner), 7251, existing)
    assert [b["original"] for b in out["pending"]] == ["Beta.", "Gamma."]
    assert out["total_blocks"] == 3
    assert out["already_human"] == 1


def test_existing可以是純字串清單():
    out = tb.pending_blocks(wrap("<p>Alpha.</p><p>Beta.</p>"), 7251, ["Alpha."])
    assert [b["original"] for b in out["pending"]] == ["Beta."]


def test_has_inline標示是否行內混排():
    inner = "<p>Plain sentence.</p><p>With <strong>bold</strong> inside.</p>"
    got = tb.extract_blocks(wrap(inner), 7251)
    assert [b["has_inline"] for b in got] == [False, True]


def test_標記notion留言殘留但不剔除():
    """original 必須與頁面逐字相符，動了就對不上 TP 的比對——所以只標記。

    測試站掃到 7／36 篇已發佈文章帶著這種殘留（轉換器只剔除了 discussion span
    與 notionvc 註解，漏了 notion-enable-hover）。
    """
    dirty = ('<p><em><span class="notion-enable-hover" data-token-index="0">'
             "Last updated: May 28, 2026</span></em>"
             "<!-- notionvc: 6c1d0579-8e9e-4f2c-8512-d2db696cef78 --></p>")
    inner = dirty + "<p>Clean sentence.</p>"
    got = tb.extract_blocks(wrap(inner), 7251)
    assert [b["has_notion_residue"] for b in got] == [True, False]
    # 原文原封不動，連註解都留著
    assert "notionvc:" in got[0]["original"]

    out = tb.pending_blocks(wrap(inner), 7251, [])
    assert len(out["notion_residue"]) == 1
    assert len(out["pending"]) == 2      # 標記歸標記，不因此少送


def test_只用re模組():
    """要打包進 n8n 的 Code node，那裡只允許 import re。"""
    src = (pathlib.Path(__file__).resolve().parent / "tp_blocks.py").read_text(encoding="utf-8")
    imports = {ln.strip() for ln in src.splitlines()
               if ln.startswith("import ") or ln.startswith("from ")}
    assert imports == {"import re"}, imports
