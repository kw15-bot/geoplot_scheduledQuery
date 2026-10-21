#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notam_check.py
==============

NOTAM(notam_out/notam_cn.geojson、有効・有効前の全件)が本文どおりに地図に描けているかを、
1日1回点検する(notam-report.yml の日次レポートの後)。結果は
notam_out/reports/notam_check_YYYY-MM-DD.md と notam_check_latest.md(latest.md は日次レポートのもの)。

E項(本文)と、描いた図形(geometry / geometry_source)を突き合わせる:
  - 本文に座標が3つ以上(または座標で中心を書いた円)があるのに、多角形になっていない(Q項の円/点のまま)
  - 本文の座標(多角形の頂点)が、描いた図形の頂点に入っていない(読み落とし)
  - 本文の円の中心のまわりに円が描けていない
  - 地点名(VOR/NDB)を中心とする円・航空路の区間閉鎖を、円/線にできていない
    (AIPの索引に無い地点の可能性。台湾(RCAA)の航空路は地点のデータが無いので「既知」として別に数える)
  - 多角形の辺の交差・面積が大きすぎる・Q項の中心から大きく離れている
  - 終わりが始まりより前(取消・差し替えで終わったものは除く)
点の障害物(Qコード OB)とチェックリスト(KK)は、座標があっても面ではないので対象外。

使い方:
  python notam_check.py --out-dir notam_out          # notam_out/reports/ に書く
  python notam_check.py --out-dir notam_out --stdout # 書かずに標準出力へ
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import notam_cn_collect as C
from geo_checks import ring_area_km2, ring_self_intersects

JST = dt.timezone(dt.timedelta(hours=9))
DRAWN = ("api", "text-polygon", "route-segment", "aip-chart-line")
AREA_MAX_KM2 = 3_000_000
QLINE_OFFSET_MAX_NM = 150
NON_AREA_SUBJECTS = ("OB", "KK")       # 障害物(点)・チェックリスト
NEAR_DEG = 2e-4                        # 本文の座標と図形の頂点を同じとみなす距離(約20m)

_NAMED_CENTER = re.compile(r"CENT(?:ER|RE)(?:ED|D)?\s+(?:AT|ON)\s+[A-Z][A-Z ]*?\s(?:VOR|NDB|DME|VORTAC)\b", re.I)
_ROUTE_CLOSURE = re.compile(r"(?:ATS\s+RTE|\bAWY\b|\bSEGMENT\b|VFR\s+CORRIDOR).*?\bCLSD\b|\bCLSD\b.*?(?:ATS\s+RTE|\bAWY\b)"
                            r"|AFFECTED\s+ROUTE|ROUTE\s+RESTRICTION", re.I | re.S)


def text_points(e_text):
    """E項の座標を (lat, lon, 種類) で返す。種類: 'vertex'(多角形の頂点) / 'center'(円の中心) / 'line'(帯の中心線)。
    括弧の中の臨時ウェイポイント定義は除く(収集側と同じ)。"""
    t = C._rewrite_each_side(C._SPLIT_DIGITS.sub("", e_text or ""))   # 「<線> WITH EACH SIDE OF 30KM」も帯として見る
    corridor = bool(C._CORRIDOR.search(t))
    out = []
    for m in C._TEXT_COORD.finditer(t):
        if C._is_named_waypoint_coord(t, m.start(), m.end()):
            continue
        if m["ns1"] is not None:
            ns, lat_d, ew, lon_d = m["ns1"], re.sub(r"[ \t]", "", m["lat1"]), m["ew1"], m["lon1"]
        else:
            ns, lat_d, ew, lon_d = m["ns2"], m["lat2"], m["ew2"], m["lon2"]
        lat, lon = C._dms(lat_d, 2), C._dms(lon_d, 3)
        if lat is None or lon is None or lat > 90 or lon > 180:
            continue
        lat, lon = (lat if ns == "N" else -lat), (lon if ew == "E" else -lon)
        is_center = bool(C._CIRCLE_BEFORE.search(t[max(0, m.start() - 120):m.start()])) or (
            bool(C._CENTER_BEFORE.search(t[max(0, m.start() - 40):m.start()])) and bool(C._RADIUS_AFTER.match(t, m.end())))
        out.append((lat, lon, "center" if is_center else "line" if corridor else "vertex"))
    return out


