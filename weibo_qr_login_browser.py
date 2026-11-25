#!/usr/bin/env python3
"""微博の公式のログインページを本物のブラウザ(Playwright の Chromium)で開き、そのページが出す QR でログインし直す
(2026-11-25、HANDOFF_NOTAM.md §32)。

weibo_qr_login.py(API を直接呼んで QR を作る)では、スマホで読み取っても確認画面が読み込み中のまま進まず、
微博側の状態は最後まで「未使用」だった。公式のログインページそのものに QR を作らせれば通るかを試す。

  1. passport.weibo.com のログインページを開き、ページが取りにいく QR(/sso/v2/qrcode/image の応答)の画像 URL をログに出す
  2. ページが読み取りの状態を確かめる応答(/sso/v2/qrcode/check)の retcode をログに出す(読み取りが届いたか分かる)
  3. ログインできたら(.weibo.com に ALF 付きの SUB ができたら)、weibo.com を開いて XSRF-TOKEN も受け取り、
     东部战区の投稿一覧が読めるか確かめて、収集が使う形で --out に書く(cookie の値はログに出さない)

使い方: python weibo_qr_login_browser.py --out "$RUNNER_TEMP/weibo_cookie/cookie.json"
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import time

from weibo_qr_login import check_cookie, summary

LOGIN_URL = "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog&url=https%3A%2F%2Fweibo.com%2F"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", type=int, default=300)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    shown, last = set(), [None]

    def on_response(r):
        u = r.url
        try:
            if "/sso/v2/qrcode/image" in u:
                d = r.json().get("data") or {}
                img = d.get("image")
                if img and img not in shown:
                    shown.add(img)
                    print("=" * 60)
                    print("微博アプリの「扫一扫」で、次の QR を読み取ってください(新しい QR が出たら新しい方を):")
                    print("  QR の画像:", img, flush=True)
                    print("=" * 60, flush=True)
            elif "/sso/v2/qrcode/check" in u:
                d = r.json()
                if d.get("retcode") != last[0]:
                    last[0] = d.get("retcode")
                    print(f"[page] qrcode/check retcode={d.get('retcode')} msg={d.get('msg')}", flush=True)
        except Exception:  # noqa: BLE001
            pass

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900},
                            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                        "Chrome/128.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        page.on("response", on_response)
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        if not shown:                                   # 既定が SMS などのときは「扫码登录」に切り替える
            for txt in ("扫码登录", "二维码登录", "扫码"):
                try:
                    page.get_by_text(txt, exact=False).first.click(timeout=3000)
                    break
                except Exception:  # noqa: BLE001
                    continue
            page.wait_for_timeout(4000)
        if not shown:
            print("ログインページに QR が出なかった(ページの形が変わった可能性)。URL:", page.url.split("?")[0])
            return 2

        end = time.time() + a.wait
        logged_in = False
        while time.time() < end:
            page.wait_for_timeout(3000)
            cs = {c["name"]: c for c in ctx.cookies() if c["domain"].lstrip(".").endswith("weibo.com")}
            if "SUB" in cs and "ALF" in cs:
                logged_in = True
                break
        if not logged_in:
            print("時間内にログインできなかった。最後の状態:", last[0])
            return 3
        try:
            page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001
            pass
        jar = {}
        for c in ctx.cookies("https://weibo.com/"):
            jar[c["name"]] = c["value"]
        b.close()

    cookie = "; ".join(f"{k}={v}" for k, v in jar.items())
    print("[login] weibo.com の cookie:", sorted(jar))
    ok, n = check_cookie(cookie)
    print(f"[check] 东部战区の投稿一覧: {'読めた' if ok else '読めない'}({n}件)")
    if not ok:
        print("新しい cookie で投稿を読めなかったので保存しない。")
        return 5
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    secret = os.environ.get("WEIBO_COOKIE", "").strip()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"secret_hash": hashlib.sha256(secret.encode()).hexdigest(), "cookie": cookie,
                   "updated_at": now, "since": now, "via": "qr-browser"}, f, ensure_ascii=False)
    os.chmod(a.out, 0o600)
    print("保存した。次の収集からこの cookie を使う。")
    summary("**成功**: 新しい cookie を保存した。次の収集から使う。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
