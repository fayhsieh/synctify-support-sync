"""translate_prompt 的測試。

重點在「術語挑選」——那是這支模組唯一會靜默出錯的地方。挑錯詞不會拋例外，
只會讓 prompt 帶著錯誤的指示，而譯文出來之後沒人看得出是 prompt 的問題。
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import translate_prompt as tp

詞彙表 = [
    {"en": "Products", "zh": "产品"},
    {"en": "Product SKU", "zh": "Product SKU"},
    {"en": "Add Product", "zh": "添加产品"},
    {"en": "Cartons", "zh": "纸箱数"},
    {"en": "Link", "zh": "链接"},
    {"en": "Import", "zh": "导入"},
    {"en": "Channel", "zh": "渠道"},
    {"en": "Warehouse", "zh": "仓库"},
    {"en": "No Translation", "zh": ""},        # 沒有譯文 → 不該出現在 prompt
]

樣本 = [
    {"en": 'Click <span class="direction_step">Create</span> in the top-right corner.',
     "zh_cn": '点击右上角的 <span class="direction_step">创建</span>'},
    {"en": "Overview", "zh_cn": "概览"},
    {"en": 'Select a <span class="direction_step">Channel</span>.',
     "zh_cn": '选择 <span class="direction_step">渠道</span>'},
]


# ── 術語挑選 ────────────────────────────────────────────────────────────

def test_只挑真的出現的術語():
    hits = dict(tp.find_terms("Go to Products and select a Channel.", 詞彙表))
    assert hits == {"Products": "产品", "Channel": "渠道"}


def test_長詞優先_短詞不再重複命中():
    """Product SKU 命中後，Products／Add Product 不該再對同一段文字命中。

    否則模型會收到兩條指向不同譯文的規則，等於沒有規則。
    """
    hits = tp.find_terms("Enter the Product SKU here.", 詞彙表)
    ens = [en for en, _ in hits]
    assert "Product SKU" in ens
    assert "Products" not in ens


def test_詞邊界_不會把_Important_當成_Import():
    """2026-08 術語稽核實際踩過的誤判。純子字串比對必然中招。"""
    hits = [en for en, _ in tp.find_terms("Important Note about this.", 詞彙表)]
    assert "Import" not in hits


def test_詞邊界_不會把_Retail_Link_當成_Link():
    hits = [en for en, _ in tp.find_terms("Open the Retail Linkage page.", 詞彙表)]
    assert "Link" not in hits


def test_Link_單獨出現時要命中():
    """邊界檢查不能矯枉過正——真的出現時必須抓到。"""
    hits = [en for en, _ in tp.find_terms("Click the Link below.", 詞彙表)]
    assert "Link" in hits


def test_沒有譯文的詞條被跳過():
    hits = [en for en, _ in tp.find_terms("No Translation here.", 詞彙表)]
    assert "No Translation" not in hits


def test_大小寫不敏感():
    hits = [en for en, _ in tp.find_terms("go to products.", 詞彙表)]
    assert "Products" in hits


def test_同一個詞出現多次只列一條():
    hits = tp.find_terms("Products, Products, and more Products.", 詞彙表)
    assert [en for en, _ in hits].count("Products") == 1


# ── 樣本挑選 ────────────────────────────────────────────────────────────

def test_有標籤的內容配有標籤的樣本():
    """形狀不同的例子會讓模型以為輸出格式可以自由選擇。"""
    picked = tp.pick_samples('<p>Click <span class="direction_step">X</span></p>', 樣本)
    assert "<" in picked[0]["en"]


def test_純文字的內容配純文字的樣本():
    picked = tp.pick_samples("Just plain text here.", 樣本)
    assert "<" not in picked[0]["en"]


# ── prompt 組裝 ─────────────────────────────────────────────────────────

def test_prompt_含硬性規則與術語():
    sysmsg, user = tp.build_prompt("Go to Products.", 詞彙表, 樣本)
    assert "HTML 標籤原樣保留" in sysmsg
    assert "Products → 产品" in user
    assert "Go to Products." in user


def test_prompt_明說術語以對照為準而不是學樣本():
    """這是整個設計的關鍵約束，prompt 裡必須講出來。

    樣本的校對早於術語表定案，術語與詞彙表相反（商品 vs 产品）。
    不明講的話，模型會從樣本學到錯的術語。
    """
    _, user = tp.build_prompt('<p>Products</p>', 詞彙表, 樣本)
    assert "只學語氣與結構" in user
    assert "術語一律以上方對照為準" in user


def test_沒有術語時明說而不是留白():
    """留白會讓模型以為是漏給了，可能自行腦補。"""
    _, user = tp.build_prompt("Nothing relevant here at all.", 詞彙表, 樣本)
    assert "沒有出現術語表中的受管術語" in user


def test_不會夾帶未出現的術語():
    _, user = tp.build_prompt("Go to Products.", 詞彙表, 樣本)
    assert "仓库" not in user      # Warehouse 沒出現在原文裡


# ── 對真實樣本檔跑一次 ──────────────────────────────────────────────────

樣本檔 = pathlib.Path(__file__).resolve().parent.parent / "samples" / "tp-style-samples.json"


@pytest.mark.skipif(not 樣本檔.exists(), reason="需要 samples/tp-style-samples.json")
def test_真實樣本能組出_prompt():
    data = json.loads(樣本檔.read_text(encoding="utf-8"))
    real = data["samples"]
    assert real, "樣本檔是空的"
    for s in real[:5]:
        sysmsg, user = tp.build_prompt(s["en"], 詞彙表, real)
        assert sysmsg and user
        assert s["en"] in user
        # 樣本自己不該被當成「要翻譯的內容」重複出現在最後一段之外
        assert user.count("請翻譯以下內容：") == 1


@pytest.mark.skipif(not 樣本檔.exists(), reason="需要 samples/tp-style-samples.json")
def test_樣本檔標明了不可當術語依據():
    """這個警告若被拿掉，後人很可能拿樣本當 ground truth。"""
    data = json.loads(樣本檔.read_text(encoding="utf-8"))
    assert "_不可當作術語依據" in data
    assert "詞彙表為準" in data["_不可當作術語依據"]



# ── 評估時的資料洩漏防護 ────────────────────────────────────────────────

def test_待譯內容本身不會出現在範例裡():
    """否則拿樣本當測試輸入時，模型直接抄答案，評估分數全是假的。

    2026-09-08 組第一版 prompt 時當場看到：sample[3] 同時是範例也是待譯內容。
    """
    src = 樣本[0]["en"]
    picked = tp.pick_samples(src, 樣本)
    assert all(s["en"] != src for s in picked)


def test_排除比對忽略空白差異():
    """TP 存的原文帶大量縮排換行，逐字元比對會漏掉。"""
    src = 樣本[0]["en"]
    亂空白 = src.replace(" ", "\n   ")
    picked = tp.pick_samples(亂空白, 樣本)
    assert all(s["en"] != src for s in picked)


def test_排除之後仍然給得出範例():
    """防洩漏不能把樣本清空——沒有範例就學不到語氣。"""
    _, user = tp.build_prompt(樣本[0]["en"], 詞彙表, 樣本)
    assert "已審定的譯文範例" in user


@pytest.mark.skipif(not 樣本檔.exists(), reason="需要 samples/tp-style-samples.json")
def test_真實樣本逐筆自我檢查沒有洩漏():
    data = json.loads(樣本檔.read_text(encoding="utf-8"))["samples"]
    for s in data:
        _, user = tp.build_prompt(s["en"], 詞彙表, data)
        head = user.split("請翻譯以下內容：")[0]
        assert s["zh_cn"] not in head, f"答案洩漏到範例區：{s['en'][:60]}"


# ── 承自 Skill 的房規 ──────────────────────────────────────────────────
#
# 這些規則來自 skill/SKILL.md（Support Article Writer），是已在實際寫作與翻譯
# 中累積驗證過的。移植而不是重新發明——否則自動翻譯會跟人工翻譯長出兩種風格。
# 每一條都用測試釘住，免得日後改 prompt 時被無聲刪掉。

def test_system_含簡中在地化規則():
    sysmsg, _ = tp.build_prompt("Anything", 詞彙表, 樣本)
    assert "避免繁體中文句法與台灣用語" in sysmsg
    assert "不要臆測產品行為" in sysmsg


def test_system_禁止改寫可見的_UI_label():
    sysmsg, _ = tp.build_prompt("Anything", 詞彙表, 樣本)
    assert "不可改寫" in sysmsg


def test_system_要求非散文內容原樣輸出():
    """字典裡混有 GTM 的 iframe（id 3020）與錨點 href（#31-etsy）。

    翻到那些東西會直接壞掉站上的功能。
    """
    sysmsg, _ = tp.build_prompt("Anything", 詞彙表, 樣本)
    assert "iframe" in sysmsg
    assert "錨點" in sysmsg


def test_system_保留圖示控制項的寫法():
    sysmsg, _ = tp.build_prompt("Anything", 詞彙表, 樣本)
    assert "✏️(Edit)" in sysmsg


def test_system_要求巢狀標籤之間不留空白():
    """英文原文的 span 邊界都有空白（英文的詞距），中文不需要。

    2026-09-08 從樣本統計得出：英文原文 36 處標籤間有空白、0 處沒有；
    心柔的譯文 2 處有、34 處沒有——人工翻譯時會清掉。模型不知道要清，
    因為 prompt 只說「標籤原樣保留」，它們連空白一起保留了。
    """
    sysmsg, _ = tp.build_prompt("Anything", 詞彙表, 樣本)
    assert "巢狀標籤之間不要留空白" in sysmsg
    assert "词距" in sysmsg or "詞距" in sysmsg

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
