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
"""

import argparse
import datetime as dt
import http.cookiejar
import time
import email.utils
import html
import json
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
CCG_NOTIFY = [r"中国台湾岛以东海域(?:依法)?开展常态化执法巡查", r"在我钓鱼岛", r"金门"]
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


# ----------------------------------------------------------------------------- 本体
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
            if backfill:
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
    ap.add_argument("--backfill", nargs="+", choices=list(SOURCES), help="一覧を最後のページまで遡って集める")
    ap.add_argument("--max-pages", type=int, default=200, help="--backfill で読む最大ページ数(一覧ごと)")
    ap.add_argument("--import-json", help="中国海警局の記事 [{title,url,date}] を取り込む(遡った分として)")
    ap.add_argument("--probe", nargs="+", metavar="URL",
                    help="記事ページを読んで、script/style を除いたHTMLの先頭をログに出すだけ(作りの調査用。ファイルは書かない)")
    a = ap.parse_args(argv)
    if a.probe:
        for url in a.probe:
            try:
                text, via = fetch_page(url, mirror="ccg.gov.cn" in url)
            except (urllib.error.URLError, OSError) as e:
                print(f"=== PROBE {url}: {type(e).__name__}: {e}")
                continue
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
                    hit = notify_match(it["title"], pg["notify"] or CCG_NOTIFY)
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
    out.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 2


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
