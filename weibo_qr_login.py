#!/usr/bin/env python3
"""微博に QR コードでログインし直して、収集用の cookie を作る(2026-11-25、HANDOFF_NOTAM.md §32)。

微博のログイン cookie は約7日で必ず切れ、収集が受け取る Set-Cookie では延びない(2026-11-25 に確認)。
パスワードでの自動ログインは SMS・画像認証を機械で通すことになるのでやらない。代わりに微博の公式の
QR ログイン(passport.weibo.com/sso/v2/qrcode)を使い、人がスマホの微博アプリで読み取って確認する。

  1. QR を作り、読み取り用の URL をログ(と Actions の Summary)に出す
  2. 微博アプリの「扫一扫」で読み取り・確認されるのを最大 5 分待つ
  3. ログインの cookie を受け取り、東部戦区の投稿一覧が読めるか確かめる
  4. 読めたら、収集(news-collect.yml)が使う形で out に書く(Actions のキャッシュに保存する。
     リポジトリ・Pages・ログには cookie の値を出さない)

使い方: python weibo_qr_login.py --out "$RUNNER_TEMP/weibo_cookie/cookie.json"
"""
import argparse
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import sys
import time
import urllib.parse
import urllib.request

PASSPORT = "https://passport.weibo.com"
ENTRY = "miniblog"
REDIRECT = "https://weibo.com/"
QR_VER = "20250520"
OK = 20000000
NOT_SCANNED = 50114001
SCANNED = 50114002
EXPIRED = 50114004
TEST_UID = "7483054836"         # 东部战区(収集と同じ)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, url, params=None, headers=None):
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
             "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
             "Referer": f"{PASSPORT}/sso/signin?entry={ENTRY}&source={ENTRY}&url={REDIRECT}"}
        h.update(headers or {})
        with self.op.open(urllib.request.Request(url, headers=h), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")

    def cookie(self, name, domain_suffix=""):
        return next((c.value for c in self.jar if c.name == name and c.domain.endswith(domain_suffix)), None)

    def weibo_header(self):
        """weibo.com に送る cookie(.weibo.com と weibo.com のもの。値はログに出さない)。"""
        out = {}
        for c in self.jar:
            if c.domain.lstrip(".") in ("weibo.com",) and (c.path or "/") == "/":
                out[c.name] = c.value
        return "; ".join(f"{k}={v}" for k, v in out.items())


def summary(text):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(text + "\n")


def check_cookie(cookie):
    """東部戦区の投稿一覧(収集と同じ API)が読めるか。(ok, 件数) を返す。"""
    xsrf = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("XSRF-TOKEN=")), "")
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": f"https://weibo.com/u/{TEST_UID}",
         "Cookie": cookie, "X-Requested-With": "XMLHttpRequest"}
    if xsrf:
        h["X-XSRF-TOKEN"] = xsrf
    req = urllib.request.Request(f"https://weibo.com/ajax/statuses/mymblog?uid={TEST_UID}&page=1&feature=0", headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as e:
        print(f"[check] 読めなかった: {type(e).__name__}")
        return False, 0
    n = len(((d.get("data") or {}).get("list")) or [])
    return d.get("ok") == 1 and n > 0, n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="収集が読む cookie のファイル(Actions のキャッシュに保存する)")
    ap.add_argument("--wait", type=int, default=300, help="読み取りを待つ秒数")
    a = ap.parse_args(argv)

    s = Session()
    s.get(f"{PASSPORT}/sso/signin", {"entry": ENTRY, "source": ENTRY, "url": REDIRECT})
    csrf = s.cookie("X-CSRF-TOKEN")
    if not csrf:
        print("X-CSRF-TOKEN を受け取れなかった(微博のログインページの形が変わった可能性)")
        return 2
    _, body = s.get(f"{PASSPORT}/sso/v2/qrcode/image", {"entry": ENTRY, "size": "180"}, {"x-csrf-token": csrf})
    d = json.loads(body)
    if d.get("retcode") != OK:
        print("QR を作れなかった:", d.get("retcode"), d.get("msg"))
        return 2
    qrid, image = d["data"]["qrid"], d["data"]["image"]
    scan = urllib.parse.parse_qs(urllib.parse.urlsplit(image).query).get("data", [""])[0]
    print("=" * 60)
    print("微博アプリの「扫一扫」で、次の QR を読み取ってください(5分以内):")
    print("  QR の画像:", image)
    print("  スマホで直接開く場合:", scan)
    print("=" * 60, flush=True)
    summary(f"## 微博の再ログイン\n\n微博アプリの「扫一扫」で読み取ってください（5分以内）。\n\n![QR]({image})\n\n"
            f"スマホで直接開く場合: {scan}\n")

    end = time.time() + a.wait
    last = None
    while time.time() < end:
        time.sleep(3)
        try:
            _, body = s.get(f"{PASSPORT}/sso/v2/qrcode/check",
                            {"entry": ENTRY, "source": ENTRY, "url": REDIRECT, "qrid": qrid, "rid": "", "ver": QR_VER},
                            {"x-csrf-token": csrf})
            d = json.loads(body)
        except (OSError, ValueError) as e:
            print("[poll]", type(e).__name__)
            continue
        rc = d.get("retcode")
        if rc != last:
            print(f"[poll] retcode={rc} msg={d.get('msg')}", flush=True)
            last = rc
        if rc == OK:
            break
        if rc == EXPIRED:
            print("QR の期限が切れた。もう一度実行してください。")
            return 3
    else:
        print("時間内に読み取られなかった。もう一度実行してください。")
        return 3

    data = d.get("data") or {}
    # ログインの仕上げ: 返ってきた URL(crossdomain)をたどると weibo.com の cookie(SUB など)が付く
    for u in [data.get("url")] + ([f"https://login.sina.com.cn/sso/login.php?entry={ENTRY}&alt={data['alt']}&returntype=TEXT"]
                                  if data.get("alt") else []):
        if u:
            try:
                s.get(u, headers={"Accept": "text/html,*/*"})
            except OSError as e:
                print("[finish]", urllib.parse.urlsplit(u).netloc, type(e).__name__)
    try:
        s.get("https://weibo.com/", headers={"Accept": "text/html"})   # XSRF-TOKEN を受け取る
    except OSError:
        pass
    cookie = s.weibo_header()
    names = [c.split("=", 1)[0] for c in cookie.split("; ") if c]
    print("[login] weibo.com の cookie:", names)
    if "SUB" not in names:
        print("ログインの cookie(SUB)を受け取れなかった。")
        return 4
    ok, n = check_cookie(cookie)
    print(f"[check] 东部战区の投稿一覧: {'読めた' if ok else '読めない'}({n}件)")
    if not ok:
        print("新しい cookie で投稿を読めなかった(海外からのログインの追加確認などの可能性)。保存しない。")
        summary("**失敗**: 新しい cookie で投稿を読めなかったので保存しなかった。")
        return 5

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    secret = os.environ.get("WEIBO_COOKIE", "").strip()
    out = {"secret_hash": hashlib.sha256(secret.encode()).hexdigest(), "cookie": cookie,
           "updated_at": now, "since": now, "via": "qr"}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.chmod(a.out, 0o600)
    print("保存した。次の収集からこの cookie を使う。")
    summary("**成功**: 新しい cookie を保存した。次の収集から使う（7日ほどで切れる）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
