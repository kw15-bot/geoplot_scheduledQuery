#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
aip_chart_areas.py
==================

AIPの図(チャート)を参照して区域を示すNOTAMを線にする(HANDOFF_NOTAM.md §24)。

例(ZWUQ W1338/26):
  REF AIP CHINA ZWWW-3P-5 AND ZWWW-3P-6 ,THE RESTRICTION AREA(WEST AND NORTH OF G-H-J-K)ACTIVE, ...
本文には座標が無く、図に書かれた点 G・H・J・K の座標が要る。図から読み取った点を CHART_POINTS に持ち、
NOTAMが挙げた点の順に結んだ線(LineString)を返す。区域の外側の境界は図にも無い(「G-H-J-Kの西と北」)ので、
面ではなく境界の線だけを描く。geometry_source は "aip-chart-line"。

点を足すとき: 図の名前(例 ZWWW-3P-5)ごとに、点の名前 -> (緯度, 経度)。図の版(EFF)をコメントに残す。
"""

import re

# 図から読み取った点(緯度, 経度)。
CHART_POINTS = {
    # ZWWW-3P-5 STANDARD DEPARTURE CHART-INSTRUMENT RNAV RWY26L/R(NIXER)、EFF2507091600。
    # 注記 1: When the restriction area (west and north of G-H-J-K) active, aircraft flying into the area is forbidden.
    "ZWWW-3P-5": {
        "G": (43 + 52.5 / 60, 87 + 13.1 / 60),
        "H": (43 + 52.2 / 60, 87 + 18.9 / 60),
        "J": (43 + 53.5 / 60, 87 + 20.8 / 60),
        "K": (44 + 0.3 / 60, 87 + 21.5 / 60),
    },
}
# ZWWW-3P-6 SID RNAV RWY26L/R(VARMI)、EFF2507091600。注記・G/H/J/K の座標は 3P-5 と同じ(照合済み)。
CHART_POINTS["ZWWW-3P-6"] = CHART_POINTS["ZWWW-3P-5"]

_CHART_REF = re.compile(r"\b([A-Z]{4}-\d+[A-Z]?-\d+[A-Z]?)\b")
# (WEST AND NORTH OF G-H-J-K) のような「<方角> OF <点>-<点>-...」
_POINT_CHAIN = re.compile(r"\bOF\s+([A-Z0-9]{1,5}(?:\s*-\s*[A-Z0-9]{1,5})+)\b")


def chart_boundary_geometry(e_text):
    """E項が図(CHART_POINTS にあるもの)を参照し、その図の点を並べていれば LineString を返す。無ければ None。"""
    t = (e_text or "").upper()
    if "AIP" not in t:
        return None
    charts = [c for c in _CHART_REF.findall(t) if c in CHART_POINTS]
    if not charts:
        return None
    for m in _POINT_CHAIN.finditer(t):
        names = [s.strip() for s in m.group(1).split("-")]
        for c in charts:
            pts = CHART_POINTS[c]
            if all(n in pts for n in names):
                return {"type": "LineString",
                        "coordinates": [[round(pts[n][1], 6), round(pts[n][0], 6)] for n in names]}
    return None
