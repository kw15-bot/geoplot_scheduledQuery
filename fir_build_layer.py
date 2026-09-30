#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
fir_build_layer.py
==================

ビューアの「レイヤー」(NOTAMモード)の FIR 境界 layers/fir.geojson を作る(HANDOFF_NOTAM.md §23)。

元データ: ArcGIS Online のアイテム「ICAO Flight Information Region」(4b70cff99cf14565b6671a314c8ea6e8)
  https://services5.arcgis.com/62o2qANhRqripAuB/arcgis/rest/services/ICAO_Flight_Information_Region/FeatureServer/12
  (ICAO の FIR 図 2020-12-18 版。アイテムの説明は「航法・公式用ではない、デモ用」。344 件)
取得(GeoJSON):
  .../FeatureServer/12/query?where=1%3D1&outFields=ICAOCODE,FIRname,KIND,REGION,centlong,centlat
      &returnGeometry=true&maxAllowableOffset=0.01&geometryPrecision=3&outSR=4326&f=geojson&resultRecordCount=2000

出力: 1 FIR(元の1件)ごとに MultiLineString(境界線)。properties は icao(ICAOCODE)・name(FIRname)と、
ラベルを置く位置 label=[lon, lat](元データの centlong/centlat)。
  - 地図は東経-60〜330度で描く(NOTAMと同じく、西経30度より西は+360度)。全体が西経30度より西の FIR は +360 度する。
  - 日付変更線で東西2件に分かれている FIR(KZAK・NZZO・PAZN など)は、つなぎ目(経度±180の辺)を線にしない。
    ラベルは分かれたそれぞれに置く(KZAK なら グアム側と ハワイ側の両方に出る)。
  - 線は Douglas-Peucker で 0.02 度に間引く。

使い方: python fir_build_layer.py fir_raw.geojson [--out layers/fir.geojson]
"""

import argparse
import json
import os
import sys

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "layers", "fir.geojson")
TOL = 0.02


def rdp(pts, tol=TOL):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        n2 = dx * dx + dy * dy
        best, bi = -1.0, -1
        for k in range(i + 1, j):
            x, y = pts[k]
            if n2 == 0:
                d = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / n2))
                d = (x - x1 - t * dx) ** 2 + (y - y1 - t * dy) ** 2
            if d > best:
                best, bi = d, k
        if bi > 0 and best > tol * tol:
            keep[bi] = True
            stack += [(i, bi), (bi, j)]
    return [p for p, k in zip(pts, keep) if k]


def polygons(geom):
    return [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]


def on_seam(a, b):
    return abs(abs(a[0]) - 180) < 1e-6 and abs(abs(b[0]) - 180) < 1e-6


def ring_lines(ring, shift):
    """リングを線にする。経度±180の辺(日付変更線のつなぎ目)で切る。"""
    lines, cur = [], [ring[0]]
    for a, b in zip(ring, ring[1:]):
        if on_seam(a, b):
            if len(cur) > 1:
                lines.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if len(cur) > 1:
        lines.append(cur)
    out = []
    for ln in lines:
        ln = [[round(x + shift, 3), round(y, 3)] for x, y in ln]
        # 西経180度(元の値 -180)は +360 すると 180。東側の +180 と同じ線になる
        ln = rdp(ln)
        if len(ln) > 1:
            out.append(ln)
    return out


def ring_centroid(ring):
    """リングの重心(面積で重み付け)。"""
    a = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        c = x1 * y2 - x2 * y1
        a += c
        cx += (x1 + x2) * c
        cy += (y1 + y2) * c
    if abs(a) < 1e-12:
        return sum(x for x, _ in ring) / len(ring), sum(y for _, y in ring) / len(ring)
    return cx / (3 * a), cy / (3 * a)


def build(raw):
    feats = []
    for f in raw["features"]:
        p, g = f["properties"], f["geometry"]
        if not g:
            continue
        polys = polygons(g)
        lons = [c[0] for poly in polys for r in poly for c in r]
        shift = 360 if max(lons) <= -30 else 0
        lines = [ln for poly in polys for r in poly for ln in ring_lines(r, shift)]
        if not lines:
            continue
        lon = p.get("centlong")
        lat = p.get("centlat")
        if lon is not None:
            lon += shift                                  # 線と同じだけずらす(西経30度をまたぐ FIR はずらさない)
        # centlong/centlat が入っていない(0,0)・範囲の外のものは、いちばん大きいリングの重心に置く
        if lon is None or lat is None or (lon % 360 == 0 and lat == 0) or not (min(lons) + shift <= lon <= max(lons) + shift):
            big = max((r for poly in polys for r in poly[:1]), key=len)
            lon, lat = ring_centroid(big)
            lon += shift
        feats.append({"type": "Feature",
                      "properties": {"icao": p.get("ICAOCODE"), "name": p.get("FIRname"),
                                     "label": [round(lon, 3), round(lat, 3)] if lon is not None and lat is not None else None},
                      "geometry": {"type": "MultiLineString", "coordinates": lines}})
    feats.sort(key=lambda f: (f["properties"]["icao"] or "", f["properties"]["name"] or ""))
    return {"type": "FeatureCollection",
            "source": "ICAO Flight Information Region (ArcGIS Online item 4b70cff99cf14565b6671a314c8ea6e8), ICAO 2020-12-18",
            "features": feats}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", help="ArcGIS から取得した GeoJSON")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()
    with open(a.raw, encoding="utf-8") as fh:
        gj = build(json.load(fh))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(gj, fh, separators=(",", ":"), ensure_ascii=False)
    n = sum(len(ln) for f in gj["features"] for ln in f["geometry"]["coordinates"])
    print(f"fir layer: {len(gj['features'])} FIRs, {sum(1 for f in gj['features'] if f['properties']['label'])} labels, "
          f"{n} points -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
