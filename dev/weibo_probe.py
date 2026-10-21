#!/usr/bin/env python3
"""微博の公開アカウント(既定: 东部战区)の投稿が、ログインなしで読めるかを調べる(2026-10-21、調査用)。
訪問者cookie(passport.weibo.com/visitor/genvisitor2)を取り、アカウントの情報と投稿一覧をいくつかの経路で試して、
結果の先頭をログに出すだけ。何も保存しない。  使い方: python dev/weibo_probe.py [アカウント名]"""
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

NAME = sys.argv[1] if len(sys.argv) > 1 else "东部战区"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(url, data=None, headers=None, show=600):
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Referer": "https://weibo.com/"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with op.open(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
            print(f"--- {r.status} {url}\n{body[:show]}\n")
            return body
    except Exception as e:  # noqa: BLE001
        body = getattr(e, "read", lambda: b"")().decode("utf-8", "replace") if hasattr(e, "read") else ""
        print(f"--- ERROR {url}: {e}\n{body[:show]}\n")
        return ""


def visitor():
    b = get("https://passport.weibo.com/visitor/genvisitor2",
            data=urllib.parse.urlencode({"cb": "visitor_gray_callback", "tid": "", "from": "weibo"}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": "https://passport.weibo.com/visitor/visitor"})
    m = re.search(r"\((\{.*\})\)", b, re.S)
    if not m:
        return
    d = json.loads(m.group(1)).get("data") or {}
    for k in ("sub", "subp"):
        if d.get(k):
            jar.set_cookie(http.cookiejar.Cookie(0, k.upper(), d[k], None, False, ".weibo.com", True, True, "/", True,
                                                 False, None, False, None, None, {}))
    print("visitor cookies:", sorted(c.name for c in jar))


def main():
    q = urllib.parse.quote(NAME)
    # 1) ログインも cookie も無しで
    get(f"https://m.weibo.cn/api/container/getIndex?containerid=100103type%3D3%26q%3D{q}&page_type=searchall")
    # 2) 訪問者 cookie を取ってから
    visitor()
    info = get(f"https://weibo.com/ajax/profile/info?screen_name={q}")
    uid = None
    try:
        uid = json.loads(info)["data"]["user"]["idstr"]
    except Exception:  # noqa: BLE001
        m = re.search(r'"idstr"\s*:\s*"(\d+)"', info)
        uid = m and m.group(1)
    print("uid:", uid)
    if uid:
        posts = get(f"https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0", show=300)
        try:
            for s in json.loads(posts)["data"]["list"][:10]:
                text = re.sub(r"<[^>]+>", "", s.get("text_raw") or s.get("text") or "")
                print("POST", s.get("created_at"), s.get("mblogid"), text[:120].replace("\n", " "))
        except Exception as e:  # noqa: BLE001
            print("mymblog parse failed:", e)
        get(f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=107603{uid}", show=800)
        # 3) r.jina.ai 経由(海警局で使っている経路)
        get(f"https://r.jina.ai/https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=107603{uid}",
            headers={"X-No-Cache": "true"}, show=800)


if __name__ == "__main__":
    main()
