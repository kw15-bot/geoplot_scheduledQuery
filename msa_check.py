#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
msa_check.py
============

航行警報(NAVWAR、msa_out/military.geojson)が本文どおりに描けているかを、1日1回点検する
(msa-check.yml、毎日 08:47 JST)。結果は msa_out/reports/msa_check_YYYY-MM-DD.md と latest.md。

対象: 有効中・これから有効になる警報、と直近2日に初めて見つけた警報(期間不明のものも含む)。
点検すること(本文 raw_text と、描いた図形 geometry を突き合わせる):
  - 円・円弧・扇形の記述(「…为圆心…半径」「以顶点为中心半径…圆弧」「方位a°-b°，半径…的扇形」)があるのに、
    その形で描けていない(円の中心だけの点や、円弧が直線になった多角形)
  - 本文の座標の数と、描いた点の数が合わない(座標の読み落とし・読み過ぎ)
  - 座標があるのに図形が無い(no_coords)
  - 多角形の辺が交差している(角の順番の取り違え)
  - 図形が中国沿岸の範囲(北緯0〜45度・東経100〜135度)の外にある
  - 面積が異常に大きい(5万 km² 超)
  - 有効期間が読めていない(期間不明)

使い方:
  python msa_check.py --out-dir msa_out             # msa_out/reports/ に書く
  python msa_check.py --out-dir msa_out --all       # 全件(過去分も)を点検し、標準出力にだけ出す
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

import msa_scraper as M

JST = dt.timezone(dt.timedelta(hours=9))
AREA_MAX_KM2 = 100000      # 琼航警131/26(約5.5万km²)のような本物の広い訓練区域もある
BBOX = (0.0, 45.0, 100.0, 135.0)          # lat_min, lat_max, lon_min, lon_max

# 本文中の座標の数を、収集側とは別の、ゆるい書き方で数える(緯度の数 = 点の数)
_LAT_LOOSE = re.compile(
    r"(?:\d{1,2}\s*[-°]\s*\d{1,2}(?:[.\d]*)(?:\s*[′'\-]\s*[\d.]*\s*[″\"]?)?\s*[′']?\s*N(?![A-Z]))"
    r"|(?:N\s*\d{1,2}\s*°\s*\d{1,2}(?:[.\d]*)\s*[′']?(?:\s*[\d.]+\s*[″\"])?)"
    r"|(?:北纬\s*\d{1,2}\s*°)"
)


# 緯度の直後の経度(「 119-26.01E」「、E 119°13'44"」「东经119°…」)
_LON_AFTER = re.compile(r"[\s、，,]*(?:E\s*\d{2,3}\s*°[\d.\s'\"′″]*|东经\s*\d{2,3}\s*°[\d.\s'\"′″]*|\d{2,3}\s*[-°][\d.\s\-'\"′″]*E)")


