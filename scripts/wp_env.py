#!/usr/bin/env python3
"""從 .env 取出「某一個站台」的 WordPress 連線資訊。

命名慣例與 `N8N_WEBHOOK_PATH` / `N8N_WEBHOOK_PATH_TEST` 一致：

    無後綴   = 正式站 support.synctify.net
    _TEST    = 測試站 support.synctify.io

所以兩站的帳密可以同時放在 .env 裡，跑腳本時用 `--target` 選，不必改檔案：

    ./.venv/bin/python scripts/verify_site_ready.py                  # 正式站
    ./.venv/bin/python scripts/verify_site_ready.py --target test    # 測試站

**兩站的 Application Password 是各自獨立的**——測試站那組拿去打正式站會 401，
反之亦然。這是搬站期間最容易踩的坑：.env 裡留著上一次用的那組，跑出來的失敗
看起來像站台壞了，其實只是拿錯鑰匙。

站台網址有預設值，.env 沒填就用預設，所以通常只需要填帳密兩項。
⚠️ 網址同時也寫在 `scripts/build_n8n_code_node.py` 的 `TARGETS`（那份還帶
n8n 憑證 ID 與 webhook 環境變數名），換站台時兩邊都要改。
"""
import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

TARGETS = {
    "prod": {"suffix": "",      "label": "正式站", "base": "https://support.synctify.net"},
    "test": {"suffix": "_TEST", "label": "測試站", "base": "https://support.synctify.io"},
}
DEFAULT_TARGET = "prod"


class MissingCredentials(Exception):
    """.env 少了這個站台需要的變數。訊息直接寫給使用者看，照著補就好。"""


def read_env(root=ROOT):
    """把 .env 讀成 dict。檔案不存在時回空 dict，由呼叫端報錯。"""
    out = {}
    f = pathlib.Path(root) / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


class WPTarget:
    """一個站台的連線資訊。"""

    def __init__(self, target, base, user, password, label):
        self.target = target
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.label = label

    @property
    def auth(self):
        """(user, password) tuple——httpx.BasicAuth / requests 都吃這個形狀。"""
        return (self.user, self.password)

    def basic_auth_header(self):
        import base64
        raw = f"{self.user}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def __repr__(self):
        return f"<WPTarget {self.target} {self.base} as {self.user!r}>"


def resolve(target=DEFAULT_TARGET, root=ROOT, base_override=None):
    """取得指定站台的連線資訊。

    base_override 用於 `--base`：只換網址，帳密仍取自該 target，
    這樣才能拿同一組憑證去打同站台的別的網域（例如暫時的 staging）。
    """
    if target not in TARGETS:
        raise MissingCredentials(
            f"未知的 target {target!r}，可選：{'、'.join(TARGETS)}")

    spec = TARGETS[target]
    sfx, label = spec["suffix"], spec["label"]
    env = read_env(root)

    if not env:
        raise MissingCredentials(
            f"找不到 {pathlib.Path(root) / '.env'}（或內容是空的）。\n"
            f"    複製 .env.example 為 .env 再填入實際值。")

    base = base_override or env.get(f"WP_BASE_URL{sfx}") or spec["base"]
    user = env.get(f"WP_USERNAME{sfx}", "")
    pw = env.get(f"WP_APP_PASSWORD{sfx}", "").replace(" ", "")

    missing = [n for n, v in ((f"WP_USERNAME{sfx}", user),
                              (f"WP_APP_PASSWORD{sfx}", pw)) if not v]
    if missing:
        raise MissingCredentials(
            f".env 缺少{label}的 {' / '.join(missing)}。\n"
            f"    這個專案的慣例是「無後綴＝正式站、_TEST＝測試站」，跟\n"
            f"    N8N_WEBHOOK_PATH / N8N_WEBHOOK_PATH_TEST 一樣。補上：\n\n"
            + "".join(f"        {n}=\n" for n in missing)
            + f"\n    {label}的 Application Password 要在{label}自己的後台產生，\n"
              f"    另一站那組在這裡無效。")

    return WPTarget(target, base, user, pw, label)


def add_target_arg(parser, default=DEFAULT_TARGET, help_extra=""):
    """把 `--target` 掛到 argparse 上，兩個驗證腳本共用同一組說明文字。"""
    parser.add_argument(
        "--target", choices=sorted(TARGETS), default=default,
        help=(f"目標站台（預設 {default}：{TARGETS[default]['label']}）。"
              f"帳密取自 .env 對應後綴的變數。{help_extra}"))
    return parser


if __name__ == "__main__":                       # 自我檢查：兩站各解析一次
    ap = argparse.ArgumentParser(description="檢查 .env 的兩站設定是否齊全")
    ap.parse_args()
    for t in sorted(TARGETS):
        try:
            wp = resolve(t)
            print(f"✅ {TARGETS[t]['label']:<4} {wp.base}  使用者 {wp.user!r}  "
                  f"密碼長度 {len(wp.password)}")
        except MissingCredentials as e:
            print(f"❌ {TARGETS[t]['label']:<4} {e}")
