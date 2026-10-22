#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_collect.py
===============

ビューア(geoplot-mil.html)の🔔通知に出す「新しい投稿」を集める(2026-10-12〜、HANDOFF_NOTAM.md §27)。

対象(SOURCES)。投稿は全部保存し、見出しが「通知する見出し」(NOTIFY_RULES、ユーザー指定 2026-10-12)に当たるものに
notify=true を付ける。ビューアは notify のものだけを🔔に出す。
  ccg_hjyw  中国海警局「海警要闻」 https://www.ccg.gov.cn/hjyw/
  ccg_wqzf  中国海警局「维权执法」 https://www.ccg.gov.cn/wqzf/
            ユーザー指定は 海警要闻→「中国海警位中国台湾岛以东海域依法开展常态化执法巡查」、维权执法→「在我钓鱼岛」「金门」。
            同じ種類の記事がどちらの一覧に載ることもある(例: 台湾岛以东 の 2026-09-02 は维权执法だけ)ので、3つとも両方の一覧に使う。
            どちらも一覧ページのHTMLから、記事へのリンク(<月>/t<日付>_<番号>.html)と見出し(リンクの文字)を拾う。
            同じ記事が両方の一覧に載ることがある(id は同じ。どちらかの規則に当たれば notify)。
  mna       国防部 軍聞社「國防訊息」 https://mna.mnd.gov.tw/news/information/ → 「聯合戰備警巡」
            一覧と同じ記事を RSS(https://mna.mnd.gov.tw/K2S/RSS、最近3か月の全カテゴリ)から取り、
            カテゴリ(dc:subject)が「國防訊息」のものだけを使う(一覧ページのHTMLより構造が安定しているため)。

  jfjb      解放军报(数字报) http://www.81.cn/szb_223187/szblb/index.html(2026-10-22〜)。紙面のページは JavaScript で、
            1日分の全版・全記事(見出し・副題・著者・本文HTML・写真)を1つの JSON(/_szb/jfjb/YYYY/MM/DD/index.json)から描いている。
            最新号の日付は https://rmt-zuul.81.cn/api-paper/api/newestPaper。毎回、最新号を読み直し(後から足された記事も拾う)、
            直近 JFJB_RECHECK_DAYS 日で読めていない日・最新号でなくなってから読み直していない日も読む(取りこぼし防止)。
            1日分を news_out/jfjb/YYYY-MM-DD.json(版ごとに記事の本文まで)に、日ごとの版の一覧を news_out/jfjb/index.json に書く。
            記事数が多い(1日60本前後)ので news.json の items には入れない(通知の規則 JFJB_NOTIFY に当たったものだけ入れる)。
  weibo     东部战区の微博 https://weibo.com/u/7483054836(2026-10-22〜)。投稿一覧はログインが要るので、ユーザーの微博アカウントの
            cookie を GitHub Secret WEIBO_COOKIE で渡す(環境変数)。投稿は全部 items に入れ、WEIBO_NOTIFY の語句に当たるものだけ通知
            (毎日の定時の投稿 WEIBO_EXCLUDE は除く)。cookie が切れたら sources.weibo.auth_error_at に日付を書く(ビューアが表示する)。

出力: news_out/news.json(リポジトリには暗号化した news_out/news.json.enc だけを置く。data_crypt.py)
  {"sources": {id: {"name", "url", "last_ok_date", "last_error", "last_error_at"}},
   "items": [{"id", "source", "page", "title", "url", "published", "first_seen", "notify", "matched"}, ...]}
  新しい順、全件を蓄積する(2026-10-12〜。通知する見出しを後で決めるため)。page は見つかった一覧(ccg_hjyw / ccg_wqzf / mna)。
  backfilled: 遡って集めた分(--backfill / --import-json)。ビューアはこれを通知しない。
  ビューアは notify の付いた、まだ見ていない id を通知に出す(一覧ごとに、初めて読んだときはその時点の分を既読にする)。

取得に失敗したソースは前回の items をそのまま残し、last_error に理由を書く。全ソースが失敗したら終了コード 2。
使い方: python news_collect.py [--out-dir news_out] [--fixture-dir DIR]   (fixture: <page>.html / <page>.xml を読む。テスト用)
        python news_collect.py --backfill mna           # 一覧を最後のページまで遡る(軍聞社: ?PageIndex=1,2,…)
        python news_collect.py --backfill ccg           # 中国海警局: index.html, index_1.html, …(海外からはWAFで拒否されやすい)
        python news_collect.py --import-json FILE       # 別の手段で集めた中国海警局の記事 [{"title","url","date"}] を取り込む
        python news_collect.py --backfill jfjb          # 解放军报を 2026-01-01(数字报で選べる最初の日)から読めていない日まで遡る
"""

import argparse
import base64
import datetime as dt
import http.cookiejar
import time
import email.utils
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

TIMEOUT = 40
RETRIES = 3
RETRY_WAIT = 8          # 秒。失敗のたびに 8・16 秒待って取り直す
PAGE_GAP = 3            # 秒。同じサイトの一覧を続けて読むときの間隔
# 中国海警局は、ボットらしいアクセスに HTTP 418 を返すことがある(2026-10-12、Actions の2回目で発生)。
# 航行警報側(msa_scraper.py)と同じく、普通のブラウザに近いヘッダーで、トップページを先に開いて Cookie を受け取り、
# Referer を付けて一覧を読む。
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh-TW;q=0.9,zh;q=0.8,en;q=0.5",
}
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_warmed = set()

# 中国海警局の2つの一覧に共通の、通知する見出し(正規表現、どれかに当たれば通知)。
# 「台湾岛以东」はユーザー指定の「…依法开展常态化执法巡查」のほか、「依法」の無い見出し(2026-07-04)もあったので両方
# 2026-10-21: 「中国台湾岛以东海域(依法)开展常态化执法巡查」は通知・赤丸の対象から外した(ユーザー指示)
CCG_NOTIFY = [r"在我钓鱼岛", r"金门"]
# source(保存の単位) -> 読む一覧(page)。page ごとに、通知する見出しの規則(None は CCG_NOTIFY)
SOURCES = {
    "ccg": {"name": "中国海警局", "pages": [
        {"page": "ccg_hjyw", "name": "中国海警局 海警要闻", "url": "https://www.ccg.gov.cn/hjyw/", "kind": "ccg",
         "backfill": "https://www.ccg.gov.cn/hjyw/index_{m}.html", "backfill_kind": "ccg", "notify": None, "mirror": True},
        {"page": "ccg_wqzf", "name": "中国海警局 维权执法", "url": "https://www.ccg.gov.cn/wqzf/", "kind": "ccg",
         "backfill": "https://www.ccg.gov.cn/wqzf/index_{m}.html", "backfill_kind": "ccg", "notify": None, "mirror": True},
    ]},
    "mna": {"name": "國防部 軍聞社", "pages": [
        {"page": "mna", "name": "國防部 軍聞社 國防訊息", "url": "https://mna.mnd.gov.tw/news/information/",
         "fetch": "https://mna.mnd.gov.tw/K2S/RSS", "kind": "mna_rss", "category": "國防訊息",
         "backfill": "https://mna.mnd.gov.tw/news/information/?PageIndex={n}", "backfill_kind": "mna_list",
         "notify": [r"聯合戰備警巡"]},
    ]},
}


# 解放军报(2026-10-22〜、ユーザー指示「解放军报の自動収集、取りこぼさないように」)
JFJB_NAME = "解放军报"
JFJB_NEWEST = "https://rmt-zuul.81.cn/api-paper/api/newestPaper"
JFJB_DAY = "http://www.81.cn/_szb/jfjb/{y}/{m}/{d}/index.json"
JFJB_PAGE_URL = "http://www.81.cn/szb_223187/szblb/index.html?paperNumber={no}&paperName=jfjb&paperDate={date}"
JFJB_ARTICLE_URL = "http://www.81.cn/szb_223187/szbxq/index.html?paperName=jfjb&paperDate={date}&paperNumber={no}&articleid={id}"
JFJB_FIRST_DAY = "2026-01-01"       # 数字报の「往期回顾」で選べる最初の日
JFJB_RECHECK_DAYS = 7               # 毎回、この日数ぶん遡って、読めていない日・読み直していない日を読む
JFJB_BACKFILL_SECONDS = 20 * 60     # 遡りは1回の実行でこの時間まで(1日分の読み込みに6〜9秒。全部は30分の制限に収まらないので、何回かに分けて実行する)
JFJB_DIR = "jfjb"
JFJB_FORMAT = 3                     # 1日分のファイルの形。変えたら読み直す(2: 版面の画像 img、3: 記事の紙面上の位置 area。2026-10-22)
JFJB_NOTIFY = []                    # 通知する見出し(正規表現)。未定(2026-10-22)。当たった記事だけ news.json の items に入れる

# 东部战区の微博(2026-10-22〜、ユーザー指示「一覧は全部、語句に合うものだけ通知」)
WEIBO_UID = "7483054836"            # 东部战区(下の WEIBO_ACCOUNTS の先頭)
WEIBO_NAME = "微博"
# 見張る微博のアカウント(2026-10-22 央视军事を追加、ユーザー指示)。page はビューアのタブ
WEIBO_ACCOUNTS = [
    {"uid": "7483054836", "name": "东部战区", "page": "weibo_dbzq"},
    {"uid": "6189120710", "name": "央视军事", "page": "weibo_ysjs"},
]
WEIBO_LIST = "https://weibo.com/ajax/statuses/mymblog?uid={uid}&page=1&feature=0"
WEIBO_LONGTEXT = "https://weibo.com/ajax/statuses/longtext?id={mid}"
WEIBO_NOTIFY = [r"演习", r"联合", r"台岛", r"台湾", r"海峡", r"战备警巡", r"正义使命", r"海峡雷霆"]   # ユーザー確認済み(2026-10-22)
WEIBO_EXCLUDE = [r"#东部熄灯号#", r"#直击东部战区练兵备战现场#"]   # 毎日の定時の投稿は通知しない


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _get_once(url, referer=None):
    headers = dict(HEADERS, **({"Referer": referer} if referer else {}))
    with _opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def http_get(url):
    """サイトごとに1回、トップページを開いて Cookie を受け取ってから、Referer 付きで読む。失敗したら待って取り直す。"""
    top = re.match(r"https?://[^/]+/", url).group(0)
    if top not in _warmed:
        _warmed.add(top)
        try:
            _get_once(top)
        except (urllib.error.URLError, OSError):
            pass                       # トップが読めなくても本番は試す
    last = None
    for i in range(RETRIES):
        try:
            return _get_once(url, referer=top)
        except (urllib.error.URLError, OSError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError):       # どんな拒否か(JavaScriptの検査ページ等)をログで見られるよう、本文の先頭を出す
                try:
                    body = e.read(600).decode("utf-8", "replace")
                except OSError:
                    body = ""
                print(f"[HTTP {e.code}] {url} headers={dict(e.headers)} body={body!r}", file=sys.stderr)
                if e.code in (403, 418):    # ファイアウォールの拒否(中国海警局の CloudWAF「访问被拦截！」は418)。取り直しても同じなので諦める
                    break
            if i < RETRIES - 1:
                time.sleep(RETRY_WAIT * (i + 1))
    raise last


# 2026-10-19: 中国海警局は GitHub Actions から 418(CloudWAF)で拒否され続けている(HANDOFF_NOTAM.md §27.3)。
# 直接が拒否されたときだけ、別の場所から読んでもらう(どちらも無料・キー不要):
#   jina    … r.jina.ai(ページを代わりに読んで返すサービス。X-Return-Format: html で元のHTMLに近い形)
#   wayback … Internet Archive の最新の保存版(web.archive.org/web/2/<URL>。古いことがある)
# (名前, URLの作り方, 追加ヘッダー, 試す回数)。jina は取りに行く場所が毎回変わるらしく、拒否されても取り直すと通ることがある
# (2026-10-19 の Actions: 维权执法は jina で読めたが、海警要闻は jina も「访问被拦截」・wayback は 429)
MIRRORS = [
    # 出力形式の指定は、今のREADMEでは x-respond-with。旧名の X-Return-Format も効いている(2026-10-19 に HTML が返った)ので両方付ける
    # x-no-cache: jina の一時保存(最大1時間)を使わず、毎回取りに行く(新着が遅れないように。2026-10-19 ユーザー指示)
    ("jina", lambda url: "https://r.jina.ai/" + url,
     {"X-Respond-With": "html", "X-Return-Format": "html", "X-No-Cache": "true"}, 3),
    ("wayback", lambda url: "https://web.archive.org/web/2/" + url, {}, 1),
]
MIRROR_TIMEOUT = 60
MIRROR_RETRY_WAIT = 5


def fetch_page(url, mirror=False):
    """(本文, 経路)。経路は 'direct' か MIRRORS の名前。mirror=True のページは、直接が 403/418 で拒否されたら MIRRORS を順に試す。"""
    try:
        return http_get(url), "direct"
    except urllib.error.HTTPError as e:
        if not mirror or e.code not in (403, 418):
            raise
        first = e
    errs = [f"direct: HTTP {first.code}"]
    for name, make, extra, tries in MIRRORS:
        for k in range(tries):
            if k:
                time.sleep(MIRROR_RETRY_WAIT)
            try:
                req = urllib.request.Request(make(url), headers=dict(HEADERS, **extra))
                with urllib.request.urlopen(req, timeout=MIRROR_TIMEOUT) as r:
                    text = r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")
                if "访问被拦截" in text:                          # 中継先もファイアウォールに止められた
                    raise ValueError("访问被拦截")
                return text, name if k == 0 else f"{name}#{k + 1}"
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"[mirror] {name} try {k + 1} {url}: {e}", file=sys.stderr)
                if k == tries - 1:
                    errs.append(f"{name}: {type(e).__name__}: {e}")
    raise urllib.error.URLError("; ".join(errs))


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
        # 2026-10-20: 一覧の本体の記事だけを拾う。本体は「YYYY-MM-DD HH:MM:SS」付き、横の欄(過去の特集など)は日時なし。
        # 以前は横の欄の2025年の記事も新着として入り、通知の語句に当たるものもあった。
        near = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)", text[m.end():m.end() + 200])
        if not near:
            continue
        published = near.group(1) + "T" + near.group(2)
        url = re.sub(r"^https?://web\.archive\.org/web/[^/]+/", "", urljoin(base, href))   # 保存版のリンクを元のURLに戻す
        if "/hjyw/" not in url and "/wqzf/" not in url:      # 横の欄(国际合作など)の記事は除く
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
        summary = strip_tags(_tag(item, "summary"))           # 一覧に出る要約(報道タブで使う。v4.1.0〜)
        out.append({"id": f"mna:{key}", "source": "mna", "title": title, "url": link, "published": published,
                    "summary": summary})
    return out


# 軍聞社の一覧ページ(?PageIndex=n)。<a href="/news/detail/?UserKey=…" title="見出し"> … <span class="time">民國115年10月04日</span>
_MNA_LIST = re.compile(r"<a\b[^>]*href=[\"']([^\"']*UserKey=([\w-]+))[^\"']*[\"'][^>]*>(.*?)</a>", re.S | re.I)


def parse_mna_list(text, base="https://mna.mnd.gov.tw/"):
    out, seen = [], set()
    for m in _MNA_LIST.finditer(text):
        href, key, inner = m.groups()
        if key in seen:
            continue
        tm = _TITLE_ATTR.search(m.group(0)[:m.group(0).find(">") + 1])
        title_div = re.search(r'class=["\']title["\'][^>]*>(.*?)</div>', inner, re.S)
        title = strip_tags(title_div.group(1)) if title_div else (strip_tags(tm.group(1)) if tm else "")
        if not title:
            continue
        d = re.search(r"民國(\d{2,3})年(\d{1,2})月(\d{1,2})日", inner)
        published = f"{int(d.group(1)) + 1911:04d}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else ""
        seen.add(key)
        sm = re.search(r'class=["\']summary["\'][^>]*>(.*?)</div>', inner, re.S)
        out.append({"id": f"mna:{key}", "source": "mna", "title": title, "url": urljoin(base, href), "published": published,
                    "summary": strip_tags(sm.group(1)) if sm else ""})
    return out


# ----------------------------------------------------------------------------- 記事本文の写し(v4.4.0、2026-10-20)
# 元のサイトに入れないときのために、記事ページの見出し・日付・写真(縮小して埋め込み)・本文を news_out/articles/ に保存する。
# ビューアは報道タブで見出しを押すとこの写しを元サイト風に表示し、PDF にもできる。
ARTICLE_DIR = "articles"
ARTICLE_MAX_PER_RUN = 6         # 1回の収集で新しく写しを作る最大数(遡り分もこの数ずつ埋まっていく)
ARTICLE_MAX_PER_RUN_CCG = 3     # 中国海警局は記事ページも拒否されがち(2026-10-20 の実績で約6割は読めた)。1件に最大20秒ほどかかる
ARTICLE_MAX_TRIES = 5           # 読めない・本文が取れない記事は、この回数で一旦やめ、日が変わったらまた1回だけ試す
IMG_MAX_W = 960
IMG_QUALITY = 72


def _block(text, start_re, tag):
    """start_re に当たる開始タグから、対応する閉じタグまで(同じタグの入れ子を数える)を返す。"""
    m = re.search(start_re, text, re.I | re.S)
    if not m:
        return ""
    depth, i = 0, m.start()
    for t in re.finditer(rf"<(/?){tag}\b[^>]*>", text[m.start():], re.I):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return text[m.start():m.start() + t.end()]
    return text[m.start():]


def _paragraphs(block):
    out = []
    for p in re.findall(r"<p\b[^>]*>(.*?)</p>", block, re.S | re.I):
        t = strip_tags(re.sub(r"(?i)<br\s*/?>", "\n", p))
        if t:
            out.append(t)
    return out


def _figures(block, base):
    figs, seen = [], set()
    for f in re.findall(r"<figure\b[^>]*>(.*?)</figure>", block, re.S | re.I):
        im = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", f, re.I)
        if not im:
            continue
        src = urljoin(base, html.unescape(im.group(1)))
        if src in seen:
            continue
        seen.add(src)
        cap = re.search(r"<figcaption\b[^>]*>(.*?)</figcaption>", f, re.S | re.I)
        figs.append({"src": src, "caption": strip_tags(cap.group(1)) if cap else ""})
    return figs


def parse_mna_article(text, url):
    """軍聞社の記事ページ。見出し(h2)・分類(tagBlk)・民國の日付(info)・<article> の写真と本文・關鍵字。"""
    title_m = re.search(r'class=["\']title["\'][^>]*>\s*<h2[^>]*>(.*?)</h2>', text, re.S | re.I)
    tag_m = re.search(r'class=["\']tagBlk["\'][^>]*>.*?<li[^>]*>(.*?)</li>', text, re.S | re.I)
    date_m = re.search(r"民國\d{2,3}年\d{1,2}月\d{1,2}日", text)
    art = _block(text, r"<article\b", "article")
    art_main = re.sub(r'(?is)<div class="modal\b.*?</figure>\s*</div>\s*</div>', "", art)   # 拡大表示用の同じ写真は除く
    kw_block = _block(art, r'class=["\']keyWordContent', "div")
    paras = _paragraphs(re.split(r'class=["\'][^"\']*keywordBlk', art_main)[0])
    return {"site": "國防部 軍聞社", "category": strip_tags(tag_m.group(1)) if tag_m else "",
            "title": strip_tags(title_m.group(1)) if title_m else "", "date": date_m.group(0) if date_m else "",
            "figures": _figures(art_main, url), "paragraphs": paras,
            "keywords": [strip_tags(k) for k in re.findall(r"<a\b[^>]*>(.*?)</a>", kw_block, re.S)]}


def parse_ccg_article(text, url):
    """中国海警局の記事ページ(作りは未確認。2026-10-20 時点で GitHub からは記事ページを読めていない)。
    見出しは <h1>/<h2>/ArticleTitle/<title>、日時は最初の「YYYY-MM-DD HH:MM」、本文は TRS_Editor か、本文らしい <p> の集まり。"""
    title = ""
    for pat in (r'<meta\s+name=["\']ArticleTitle["\']\s+content=["\']([^"\']+)', r"<h1\b[^>]*>(.*?)</h1>",
                r"<h2\b[^>]*>(.*?)</h2>", r"<title>(.*?)</title>"):
        m = re.search(pat, text, re.S | re.I)
        if m and strip_tags(m.group(1)):
            title = re.sub(r"\s*[-_|].*$", "", strip_tags(m.group(1))) if "title>" in pat else strip_tags(m.group(1))
            break
    date_m = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?", text)
    src_m = re.search(r"来源[：:]\s*([^<\s]{2,20})", text)
    body = _block(text, r'<div\b[^>]*class=["\'][^"\']*TRS_Editor', "div") or \
        _block(text, r'<div\b[^>]*(?:id|class)=["\'][^"\']*(?:article|content|zoom)[^"\']*["\']', "div")
    paras = [p for p in _paragraphs(body or text) if len(p) >= 8 and "版权所有" not in p and "备案号" not in p]
    figs = []
    for im in re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", body or "", re.I):
        figs.append({"src": urljoin(url, html.unescape(im)), "caption": ""})
    return {"site": "中国海警局", "category": "", "title": title, "date": date_m.group(0) if date_m else "",
            "source": src_m.group(1) if src_m else "", "figures": figs[:4], "paragraphs": paras, "keywords": []}


def tidy_ccg_article(art, item_title):
    """中国海警局の写しを整える(2026-10-20、初めて読めた記事 t20261010_3249 で確認):
    - 見出しは一覧の見出しを使う(ページの h1 などはサイト名「中国海警局」だった)
    - 本文の先頭にある見出しの繰り返しを除き、その次の英語の見出し(句点で終わらない英文1行)は副題にする
      (本文は中国語と英語の段落が交互に並ぶ)"""
    if item_title:
        art["title"] = item_title
    paras = list(art.get("paragraphs") or [])
    if paras and paras[0].strip() == art["title"].strip():
        paras.pop(0)
        if paras and re.fullmatch(r"[\x20-\x7e’‘“”]+", paras[0]) and not paras[0].rstrip().endswith("."):
            art["subtitle"] = paras.pop(0)
    art["paragraphs"] = paras
    return art


def _get_bytes(url, referer=None):
    headers = dict(HEADERS, **({"Referer": referer} if referer else {}))
    with _opener.open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
        return r.read(), (r.headers.get_content_type() or "image/jpeg")


def image_data_uri(url, referer=None):
    """写真を読み、横 IMG_MAX_W 以下の JPEG にして data: URI で返す(Pillow が無ければ 400KB 以下ならそのまま)。"""
    raw, ctype = _get_bytes(url, referer)
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im = im.convert("RGB")
        if im.width > IMG_MAX_W:
            im = im.resize((IMG_MAX_W, round(im.height * IMG_MAX_W / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=IMG_QUALITY, optimize=True)
        raw, ctype = buf.getvalue(), "image/jpeg"
    except ImportError:
        if len(raw) > 400_000:
            raise ValueError("写真が大きすぎる(Pillow が無いので縮小できない)")
    except OSError as e:
        raise ValueError(f"写真を読めない: {e}")
    return f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")


def article_file(item_id):
    return f"{ARTICLE_DIR}/{re.sub(r'[^A-Za-z0-9_-]', '_', item_id)}.json"


def collect_articles(state, out, now, fetch=None):
    """写しの無い記事について記事ページを読み、news_out/articles/<id>.json に保存して、item に article(ファイル名)を付ける。
    通知対象 → 新しい順。読めない・本文が取れないときは article_tries を増やし、ARTICLE_MAX_TRIES 回で諦める。"""
    fetch = fetch or (lambda url: fetch_page(url, mirror="ccg.gov.cn" in url)[0])
    # tidy_ccg_article より前に作った海警局の写し(見出しが「中国海警局」)を整え直す
    for it in state["items"]:
        if it.get("source") == "ccg" and it.get("article") and (out / it["article"]).exists():
            path = out / it["article"]
            art = json.loads(path.read_text(encoding="utf-8"))
            if art.get("title") != it.get("title"):
                path.write_text(json.dumps(tidy_ccg_article(art, it.get("title")), ensure_ascii=False, sort_keys=True) + "\n",
                                encoding="utf-8")
    today = now[:10]
    todo = [it for it in state["items"] if not it.get("article") and it.get("url")
            and (it.get("article_tries", 0) < ARTICLE_MAX_TRIES or it.get("article_last_try") != today)]
    todo.sort(key=lambda it: it.get("published") or "", reverse=True)        # 新しい順
    todo.sort(key=lambda it: 0 if it.get("notify") else 1)                    # 通知対象を先に(並べ替えは安定なので新しい順は保たれる)
    done, quota = {"mna": 0, "ccg": 0}, {"mna": ARTICLE_MAX_PER_RUN, "ccg": ARTICLE_MAX_PER_RUN_CCG}
    made = 0
    for it in todo:
        src = it.get("source")
        if src not in quota or done[src] >= quota[src]:
            continue
        done[src] += 1
        try:
            text = fetch(it["url"])
            art = (parse_mna_article if src == "mna" else parse_ccg_article)(text, it["url"])
            if src == "ccg":
                tidy_ccg_article(art, it.get("title"))
            if not art["paragraphs"]:
                raise ValueError("本文を読み取れなかった")
            for fig in art["figures"]:
                try:
                    fig["data"] = image_data_uri(fig["src"], referer=it["url"])
                except (urllib.error.URLError, OSError, ValueError) as e:
                    fig["error"] = f"{type(e).__name__}: {e}"
            art.update({"id": it["id"], "url": it["url"], "fetched_at": now})
            path = out / article_file(it["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(art, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            it["article"] = article_file(it["id"])
            it.pop("article_tries", None)
            it.pop("article_last_try", None)
            made += 1
        except (urllib.error.URLError, OSError, ValueError) as e:
            it["article_tries"] = it.get("article_tries", 0) + 1
            if it["article_tries"] >= ARTICLE_MAX_TRIES:
                it["article_last_try"] = today          # 諦めた記事は、日付が変わるまで試さない(日付だけなのでコミットは日に1回)
            print(f"[article] {it['id']} {it['url']}: {type(e).__name__}: {e}", file=sys.stderr)
    return made


PARSERS = {
    "ccg": lambda text, pg: parse_ccg(text, pg["url"]),
    "mna_rss": lambda text, pg: parse_mna_rss(text, pg.get("category")),
    "mna_list": lambda text, pg: parse_mna_list(text),
}


def notify_match(title, rules):
    """見出しが通知の規則のどれかに当たれば、当たった部分を返す(無ければ None)。"""
    for rx in rules:
        m = re.search(rx, title or "")
        if m:
            return m.group(0)
    return None


# ----------------------------------------------------------------------------- 解放军报
def _jfjb_get(url):
    """JSON を読む。404 は取り直さない(その日の紙面が無い)。それ以外の失敗は待って取り直す。"""
    last = None
    for i in range(RETRIES):
        try:
            return json.loads(_get_once(url, referer="http://www.81.cn/"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
        if i < RETRIES - 1:
            time.sleep(RETRY_WAIT * (i + 1))
    raise last


def _jfjb_figures(content):
    """本文HTMLの写真(<div class="attachment-image"><table><tr><td><img></td></tr><tr><td>説明</td></tr></table>)。
    写真そのものは保存しない(1日60本前後あり、リポジトリが大きくなるため)。元の URL と説明だけ持ち、ビューアが元のサイトから直接読む。"""
    figs = []
    for blk in re.findall(r"<table\b.*?</table>", content, re.S | re.I):
        im = re.search(r"<img\b[^>]*\bsrc=[\"']([^\"']+)", blk, re.I)
        if im:
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", blk, re.S | re.I)
            cap = " ".join(t for t in (strip_tags(c) for c in cells) if t)
            figs.append({"src": html.unescape(im.group(1)), "caption": cap})
    return figs


def _jfjb_area(points):
    """記事の紙面上の位置。point は "x,y"(版面の画像の幅・高さに対する %)の並び → [[x, y], ...](小数2桁)。"""
    out = []
    for p in points or []:
        try:
            x, y = (float(v) for v in str(p).split(","))
        except ValueError:
            continue
        out.append([round(x, 2), round(y, 2)])
    return out if len(out) >= 3 else []


def parse_jfjb_day(data, date):
    """index.json(1日分)を、版ごとの記事の一覧(本文は段落に分けて)にする。"""
    pages = []
    for pg in sorted(data.get("paperInfo") or [], key=lambda p: str(p.get("paperNumber"))):
        no = str(pg.get("paperNumber") or "")
        arts = []
        for xy in pg.get("xyList") or []:
            content = xy.get("content") or ""
            body = re.sub(r"(?is)<table\b.*?</table>", "", content)
            paras = _paragraphs(body) or [t for t in (strip_tags(x) for x in re.split(r"(?i)<br\s*/?>", body)) if t]
            arts.append({"id": f"jfjb:{date}_{xy.get('id')}", "title": strip_tags(xy.get("title") or ""),
                         "subtitle": strip_tags(xy.get("title2") or ""), "guide": strip_tags(xy.get("guideTitle") or ""),
                         "author": strip_tags(xy.get("author") or ""), "kind": xy.get("type") or "",
                         "paragraphs": paras, "figures": _jfjb_figures(content), "area": _jfjb_area(xy.get("point")),
                         "url": JFJB_ARTICLE_URL.format(date=date, no=no, id=xy.get("id"))})
        imgs = {i.get("typeName"): i.get("url") for i in pg.get("imgList") or []}
        pages.append({"no": no, "name": strip_tags(pg.get("paperBk") or ""), "pdf": pg.get("paperPDF") or "",
                      "img": imgs.get("标准图") or pg.get("paperImg") or "", "img_large": imgs.get("真图") or pg.get("paperImg") or "",
                      "url": JFJB_PAGE_URL.format(no=no, date=date), "articles": arts})
    return {"date": date, "paper": JFJB_NAME, "pages": pages}


def _write_if_changed(path, obj):
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def collect_jfjb(state, out, now, backfill=False, get=None, newest=None, budget=None):
    """解放军报を読む。最新号は毎回、直近 JFJB_RECHECK_DAYS 日は読めていない日・最新号のうちに読んだきりの日を読む
    (backfill のときは JFJB_FIRST_DAY から)。読めた日は news_out/jfjb/<日付>.json と index.json を書き、
    通知の規則に当たった記事だけ news.json の items に足す。(読んだ日数, 新しく足した記事数, エラー) を返す。"""
    get = get or (lambda url: _jfjb_get(url))
    meta = state["sources"].setdefault("jfjb", {})
    meta["name"] = JFJB_NAME
    meta["pages"] = {"jfjb": {"name": JFJB_NAME, "url": "http://www.81.cn/szb_223187/szblb/index.html"}}
    days = meta.setdefault("days", {})            # {日付: {"articles": n, "final": 最新号でなくなってから読んだか}}
    errors = []
    if newest is None:
        try:
            res = get(JFJB_NEWEST)
            newest = next((d.get("paperData") for d in res.get("data") or [] if d.get("paperName") == JFJB_NAME), None)
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"newestPaper: {type(e).__name__}: {e}")
        if not newest:                              # 最新号の日付が分からなければ、北京時間の今日
            newest = (dt.datetime.fromisoformat(now) + dt.timedelta(hours=8)).date().isoformat()
    last = dt.date.fromisoformat(newest)
    first = dt.date.fromisoformat(JFJB_FIRST_DAY) if backfill else last - dt.timedelta(days=JFJB_RECHECK_DAYS)
    todo = []
    d = last
    while d >= first:
        ds = d.isoformat()
        rec = days.get(ds, {})
        if ds == newest or not rec.get("final") or (rec.get("articles") and rec.get("v") != JFJB_FORMAT):
            todo.append(ds)
        d -= dt.timedelta(days=1)
    index_path = out / JFJB_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"days": {}}
    known = {it["id"] for it in state["items"] if it.get("source") == "jfjb"}
    read, added, hits = 0, 0, []
    deadline = time.time() + budget if budget else None
    for i, ds in enumerate(todo):
        if deadline and time.time() > deadline:
            errors.append(f"時間切れ: {len(todo) - i} 日分が残っている(もう一度 backfill を実行する)")
            break
        if i and backfill:
            time.sleep(PAGE_GAP)
        y, m, dd = ds.split("-")
        try:
            day = parse_jfjb_day(get(JFJB_DAY.format(y=y, m=m, d=dd)), ds)
        except urllib.error.HTTPError as e:
            if e.code == 404 and ds != newest:          # その日の紙面は無い(休刊)。最新号でなければ、もう読まない
                days[ds] = {"articles": 0, "final": True}
            elif e.code != 404:
                errors.append(f"{ds}: HTTP {e.code}")
            continue
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{ds}: {type(e).__name__}: {e}")
            continue
        n = sum(len(p["articles"]) for p in day["pages"])
        if backfill:
            print(f"[jfjb] {ds}: {len(day['pages'])} pages, {n} articles", file=sys.stderr)
        if not n:
            errors.append(f"{ds}: 記事が0本(作りが変わった可能性)")
            continue
        read += 1
        _write_if_changed(out / JFJB_DIR / f"{ds}.json", day)
        days[ds] = {"articles": n, "final": ds != newest, "v": JFJB_FORMAT}
        index["days"][ds] = {"pages": [[p["no"], p["name"], len(p["articles"]), p["pdf"]] for p in day["pages"]]}
        for p in day["pages"]:
            for art in p["articles"]:
                hit = notify_match(art["title"] + " " + art["subtitle"], JFJB_NOTIFY)
                if hit and art["id"] not in known:
                    hits.append({"id": art["id"], "source": "jfjb", "page": "jfjb", "title": art["title"], "url": art["url"],
                                 "published": ds, "section": f"第{p['no']}版：{p['name']}", "notify": True, "matched": hit})
    if hits:
        added = merge(state, "jfjb", hits, now, backfill=backfill)
    index["newest"] = newest
    _write_if_changed(index_path, index)
    if errors:
        meta.update({"last_error": "; ".join(errors)[:500], "last_error_at": now[:10]})
    else:
        meta.update({"last_ok_date": now[:10], "last_error": None, "last_error_at": None})
    return read, added, errors


# ----------------------------------------------------------------------------- 东部战区の微博
class WeiboAuthError(Exception):
    """ログインが切れている(cookie 切れ)・cookie が無い。"""


def _weibo_get(url, cookie, uid=WEIBO_UID):
    xsrf = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("XSRF-TOKEN=")), "")
    headers = dict(HEADERS, **{"Accept": "application/json, text/plain, */*", "Referer": f"https://weibo.com/u/{uid}",
                               "Cookie": cookie, "X-Requested-With": "XMLHttpRequest"}, **({"X-XSRF-TOKEN": xsrf} if xsrf else {}))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as r:
            text = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise WeiboAuthError(f"HTTP {e.code}") from e
        raise
    try:
        data = json.loads(text)
    except ValueError:
        raise WeiboAuthError("JSON でない応答(ログイン画面に回された)") from None
    if not data.get("ok"):
        msg = str(data.get("message") or data.get("msg") or "")
        if "登录" in msg or data.get("ok") == -100:
            raise WeiboAuthError(msg or "ログインが必要")
        raise ValueError(f"ok={data.get('ok')} {msg}")
    return data


def parse_weibo_post(s, acct=None):
    """mymblog の1投稿 → item。本文は text_raw(長い投稿は後で longtext に差し替える)。写真は URL だけ(ビューアが直接読む)。"""
    mid = s.get("mblogid") or str(s.get("idstr") or s.get("id"))
    text = (s.get("text_raw") or strip_tags(s.get("text") or "")).replace("\u200b", "").strip()
    pub = dt.datetime.strptime(s["created_at"], "%a %b %d %H:%M:%S %z %Y").isoformat()
    infos = s.get("pic_infos") or {}
    pics = []
    for pid in s.get("pic_ids") or []:
        inf = infos.get(pid) or {}
        big = (inf.get("largest") or inf.get("large") or inf.get("original") or {}).get("url") or f"https://wx1.sinaimg.cn/large/{pid}.jpg"
        thumb = (inf.get("bmiddle") or inf.get("large") or {}).get("url") or big
        pics.append({"src": big, "thumb": thumb})
    pinfo = s.get("page_info") or {}
    if not pics and pinfo.get("object_type") == "video":
        # 動画の投稿は表紙の画像を写真として扱う(v5.0.2。央视军事は動画の投稿が多い)。page_pic は文字列か {"url": …}
        cover = pinfo.get("page_pic")
        cover = cover.get("url") if isinstance(cover, dict) else cover
        cover = cover or ((pinfo.get("media_info") or {}).get("big_pic_info") or {}).get("pic_big", {}).get("url")
        if cover:
            pics.append({"src": cover, "thumb": cover, "cover": True})
    rt = s.get("retweeted_status") or {}
    first = re.sub(r"\s+", " ", text.split("\n")[0]).strip()
    acct = acct or WEIBO_ACCOUNTS[0]
    return {"id": f"weibo:{mid}", "source": "weibo", "page": acct["page"], "account": acct["name"], "mid": mid,
            "title": first[:90] + ("…" if len(first) > 90 else ""), "text": text, "published": pub,
            "url": f"https://weibo.com/{acct['uid']}/{mid}", "pics": pics,
            "video": pinfo.get("object_type") == "video", "long": bool(s.get("isLongText")),
            "retweet": (rt.get("text_raw") or "").strip()}


def weibo_notify(text):
    if any(re.search(rx, text or "") for rx in WEIBO_EXCLUDE):
        return None
    return notify_match(text, WEIBO_NOTIFY)


def collect_weibo(state, now, get=None, cookie=None, accounts=None):
    """見張る微博のアカウント(WEIBO_ACCOUNTS)の最新の投稿(1ページ目、20件ほど)を読んで items に足す。(新しく足した数, エラー or None)。"""
    cookie = (cookie if cookie is not None else os.environ.get("WEIBO_COOKIE", "")).strip()
    gap = PAGE_GAP if get is None else 0
    get = get or (lambda url: _weibo_get(url, cookie))
    accounts = accounts or WEIBO_ACCOUNTS
    meta = state["sources"].setdefault("weibo", {})
    meta["name"] = WEIBO_NAME
    meta["pages"] = {a["page"]: {"name": f"{a['name']} 微博", "url": f"https://weibo.com/u/{a['uid']}"} for a in WEIBO_ACCOUNTS}
    added, errors = 0, []
    try:
        if not cookie:
            raise WeiboAuthError("Secret WEIBO_COOKIE が設定されていない")
        known = {it["id"]: it for it in state["items"] if it.get("source") == "weibo"}
        for i, acct in enumerate(accounts):
            if i:
                time.sleep(gap)
            try:
                data = get(WEIBO_LIST.format(uid=acct["uid"]))
                posts = [parse_weibo_post(x, acct) for x in (data.get("data") or {}).get("list") or []]
                if not posts:
                    raise ValueError("投稿を1件も読み取れなかった")
                for p in posts:
                    old = known.get(p["id"])
                    if p["long"]:
                        if old and old.get("text_full"):
                            p["text"], p["text_full"] = old["text"], True
                        else:
                            try:
                                full = (get(WEIBO_LONGTEXT.format(mid=p["mid"])).get("data") or {}).get("longTextContent")
                                if full:
                                    p["text"], p["text_full"] = full.strip(), True
                            except (urllib.error.URLError, OSError, ValueError) as e:
                                print(f"[weibo] longtext {p['mid']}: {e}", file=sys.stderr)
                    hit = weibo_notify(p["text"] + " " + p["retweet"])
                    p.update({"notify": bool(hit), "matched": hit})
                added += merge(state, "weibo", posts, now)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                errors.append(f"{acct['name']}: {type(e).__name__}: {e}")
    except WeiboAuthError as e:
        err = f"ログインが切れている(cookie を取り直して Secret WEIBO_COOKIE を更新): {e}"
        meta.update({"last_error": err, "last_error_at": now[:10], "auth_error_at": meta.get("auth_error_at") or now[:10]})
        return 0, err
    if errors:
        meta.update({"last_error": "; ".join(errors), "last_error_at": now[:10], "auth_error_at": None})
        return added, "; ".join(errors)
    meta.update({"last_ok_date": now[:10], "last_error": None, "last_error_at": None, "auth_error_at": None})
    return added, None


# ----------------------------------------------------------------------------- 微博の写真(v5.0.1、2026-10-22。v5.0.2 から動画の投稿は表紙の画像も)
# 微博の写真(sinaimg.cn)は、ほかのサイトに埋め込むと読み込みを拒否される(ビューアで「写真を読み込めませんでした」)。
# 収集のときに Referer: weibo.com を付けて読み、縮小して news_out/weibo_pics/<mid>.json に保存する(item に pics_file)。
# 容量を抑えるため、1投稿 WEIBO_PICS_MAX 枚まで・横 WEIBO_PIC_W px まで。公開から WEIBO_PICS_KEEP_DAYS 日たった投稿の写真は消す
# (消しても data ブランチの毎月の作り直しで履歴からも消えるので、容量は増え続けない)。
WEIBO_PICS_DIR = "weibo_pics"
WEIBO_PICS_MAX = 9
WEIBO_PIC_W = 720
WEIBO_PIC_QUALITY = 70
WEIBO_PICS_PER_RUN = 15         # 1回の収集で写真を保存する投稿の数(新しい順)
WEIBO_PICS_KEEP_DAYS = 30
WEIBO_PICS_MAX_TRIES = 3


def weibo_pic_url(url):
    """写真の URL を、ほどよい大きさ(横 690px)のものに読み替える。"""
    return re.sub(r"(sinaimg\.cn/)[^/]+/", r"\g<1>mw690/", url or "")


def weibo_pic_data_uri(url, get_bytes=None):
    get_bytes = get_bytes or _get_bytes
    try:
        raw, ctype = get_bytes(weibo_pic_url(url), "https://weibo.com/")
    except (urllib.error.URLError, OSError):
        if weibo_pic_url(url) == url:
            raise
        raw, ctype = get_bytes(url, "https://weibo.com/")     # mw690 が無い画像(動画の表紙など)は元の URL で
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        if im.width > WEIBO_PIC_W:
            im = im.resize((WEIBO_PIC_W, round(im.height * WEIBO_PIC_W / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=WEIBO_PIC_QUALITY, optimize=True)
        raw, ctype = buf.getvalue(), "image/jpeg"
    except ImportError:
        if len(raw) > 300_000:
            raise ValueError("写真が大きすぎる(Pillow が無いので縮小できない)")
    except OSError as e:
        raise ValueError(f"写真を読めない: {e}")
    if not ctype.startswith("image/"):
        raise ValueError(f"写真ではない応答({ctype})")
    return f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")


def collect_weibo_pics(state, out, now, get_bytes=None):
    """写真のある微博の投稿について、写真を保存する(新しい順に WEIBO_PICS_PER_RUN 件まで)。古い投稿の写真は消す。
    {"saved": 保存した投稿数, "removed": 消した数, "errors": [...]} を返す。"""
    today = now[:10]
    limit = (dt.date.fromisoformat(today) - dt.timedelta(days=WEIBO_PICS_KEEP_DAYS)).isoformat()
    items = [it for it in state["items"] if it.get("source") == "weibo"]
    res = {"saved": 0, "removed": 0, "errors": []}
    for it in items:                                   # 古い投稿の写真を消す
        if it.get("pics_file") and str(it.get("published") or "")[:10] < limit:
            (out / it["pics_file"]).unlink(missing_ok=True)
            it.pop("pics_file")
            it["pics_expired"] = True
            res["removed"] += 1
    keep = {it["pics_file"] for it in items if it.get("pics_file")}
    pdir = out / WEIBO_PICS_DIR
    if pdir.exists():                                  # どの投稿からも指されていないファイルも消す
        for f in pdir.glob("*.json"):
            if f"{WEIBO_PICS_DIR}/{f.name}" not in keep:
                f.unlink()
                res["removed"] += 1
    todo = [it for it in items if it.get("pics") and not it.get("pics_file") and not it.get("pics_expired")
            and str(it.get("published") or "")[:10] >= limit
            and (it.get("pics_tries", 0) < WEIBO_PICS_MAX_TRIES or it.get("pics_last_try") != today)]
    todo.sort(key=lambda it: it.get("published") or "", reverse=True)
    for it in todo[:WEIBO_PICS_PER_RUN]:
        data, errs = [], []
        for p in it["pics"][:WEIBO_PICS_MAX]:
            try:
                data.append(weibo_pic_data_uri(p.get("src") or p.get("thumb"), get_bytes))
            except (urllib.error.URLError, OSError, ValueError) as e:
                data.append(None)
                errs.append(f"{type(e).__name__}: {e}")
        if not any(data):
            it["pics_tries"] = it.get("pics_tries", 0) + 1
            it["pics_last_try"] = today
            res["errors"].append(f"{it['id']}: {errs[0] if errs else '写真なし'}")
            continue
        name = f"{WEIBO_PICS_DIR}/{re.sub(r'[^A-Za-z0-9_-]', '_', it['mid'])}.json"
        pdir.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(json.dumps({"id": it["id"], "pics": data, "fetched_at": now}, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        it["pics_file"] = name
        it.pop("pics_tries", None)
        it.pop("pics_last_try", None)
        res["saved"] += 1
    return res


# ----------------------------------------------------------------------------- 本体
OLD_POST_DAYS = 7


def is_old_post(it, now):
    """見つけた時点で公開から OLD_POST_DAYS 日より前の投稿か。2026-10-20 06:30 UTC、维权执法の一覧が7〜8月の古い投稿を
    返し(経路は jina。原因不明)、それらが新着として通知に出たため、古い投稿は遡った分と同じ扱い(通知しない)にする。"""
    pub, seen = str(it.get("published") or "")[:10], str(now or "")[:10]
    try:
        return (dt.date.fromisoformat(seen) - dt.date.fromisoformat(pub)).days > OLD_POST_DAYS
    except ValueError:
        return False


def merge(state, sid, fetched, now, backfill=False):
    """新しく見つかったものに first_seen を付けて足す(全件を蓄積)。backfill のときは新しく足したものに backfilled を付ける
    (ビューアは通知しない)。足した件数を返す。"""
    old = {it["id"]: it for it in state["items"] if it["source"] == sid}
    added = 0
    for it in fetched:
        if it["id"] in old:
            prev = old[it["id"]]
            it["first_seen"] = prev.get("first_seen") or now
            if prev.get("notify") and not it.get("notify"):      # 前に通知の規則に当たった記事は、そのまま通知対象に
                it.update({"notify": True, "matched": prev.get("matched"), "page": prev.get("page", it.get("page"))})
        else:
            it["first_seen"] = now
            if backfill or is_old_post(it, now):
                it["backfilled"] = True
            added += 1
        old[it["id"]] = {**old.get(it["id"], {}), **it}
    mine = list(old.values())
    others = [it for it in state["items"] if it["source"] != sid]
    state["items"] = sorted(others + mine, key=lambda x: (x.get("published") or "", x["id"]), reverse=True)
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="news_out")
    ap.add_argument("--fixture-dir", help="取得の代わりに <page>.html / <page>.xml を読む(テスト用)")
    ap.add_argument("--backfill", nargs="+", choices=list(SOURCES) + ["jfjb"], help="一覧を最後のページまで遡って集める")
    ap.add_argument("--max-pages", type=int, default=200, help="--backfill で読む最大ページ数(一覧ごと)")
    ap.add_argument("--import-json", help="中国海警局の記事 [{title,url,date}] を取り込む(遡った分として)")
    ap.add_argument("--no-articles", action="store_true", help="記事本文の写し(news_out/articles/)を作らない")
    ap.add_argument("--probe", nargs="+", metavar="URL",
                    help="記事ページを読んで、script/style を除いたHTMLの先頭をログに出すだけ(作りの調査用。ファイルは書かない)")
    a = ap.parse_args(argv)
    if a.probe:
        for url in a.probe:
            if re.search(r"\.(jpe?g|png|gif|webp)(\?|$)", url, re.I):     # 画像: 状態とヘッダーだけ(Referer あり・なしの両方)
                for ref in (None, "https://kw15-bot.github.io/"):
                    req = urllib.request.Request(url, headers=dict(HEADERS, **({"Referer": ref} if ref else {})))
                    try:
                        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                            body = r.read()
                            print(f"=== IMG {url} referer={ref} -> {r.status} len={len(body)} headers={dict(r.headers)}")
                            if not r.headers.get_content_type().startswith("image/"):   # 画像の代わりに返ってきたページ(検査ページ等)
                                print(body[:3000].decode("utf-8", "replace"))
                    except urllib.error.HTTPError as e:
                        print(f"=== IMG {url} referer={ref} -> HTTP {e.code} headers={dict(e.headers)}")
                    except (urllib.error.URLError, OSError) as e:
                        print(f"=== IMG {url} referer={ref} -> {type(e).__name__}: {e}")
                continue
            try:
                text, via = fetch_page(url, mirror="ccg.gov.cn" in url)
            except (urllib.error.URLError, OSError) as e:
                print(f"=== PROBE {url}: {type(e).__name__}: {e}")
                continue
            if re.search(r"\.(js|json)(\?|$)", url) or not text.lstrip().startswith("<"):   # データ・スクリプトはそのまま
                print(f"=== PROBE {url} via={via} len={len(text)} (raw)")
                try:                                   # JSON は構造(キーと先頭の要素、長い文字列は短く)を出す
                    def skel(v, d=0):
                        if isinstance(v, dict):
                            return {k: skel(x, d + 1) for k, x in v.items()}
                        if isinstance(v, list):
                            return [f"<list len={len(v)}>"] + [skel(x, d + 1) for x in v[:2]]
                        return v[:300] + "…" if isinstance(v, str) and len(v) > 300 else v
                    print(json.dumps(skel(json.loads(text)), ensure_ascii=False, indent=1)[:30000])
                    continue
                except ValueError:
                    pass
                print(text[:20000] if len(text) <= 60000 else text[-60000:])   # 大きいスクリプトは後ろ(同梱ライブラリの後の本体)
                continue
            for src in re.findall(r'(?i)<script[^>]+src=["\']([^"\']+)', text):          # 中身を JS で読み込むページの調査用
                print(f"=== SCRIPT src {src}")
            for js in re.findall(r"(?is)<script\b[^>]*>(.*?)</script>", text):
                if js.strip():
                    print(f"=== INLINE SCRIPT {re.sub(r'\s+', ' ', js)[:3000]}")
            t = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
            t = re.sub(r"\s+", " ", t)
            print(f"=== PROBE {url} via={via} len={len(text)} imgs={len(re.findall(r'<img', t, re.I))}")
            print(t[:20000])
        return 0
    out = Path(a.out_dir)
    path = out / "news.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"sources": {}, "items": []}
    state.setdefault("sources", {})
    state.setdefault("items", [])
    now = now_iso()
    # 2026-10-20: 以前、一覧の横の欄(日時なし)から入った中国海警局の古い記事(2025年)は、遡った分として扱い通知しない
    for it in state["items"]:
        if it.get("source") == "ccg" and "T" not in str(it.get("published", "")) and not it.get("backfilled"):
            it["backfilled"] = True
        # 2026-10-21: 見つけた時点で古かった投稿(新着として入ってしまったもの)も、遡った分として扱う
        if not it.get("backfilled") and it.get("first_seen") and is_old_post(it, it["first_seen"]):
            it["backfilled"] = True
    if a.backfill or a.import_json:
        summary = run_backfill(state, a, now)
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    ok = 0
    summary = {}
    for sid, src in SOURCES.items():
        meta = state["sources"].setdefault(sid, {})
        meta["name"] = src["name"]
        meta.pop("url", None)
        meta["pages"] = {pg["page"]: {"name": pg["name"], "url": pg["url"]} for pg in src["pages"]}
        found, errors = {}, []
        for pg in src["pages"]:
            try:
                if a.fixture_dir:
                    fx = next((p for p in (Path(a.fixture_dir) / f"{pg['page']}.html", Path(a.fixture_dir) / f"{pg['page']}.xml")
                               if p.exists()), None)
                    if not fx:
                        raise FileNotFoundError(f"fixture {pg['page']}.html/.xml が無い")
                    text = fx.read_text(encoding="utf-8")
                else:
                    if pg is not src["pages"][0]:
                        time.sleep(PAGE_GAP)
                    text, via = fetch_page(pg.get("fetch") or pg["url"], mirror=pg.get("mirror", False))
                    # どの経路で読めたか(direct / jina / wayback)はログにだけ出す。ファイルに書くと、直接読めたり読めなかったり
                    # するたびに中身が変わってコミット(=GitHub Pages の公開)が増えるため(2026-10-20)
                    summary.setdefault(sid, {}).setdefault("via", {})[pg["page"]] = via
                items = PARSERS[pg["kind"]](text, pg)
                if not items:
                    raise ValueError("記事を1件も読み取れなかった(ページの作りが変わった可能性)")
                for it in items:
                    hit = notify_match(it["title"], CCG_NOTIFY if pg["notify"] is None else pg["notify"])
                    it.update({"source": sid, "page": pg["page"], "notify": bool(hit), "matched": hit})
                    prev = found.get(it["id"])
                    if prev:                                  # 両方の一覧に載っている記事: どちらかで当たれば通知
                        if hit and not prev["notify"]:
                            prev.update({"notify": True, "matched": hit, "page": pg["page"]})
                    else:
                        found[it["id"]] = it
            except (urllib.error.URLError, OSError, ValueError) as e:
                errors.append(f"{pg['page']}: {type(e).__name__}: {e}")
        if found:
            added = merge(state, sid, list(found.values()), now)
            ok += 1
            summary.setdefault(sid, {}).update({"fetched": len(found), "new": added, "notify": sum(i["notify"] for i in found.values())})
        if errors:
            # 時刻まで書くと、失敗が続く間は毎回ファイルが変わってコミットされるので、日付だけ
            meta.update({"last_error": "; ".join(errors), "last_error_at": now[:10]})
            summary.setdefault(sid, {})["error"] = meta["last_error"]
            print(f"[WARN] {sid}: {meta['last_error']}", file=sys.stderr)
        else:
            # 成功時刻を毎回書くと15分ごとにコミットが出るので、日付だけ持つ(新着・エラー・日付が変わったときだけファイルが変わる)
            meta.update({"last_ok_date": now[:10], "last_error": None, "last_error_at": None})
    # 通知の規則を変えたときに保存済みの記事にも効くよう、毎回いまの規則で付け直す(2026-10-21)
    page_rules = {pg["page"]: (pg["notify"] or CCG_NOTIFY) for src in SOURCES.values() for pg in src["pages"]}
    for it in state["items"]:
        rules = page_rules.get(it.get("page")) or (CCG_NOTIFY if it.get("source") == "ccg" else None)
        if rules:
            hit = notify_match(it.get("title", ""), rules)
            it["notify"], it["matched"] = bool(hit), hit
    if a.fixture_dir:
        fx = Path(a.fixture_dir)
        if (fx / "weibo_mymblog.json").exists():
            wb = json.loads((fx / "weibo_mymblog.json").read_text(encoding="utf-8"))
            added, err = collect_weibo(state, now, get=lambda url: wb, cookie="SUB=fixture", accounts=WEIBO_ACCOUNTS[:1])
            ok += err is None
            summary["weibo"] = {"new": added, "error": err}
        if (fx / "jfjb_newest.json").exists():
            read, added, errs = collect_jfjb(state, out, now, get=lambda url: _fixture_json(fx, url))
            ok += bool(read)
            summary["jfjb"] = {"days": read, "new": added, "error": "; ".join(errs) or None}
    else:
        read, added, errs = collect_jfjb(state, out, now)
        ok += bool(read)
        summary["jfjb"] = {"days": read, "new": added, "error": "; ".join(errs) or None}
        if errs:
            print(f"[WARN] jfjb: {'; '.join(errs)}", file=sys.stderr)
        added, err = collect_weibo(state, now)
        ok += err is None
        summary["weibo"] = {"new": added, "error": err}
        if err:
            print(f"[WARN] weibo: {err}", file=sys.stderr)
    if not a.fixture_dir and not a.no_articles:
        summary["articles"] = collect_articles(state, out, now)
        summary["weibo_pics"] = collect_weibo_pics(state, out, now)
        if summary["weibo_pics"]["errors"]:
            print(f"[WARN] weibo pics: {'; '.join(summary['weibo_pics']['errors'][:3])}", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 2


def _fixture_json(fx, url):
    """テスト用: 解放军报の URL を fixture のファイル(jfjb_newest.json / jfjb_YYYY-MM-DD.json)に読み替える。無い日は 404。"""
    m = re.search(r"/_szb/jfjb/(\d{4})/(\d{2})/(\d{2})/index\.json", url)
    name = f"jfjb_{m.group(1)}-{m.group(2)}-{m.group(3)}.json" if m else "jfjb_newest.json"
    if not (fx / name).exists():
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    return json.loads((fx / name).read_text(encoding="utf-8"))


def _ccg_item(title, url, date=""):
    m = re.search(r"/(hjyw|wqzf)/(\d{6})/t(\d{8})_(\d+)\.html", url or "")
    if not m or not title:
        return None
    ymd, num = m.group(3), m.group(4)
    published = (date or "").strip().replace(" ", "T") or f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    return {"id": f"ccg:{ymd}_{num}", "source": "ccg", "page": f"ccg_{m.group(1)}", "title": strip_tags(title), "url": url,
            "published": published}


def _with_notify(it, rules):
    hit = notify_match(it["title"], rules)
    it.update({"notify": bool(hit), "matched": hit})
    return it


def run_backfill(state, a, now):
    """一覧を遡って集める(--backfill)・別の手段で集めた記事を取り込む(--import-json)。足したものは backfilled。"""
    summary = {}
    if a.import_json:
        rows = json.loads(Path(a.import_json).read_text(encoding="utf-8"))
        items = [x for x in (_ccg_item(r.get("title"), r.get("url"), r.get("date")) for r in rows) if x]
        items = [_with_notify(it, CCG_NOTIFY) for it in items]
        summary["import"] = {"rows": len(rows), "items": len(items), "new": merge(state, "ccg", items, now, backfill=True)}
    for sid in a.backfill or []:
        if sid == "jfjb":
            read, added, errs = collect_jfjb(state, Path(a.out_dir), now, backfill=True, budget=JFJB_BACKFILL_SECONDS)
            summary["jfjb"] = {"days": read, "new": added, "errors": errs}
            continue
        for pg in SOURCES[sid]["pages"]:
            got, pages, err = {}, 0, None
            for n in range(1, a.max_pages + 1):
                # 軍聞社は ?PageIndex=1 から。中国海警局は1ページ目が index.html、2ページ目から index_1.html
                url = pg["backfill"].format(n=n, m=n - 1) if ("{n}" in pg["backfill"] or n > 1) else pg["url"]
                try:
                    if n > 1:
                        time.sleep(PAGE_GAP)
                    items = PARSERS[pg["backfill_kind"]](http_get(url), {**pg, "url": url})
                except (urllib.error.URLError, OSError, ValueError) as e:
                    if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                        break                                # 最後のページを過ぎた
                    err = f"{url}: {type(e).__name__}: {e}"
                    break
                new = [it for it in items if it["id"] not in got]
                if not new:
                    break                                    # 同じページが返ってきた(最後のページを過ぎた)
                pages += 1
                for it in new:
                    it.update({"source": sid, "page": pg["page"]})
                    got[it["id"]] = _with_notify(it, pg["notify"] or CCG_NOTIFY)
            added = merge(state, sid, list(got.values()), now, backfill=True)
            summary[pg["page"]] = {"pages": pages, "items": len(got), "new": added, "error": err}
            if err:
                print(f"[WARN] backfill {pg['page']}: {err}", file=sys.stderr)
    return summary


if __name__ == "__main__":
    sys.exit(main())
