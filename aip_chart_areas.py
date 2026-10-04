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

図の番号ではなく空港の本文(AD 2.20 など)で決められた線は NAMED_LINES に持つ(例: 南京 ZSNJ の制限線 B-C-D-E)。
NOTAMの本文に、その空港(指標・地名)と線の呼び名が両方あれば、その線を返す。
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


def _dms(v):
    """N313950 / E1175950 形式(度分秒) -> 10進の度。"""
    h, d = v[0], v[1:]
    w = 2 if h in "NS" else 3
    x = int(d[:w]) + int(d[w:w + 2]) / 60 + int(d[w + 2:w + 4]) / 3600
    return -x if h in "SW" else x


# 空港の本文で決められた線。match: 本文に両方が必要な語(空港, 線の呼び名)の正規表現、points: 順に(緯度, 経度)。
NAMED_LINES = [
    # ZSNJ AD 2.20 6.1 Warning(AIRAC AMDT 09/26、EFF2609021600):
    # All aircraft flying across south of restriction line without ATC clearance is forbidden strictly.
    # The restriction line is connection of B, C, D and E.
    {"name": "ZSNJ AD 2.20 6.1 restriction line (B-C-D-E)",
     "airport": re.compile(r"\b(ZSNJ|NANJING|LUKOU)\b"),
     "line": re.compile(r"RESTRICTION\s+LINE|CONTROL\s+LINE|\bB\s*-\s*C\s*-\s*D\s*-\s*E\b"),
     "points": [(_dms("N313950"), _dms("E1175950")), (_dms("N313640"), _dms("E1182930")),
                (_dms("N313400"), _dms("E1184208")), (_dms("N313200"), _dms("E1190200"))]},
]

_CHART_REF = re.compile(r"\b([A-Z]{4}-\d+[A-Z]?-\d+[A-Z]?)\b")
# (WEST AND NORTH OF G-H-J-K) のような「<方角> OF <点>-<点>-...」
_POINT_CHAIN = re.compile(r"\bOF\s+([A-Z0-9]{1,5}(?:\s*-\s*[A-Z0-9]{1,5})+)\b")


def chart_refs_missing(e_text):
    """E項が参照しているAIPの図のうち、線にできないもの(点が未登録の図、または挙げた点が図に無い)の名前。
    日次レポートで「どの図を登録すれば描けるか」を示すのに使う。"""
    t = (e_text or "").upper()
    if "AIP" not in t:
        return []
    charts = sorted(set(_CHART_REF.findall(t)))
    # 区域を図の点で示しているもの(「OF G-H-J-K」のような点の並び)だけ。手順の図を挙げて
    # 「SID ... U/S」と言っているだけのもの(ZGZU G4414/26 等)は図があっても描く区域が無いので対象外
    if not charts or not _POINT_CHAIN.search(t) or chart_boundary_geometry(e_text):
        return []
    return charts


def chart_boundary_geometry(e_text):
    """E項が図(CHART_POINTS にあるもの)を参照し、その図の点を並べていれば LineString を返す。
    空港の本文で決められた線(NAMED_LINES)に当たればその線。無ければ None。"""
    t = (e_text or "").upper()
    for nl in NAMED_LINES:
        if nl["airport"].search(t) and nl["line"].search(t):
            return {"type": "LineString",
                    "coordinates": [[round(lon, 6), round(lat, 6)] for lat, lon in nl["points"]]}
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
