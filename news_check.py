#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_check.py
=============

報道(関連サイトの収集、news_out/news.json)を1日1回点検する(notam-report.yml の日次レポートの後、2026-10-21〜)。
結果は news_out/reports/news_check_YYYY-MM-DD.md と latest.md。

  - サイトごとの収集の状態(最後に読めた日・エラー)と、一覧ごとの最新の投稿の日時
  - 直近24時間の新着(通知の語句に当たったもの=🔔の対象)
  - 記事の写し(PDF)の進み具合(写しあり / 試行中 / 諦めた)と、直近の新着で写しの無いもの
  - おかしな所: 見つけた時点で古い投稿なのに新着扱い(遡った分になっていない)、公開日時が未来、見出しが空
  - 解放军报(2026-10-22〜): 最新号の日付と直近7日の版数・記事数。最新号が2日以上前・直近の日が読めていない(取りこぼし)は要確認

使い方:
  python news_check.py --out-dir news_out           # news_out/reports/ に書く
  python news_check.py --out-dir news_out --stdout  # 書かずに標準出力へ
"""

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

import news_collect as N

JST = dt.timezone(dt.timedelta(hours=9))
PAGE_NAMES = {pg["page"]: pg["name"] for src in N.SOURCES.values() for pg in src["pages"]}


def _ts(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))   # 時差の無い時刻は北京時間


def copy_state(it):
    if it.get("article"):
        return "写しあり"
    if it.get("article_tries", 0) >= N.ARTICLE_MAX_TRIES:
        return "諦めた"
    return "試行中" if it.get("article_tries") else "未着手"


def jfjb_check(state, out_dir, now):
    """解放军报の直近7日。(行のリスト, 要確認の文のリスト)。"""
    meta = (state.get("sources") or {}).get("jfjb")
    if not meta:
        return [], []
    p = Path(out_dir) / N.JFJB_DIR / "index.json"
    index = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"days": {}}
    days = meta.get("days") or {}
    newest = index.get("newest") or ""
    today = (now + dt.timedelta(hours=8)).date()                  # 北京時間
    rows, odd = [], []
    for k in range(7):
        ds = (today - dt.timedelta(days=k)).isoformat()
        pages = (index["days"].get(ds) or {}).get("pages") or []
        d = days.get(ds)
        if pages:
            rows.append(f"- {ds}: {len(pages)}版 {sum(x[2] for x in pages)}本" + ("" if d and (d.get('final') or ds == newest) else "(最新号でなくなってから未確認)"))
        elif d and d.get("final"):
            rows.append(f"- {ds}: 紙面なし(休刊)")
        elif ds > newest:
            rows.append(f"- {ds}: まだ出ていない")
        else:
            rows.append(f"- {ds}: **読めていない**")
            odd.append(f"解放军报 {ds} の紙面を読めていない(取りこぼしの可能性)")
    if newest and (today - dt.date.fromisoformat(newest)).days >= 2:
        odd.append(f"解放军报の最新号が {newest} のまま(2日以上更新なし)")
    return rows, odd


def run(state, now):
    items = state.get("items", [])
    out = {"sources": [], "new": [], "copies": collections.Counter(), "no_copy_new": [], "odd": []}
    for sid, meta in sorted((state.get("sources") or {}).items()):
        out["sources"].append((meta.get("name") or sid, meta.get("last_ok_date"), meta.get("last_error"), meta.get("last_error_at")))
    latest = {}
    for it in items:
        pg = it.get("page") or it.get("source")
        if str(it.get("published") or "") > str(latest.get(pg, "")):
            latest[pg] = it.get("published")
        out["copies"][(it.get("source"), copy_state(it))] += 1
        fs = _ts(it.get("first_seen"))
        if fs and now - fs <= dt.timedelta(hours=24) and not it.get("backfilled"):
            out["new"].append(it)
            if not it.get("article"):
                out["no_copy_new"].append(it)
        if not it.get("backfilled") and it.get("first_seen") and N.is_old_post(it, it["first_seen"]):
            out["odd"].append(("見つけた時点で古い投稿なのに新着扱い(通知の対象になりうる)", it))
        pub = _ts(it.get("published"))
        if pub and pub - now > dt.timedelta(hours=12):
            out["odd"].append(("公開日時が未来", it))
        if not str(it.get("title") or "").strip():
            out["odd"].append(("見出しが空", it))
    out["latest"] = latest
    return out


def render(r, now):
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    o = [f"# 報道 点検 {day}", "",
         f"直近24時間の新着 {len(r['new'])} 件(うち🔔の対象 {sum(1 for i in r['new'] if i.get('notify'))} 件) / "
         f"要確認 {len(r['odd']) + len(r.get('jfjb_odd', []))} 件", "", "## 収集の状態", ""]
    for name, ok, err, err_at in r["sources"]:
        o.append(f"- {name}: 最後に読めた日 {ok or '-'}" + (f" / **エラー({err_at})**: {err}" if err else ""))
    for pg, pub in sorted(r["latest"].items()):
        o.append(f"- {PAGE_NAMES.get(pg, pg)} の最新の投稿: {str(pub or '')[:16].replace('T', ' ')}")
    o += ["", "## 直近24時間の新着", ""]
    if not r["new"]:
        o.append("なし")
    for it in sorted(r["new"], key=lambda i: str(i.get("published") or ""), reverse=True):
        mark = "🔔 " if it.get("notify") else ""
        o.append(f"- {mark}[{PAGE_NAMES.get(it.get('page'), it.get('page'))}] {str(it.get('published') or '')[:16].replace('T', ' ')} "
                 f"{it.get('title')}（写し: {copy_state(it)}）")
    if r.get("jfjb_rows"):
        o += ["", "## 解放军报(直近7日、北京時間の日付)", ""] + r["jfjb_rows"]
    o += ["", "## 記事の写し(PDF)", ""]
    for src in ("ccg", "mna"):
        c = {k[1]: v for k, v in r["copies"].items() if k[0] == src}
        o.append(f"- {N.SOURCES[src]['name']}: " + " / ".join(f"{k} {c.get(k, 0)}" for k in ("写しあり", "試行中", "未着手", "諦めた")))
    if r["no_copy_new"]:
        o.append(f"- 直近の新着で写しがまだ無いもの {len(r['no_copy_new'])} 件(次の収集で作る)")
    o += ["", "## 要確認", ""]
    for why in r.get("jfjb_odd", []):
        o.append(f"- **{why}**")
    if not r["odd"] and not r.get("jfjb_odd"):
        o.append("問題は見つかりませんでした。")
    for why, it in r["odd"]:
        o.append(f"- **{why}**: {it.get('id')} {str(it.get('published') or '')[:16]} {it.get('title')}")
    return "\n".join(o).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="news_out")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    state = json.loads((out / "news.json").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    r = run(state, now)
    r["jfjb_rows"], r["jfjb_odd"] = jfjb_check(state, out, now)
    md = render(r, now)
    if a.stdout:
        sys.stdout.write(md)
        return
    rep = out / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    (rep / f"news_check_{day}.md").write_text(md, encoding="utf-8")
    (rep / "latest.md").write_text(md, encoding="utf-8")
    print(md.splitlines()[2])


if __name__ == "__main__":
    main()