def rings_of(geom):
    if not geom:
        return []
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def check_notam(p, geoms):
    """1件のNOTAM(同じ番号の地物をまとめたもの)を点検し、(問題の文のリスト, 既知の対応外の文 or None) を返す。"""
    issues, known = [], None
    subject = (p.get("q_code") or "")[1:3]
    e = C.e_section(p.get("raw_text"), "")
    src = p.get("geometry_source")
    drawn = src in DRAWN
    pts = text_points(e)
    verts = [x for x in pts if x[2] == "vertex"]
    centers = [x for x in pts if x[2] == "center"]
    rings = [r for g in geoms for r in rings_of(g)]

    if subject not in NON_AREA_SUBJECTS:
        lines = [x for x in pts if x[2] == "line"]
        if not drawn and len(lines) >= 2:
            issues.append(f"本文に帯(線の両側)の座標 {len(lines)} 個があるのに、図形になっていない({src})")
        if not drawn and (len({(round(a, 4), round(b, 4)) for a, b, _ in verts}) >= 3 or centers):
            issues.append(f"本文に座標 {len(verts)} 個・円 {len(centers)} 個があるのに、多角形になっていない({src})")
        if not drawn and _NAMED_CENTER.search(e):
            issues.append(f"地点名を中心とする円を描けていない({_NAMED_CENTER.search(e).group(0)})")
        if not drawn and _ROUTE_CLOSURE.search(e) and not verts:
            if (p.get("fir") or "").startswith("RC"):
                known = "台湾の航空路閉鎖(地点のデータが無く線にできない)"
            else:
                issues.append("航空路の区間閉鎖を線にできていない(AIPの索引に無い地点・航空路の可能性)")

    if src in ("api", "text-polygon") and rings:
        vset = [(y, x) for r in rings for x, y in r]
        missing = [(a, b) for a, b, _ in verts
                   if not any(abs(a - y) < NEAR_DEG and abs(b - x) < NEAR_DEG for y, x in vset)]
        if missing and src == "text-polygon":
            a, b = missing[0]
            issues.append(f"本文の座標 {len(missing)} 個が図形の頂点に入っていない(例 {a:.4f}N {b:.4f}E)")
        for a, b, _ in centers:
            if not any(abs(sum(y for _, y in r[:-1]) / (len(r) - 1) - a) < 0.01
                       and abs(sum(x for x, _ in r[:-1]) / (len(r) - 1) - b) < 0.01 for r in rings):
                issues.append(f"本文の円(中心 {a:.4f}N {b:.4f}E)が描けていない")
                break
        for r in rings:
            if len(r) <= 66 and ring_self_intersects(r):      # 円(65点)より大きいものは帯など。重いので見ない
                issues.append("多角形の辺が交差している(頂点の順番の取り違えの可能性)")
                break
        big = max(ring_area_km2(r) for r in rings)
        if big > AREA_MAX_KM2:
            issues.append(f"面積が大きすぎる({big:,.0f} km²)")
    off = p.get("qline_offset_nm")
    if drawn and isinstance(off, (int, float)) and off > QLINE_OFFSET_MAX_NM:
        issues.append(f"図形が Q項の中心から {off:.0f} NM 離れている(座標の読み違いか、Q項の誤記)")
    vs, ve = p.get("valid_start") or "", p.get("valid_end") or ""
    if vs and ve and ve < vs and not p.get("ended_by"):
        issues.append(f"終わり({ve[:16]})が始まり({vs[:16]})より前")
    return issues, known


def run(geojson):
    groups = {}
    for f in geojson.get("features", []):
        p = f["properties"]
        k = (p.get("issuer"), p.get("number"), p.get("valid_start"))
        groups.setdefault(k, (p, []))[1].append(f.get("geometry"))
    rows = []
    for (_, _, _), (p, geoms) in sorted(groups.items(), key=lambda kv: (kv[1][0].get("fir") or "", kv[1][0].get("number") or "")):
        issues, known = check_notam(p, geoms)
        rows.append((p, issues, known))
    return rows


def render(rows, now):
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    bad = [r for r in rows if r[1]]
    known = [r for r in rows if r[2] and not r[1]]
    out = [f"# NOTAM 描画チェック {day}", "",
           f"点検 {len(rows)} 件(有効・有効前) / 要確認 {len(bad)} 件 / 既知の対応外 {len(known)} 件", ""]
    if not bad:
        out.append("問題は見つかりませんでした。")
        out.append("")
    for p, issues, _ in bad:
        out.append(f"## {p.get('number')} {p.get('fir')} {p.get('q_code')}")
        for s in issues:
            out.append(f"- **{s}**")
        e = " ".join(C.e_section(p.get("raw_text"), "").split())
        out.append(f"- 有効期間: {(p.get('valid_start') or '')[:16]} 〜 {(p.get('valid_end') or '')[:16]} UTC / 図形: {p.get('geometry_source')}")
        out.append(f"- E項: {e[:600]}")
        out.append("")
    if known:
        out.append("## 既知の対応外(参考)")
        for p, _, k in known:
            out.append(f"- {p.get('number')} {p.get('fir')}: {k}")
    return "\n".join(out).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="notam_out")
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    geojson = json.loads((out / "notam_cn.geojson").read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    rows = run(geojson)
    md = render(rows, now)
    if a.stdout:
        sys.stdout.write(md)
        return
    rep = out / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(JST).strftime("%Y-%m-%d")
    (rep / f"notam_check_{day}.md").write_text(md, encoding="utf-8")
    (rep / "notam_check_latest.md").write_text(md, encoding="utf-8")
    print(f"checked {len(rows)}, issues {sum(1 for r in rows if r[1])}")


if __name__ == "__main__":
    main()