def _parse_time(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def ring_area_km2(ring):
    """経緯度の環の面積(km²、等長方形近似)。"""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 111.32
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(a) / 2


def _seg_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def ring_self_intersects(ring):
    n = len(ring) - 1                       # 閉じた環(最後 = 最初)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue                    # 隣り合う(最初と最後の辺)
            if _seg_cross(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                return True
    return False


def coords_of(geom):
    if not geom:
        return []
    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        return [c]
    if t == "LineString":
        return c
    if t == "Polygon":
        return c[0][:-1]
    return []


def check_item(item, feats):
    """1つの記事(state の item)と、その記事から作った図形(feature のリスト)を点検し、問題の文のリストを返す。"""
    issues = []
    text = item.get("raw_text") or ""
    norm = M.normalize_symbols(text)
    groups = item.get("groups") or []
    fmts = {p.get("fmt") for g in groups for p in g}
    if ((M._CIRCLE_RE.search(norm) and "CIRCLE" not in fmts)
            or (M._SECTOR_ARC_RE.search(norm) and "ARC" not in fmts)
            or (M._BEARING_SECTOR_RE.search(norm) and "SECTOR" not in fmts)):
        issues.append("円・円弧・扇形の記述があるのに、その形で描けていない")
    else:
        for word in ("半径", "扇形", "圆弧"):
            if word in norm and not (set(M.SHAPE_FMTS) & fmts):
                i = norm.find(word)
                issues.append(f"「{word}」の記述があるのに円/円弧/扇形になっていない(…{norm[max(0, i - 25):i + 20]}…)")
                break

    # 座標の数: 本文(ゆるく数えた緯度の数) と、描いた元の点(円・円弧で足した点を除き、円は中心1点と数える)
    # 同じ座標が2回書かれる(円の中心を扇形・三角形の頂点でもう一度など)ので、どちらも「違う座標の数」で比べる
    keys = set()
    for m in _LAT_LOOSE.finditer(norm):      # 緯度から、その後の経度(E)までを1つの座標とみなす
        lon = _LON_AFTER.match(norm, m.end())
        keys.add(re.sub(r"[\s、，,;；]", "", m.group(0) + (lon.group(0) if lon else norm[m.end():m.end() + 12])))
    text_n = len(keys)
    base = {(round(p["lat"], 4), round(p["lon"], 4)) for g in groups for p in g if p.get("fmt") not in ("CIRCLE", "ARC")}
    circles = {}
    for g in groups:
        for p in g:
            if p.get("fmt") == "CIRCLE":
                circles.setdefault(p.get("raw"), []).append(p)
    for ps in circles.values():            # 円の中心 = 円周の点の平均
        base.add((round(sum(p["lat"] for p in ps) / len(ps), 4), round(sum(p["lon"] for p in ps) / len(ps), 4)))
    base_n = len(base)
    if text_n and base_n != text_n:
        issues.append(f"本文の座標 {text_n} 個に対して、描いた点は {base_n} 個(読み落とし・本文の誤記の可能性)")

    if not feats or all(f.get("geometry") is None for f in feats):
        if text_n:
            issues.append(f"本文に座標が {text_n} 個あるのに図形が無い")
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        gi = f["properties"].get("group_index")
        tag = f"エリア{gi}: " if len(feats) > 1 else ""
        pts = coords_of(g)
        out = [p for p in pts if not (BBOX[0] <= p[1] <= BBOX[1] and BBOX[2] <= p[0] <= BBOX[3])]
        if out:
            issues.append(f"{tag}中国沿岸の範囲の外に点がある(例 {out[0][1]:.3f}N {out[0][0]:.3f}E)")
        if g["type"] == "Polygon":
            ring = g["coordinates"][0]
            if ring_self_intersects(ring):
                issues.append(f"{tag}多角形の辺が交差している(角の順番の取り違えの可能性)")
            a = ring_area_km2(ring)
            if a > AREA_MAX_KM2:
                issues.append(f"{tag}面積が大きすぎる({a:,.0f} km²)")
    if not item.get("validity"):
        issues.append("有効期間が読めていない(期間不明)")
    return issues


def run(state, geojson, now, all_items=False):
    feats_by_url = {}
    for f in geojson.get("features", []):
        feats_by_url.setdefault(f["properties"].get("url"), []).append(f)
    rows = []
    for key, item in sorted(state.items()):
        if not item.get("military") or item.get("own_id") in M.MANUALLY_EXCLUDED_OWN_IDS:
            continue
        if item.get("cancels") and not item.get("groups"):
            continue                        # 「撤销…」だけの記事(区域を持たない)
        feats = feats_by_url.get(item.get("url")) or feats_by_url.get(key) or []
        v = item.get("validity") or {}
        end = _parse_time(v.get("end")) if isinstance(v, dict) else None
        first = _parse_time(item.get("first_seen"))
        current = end is None or end >= now - dt.timedelta(hours=12)
        new = first is not None and first >= now - dt.timedelta(days=2)
        if not all_items and not (current or new):
            continue
        if item.get("revoked") and not new:
            continue
        rows.append((item, feats, check_item(item, feats)))
    return rows


def render(rows, now):
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    bad = [r for r in rows if r[2]]
    out = [f"# 航行警報 描画チェック {day}", "",
           f"点検 {len(rows)} 件(有効中・これから有効・直近2日に初めて見つけたもの) / 要確認 {len(bad)} 件", ""]
    if not bad:
        out.append("問題は見つかりませんでした。")
    for item, feats, issues in bad:
        title = item.get("title") or item.get("own_id") or ""
        out.append(f"## {title}")
        out.append(f"- {item.get('url', '')}")
        for s in issues:
            out.append(f"- **{s}**")
        out.append(f"- 本文: {item.get('raw_text', '')}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="msa_out")
    ap.add_argument("--all", action="store_true", help="全件を点検して標準出力にだけ出す")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    geojson = json.loads((out / "military.geojson").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    rows = run(state, geojson, now, all_items=a.all)
    md = render(rows, now)
    if a.all:
        sys.stdout.write(md)
        return
    rep = out / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    (rep / f"msa_check_{day}.md").write_text(md, encoding="utf-8")
    (rep / "latest.md").write_text(md, encoding="utf-8")
    print(f"checked {len(rows)}, issues {sum(1 for r in rows if r[2])}")


if __name__ == "__main__":
    main()
