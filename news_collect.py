#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_collect.py
===============

ビューア(geoplot-mil.html)の🔔通知に出す「新しい投稿」を集める(2026-10-12〜、HANDOFF_NOTAM.md §27)。

対象(SOURCES):
  ccg  中国海警局「海警要闻」  https://www.ccg.gov.cn/hjyw/
       一覧ページのHTMLから、記事へのリンク(<月>/t<日付>_<番号>.html)と見出しを拾う。
  mna  国防部 軍聞社「國防訊息」 https://mna.mnd.gov.tw/news/information/
       一覧と同じ記事を RSS(https://mna.mnd.gov.tw/K2S/RSS、最近3か月の全カテゴリ)から取り、
       カテゴリ(dc:subject)が「國防訊息」のものだけを使う(一覧ページのHTMLより構造が安定しているため)。

出力: news_out/news.json(リポジトリには暗号化した news_out/news.json.enc だけを置く。data_crypt.py)
  {"sources": {id: {"name", "url", "last_ok_date", "last_error", "last_error_at"}},
   "items": [{"id", "source", "title", "url", "published", "first_seen"}, ...]}   新しい順、ソースごとに最大 KEEP_PER_SOURCE 件
  ビューアは items を読み、まだ見ていない id を通知に出す(初めて読んだときはその時点の分を既読にする)。

取得に失敗したソースは前回の items をそのまま残し、last_error に理由を書く。全ソースが失敗したら終了コード 2。
使い方: python news_collect.py [--out-dir news_out] [--fixture-dir DIR]   (fixture: <id>.html / <id>.xml を読む。テスト用)
"""

import argparse
import datetime as dt
import email.utils
import html
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

KEEP_PER_SOURCE = 100
TIMEOUT = 40
UA = "Mozilla/5.0 (compatible; geoplot-news/1.0)"

SOURCES = {
    "ccg": {"name": "中国海警局 海警要闻", "url": "https://www.ccg.gov.cn/hjyw/", "fetch": "https://www.ccg.gov.cn/hjyw/"},
    "mna": {"name": "國防部 軍聞社 國防訊息", "url": "https://mna.mnd.gov.tw/news/information/",
            "fetch": "https://mna.mnd.gov.tw/K2S/RSS", "category": "國防訊息"},
}


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh-TW;q=0.9,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ----------------------------------------------------------------------------- 中国海警局
_CCG_LINK = re.compile(r"<a\b([^>]*?)href\s*=\s*[\"']([^\"']*?(\d{6})/t(\d{8})_(\d+)\.html)[\"']([^>]*)>(.*?)</a>", re.S | re.I)
_TITLE_ATTR = re.compile(r"title\s*=\s*[\"']([^\"']+)[\"']", re.I)


def parse_ccg(text, base):
    """一覧HTMLから記事を拾う。同じ記事へのリンクが画像・見出しで複数あるので、見出しのあるものを採る。
    日時はリンクの近く(後ろ200文字以内)の「YYYY-MM-DD HH:MM(:SS)」、無ければURLの日付。"""
    found = {}
    for m in _CCG_LINK.finditer(text):
        attrs, href, _ym, ymd, num, attrs2, inner = m.groups()
        title_m = _TITLE_ATTR.search(attrs + " " + attrs2)
        title = strip_tags(title_m.group(1)) if title_m else strip_tags(inner)
        if not title:
            continue
        near = re.search(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2}(?::\d{2})?))?", text[m.end():m.end() + 200])
        if near:
            published = near.group(1) + ("T" + near.group(2) if near.group(2) else "")
        else:
            published = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
        url = urljoin(base, href)
        if "/hjyw/" not in url:          # 横の欄(维权执法・国际合作など)の記事は除く
            continue
        iid = f"ccg:{ymd}_{num}"
        if iid not in found or len(title) > len(found[iid]["title"]):
            found[iid] = {"id": iid, "source": "ccg", "title": title, "url": url, "published": published}
    return list(found.values())


# ----------------------------------------------------------------------------- 國防部 軍聞社(RSS)
def _tag(item, name):
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", item, re.S | re.I)
    if not m:
        return ""
    v = m.group(1).strip()
    cd = re.match(r"<!\[CDATA\[(.*)\]\]>$", v, re.S)
    return html.unescape(cd.group(1) if cd else v).strip()


def parse_mna_rss(text, category):
    out = []
    for item in re.split(r"<item\b[^>]*>", text, flags=re.I)[1:]:
        item = re.split(r"</item>", item, flags=re.I)[0]
        if category and category not in _tag(item, "dc:subject") and category not in _tag(item, "category"):
            continue
        guid = _tag(item, "guid")
        title = strip_tags(_tag(item, "title"))
        link = _tag(item, "link")
        if not link and guid:
            link = f"https://mna.mnd.gov.tw/news/detail/?UserKey={guid}"
        key = guid or (re.search(r"UserKey=([\w-]+)", link or "") or [None, None])[1]
        if not key or not title:
            continue
        published = ""
        pd = _tag(item, "pubDate")
        if pd:
            try:
                published = email.utils.parsedate_to_datetime(pd).isoformat()
            except (TypeError, ValueError):
                published = pd
        out.append({"id": f"mna:{key}", "source": "mna", "title": title, "url": link, "published": published})
    return out


PARSERS = {
    "ccg": lambda text, src: parse_ccg(text, src["fetch"]),
    "mna": lambda text, src: parse_mna_rss(text, src.get("category")),
}


# ----------------------------------------------------------------------------- 本体
def merge(state, sid, fetched, now):
    """新しく見つかったものに first_seen を付けて足し、ソースごとに新しい順で KEEP_PER_SOURCE 件に切る。足した件数を返す。"""
    old = {it["id"]: it for it in state["items"] if it["source"] == sid}
    added = 0
    for it in fetched:
        if it["id"] in old:
            prev = old[it["id"]]
            it["first_seen"] = prev.get("first_seen") or now
        else:
            it["first_seen"] = now
            added += 1
        old[it["id"]] = {**old.get(it["id"], {}), **it}
    mine = sorted(old.values(), key=lambda x: (x.get("published") or "", x["id"]), reverse=True)[:KEEP_PER_SOURCE]
    others = [it for it in state["items"] if it["source"] != sid]
    state["items"] = sorted(others + mine, key=lambda x: (x.get("published") or "", x["id"]), reverse=True)
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="news_out")
    ap.add_argument("--fixture-dir", help="取得の代わりに <id>.html / <id>.xml を読む(テスト用)")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    path = out / "news.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"sources": {}, "items": []}
    state.setdefault("sources", {})
    state.setdefault("items", [])
    now = now_iso()
    ok = 0
    summary = {}
    for sid, src in SOURCES.items():
        meta = state["sources"].setdefault(sid, {})
        meta.update({"name": src["name"], "url": src["url"]})
        try:
            if a.fixture_dir:
                fx = next((p for p in (Path(a.fixture_dir) / f"{sid}.html", Path(a.fixture_dir) / f"{sid}.xml") if p.exists()), None)
                if not fx:
                    raise FileNotFoundError(f"fixture {sid}.html/.xml が無い")
                text = fx.read_text(encoding="utf-8")
            else:
                text = http_get(src["fetch"])
            items = PARSERS[sid](text, src)
            if not items:
                raise ValueError("記事を1件も読み取れなかった(ページの作りが変わった可能性)")
            added = merge(state, sid, items, now)
            # 成功時刻を毎回書くと15分ごとにコミットが出るので、日付だけ持つ(新着・エラー・日付が変わったときだけファイルが変わる)
            meta.update({"last_ok_date": now[:10], "last_error": None, "last_error_at": None})
            summary[sid] = {"fetched": len(items), "new": added}
            ok += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            meta.update({"last_error": f"{type(e).__name__}: {e}", "last_error_at": now})
            summary[sid] = {"error": meta["last_error"]}
            print(f"[WARN] {sid}: {meta['last_error']}", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
