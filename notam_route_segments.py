#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_route_segments.py
=======================

NOTAM の E項に書かれた「航空路(ATS route)の区間閉鎖」を、中国AIPデータセット(AIP-DS)から
作った索引(aip_build_index.py、HANDOFF_NOTAM.md §19)で線に変換する。

対応する書き方(実データ ZBPE/ZLHW/ZWUQ/ZGZU/ZSHA 2026-09 で確認):
  SEGMENT SADAN - MAGIV OF ATS RTE W187 CLSD.
  ATS ROUTE G586 SEGMENT GMC-ALGAG, ATS ROUTE R343 SEGMENT MAMSI-ABTUD ... NOT AVBL
  FLW SEGMENT OF ATS RTE CLSD ...:  1. W195 : OLSUG - ALGOT.  2. B330 : DAMRO - JELAN.
  A599: PANLONG VOR'PLT'- SHANGRAO VOR'SHR'.          (無番号の項目)
  SEGMENT 20KM WEST OF YARKANT VOR/DME 'DSC'-LEDEM OF ATS RTE B215   (地点からの距離)
  Y1   :SADAN-170KM EAST OF SADAN
  SEGMENT OMBON - N350737E1000535 OF ATS RTE Y1       (座標の端点は航空路上に投影)

迂回の指示(ADJUST TO / REROUTING / ISSUE FPL / FLIGHTS VIA ... 以降)は閉鎖区間ではないので、
その手前で本文を切ってから読む。

区間は航空路の線(RouteSegmentのつながり)に沿ってたどる。"120KM EAST OF SADAN" は SADAN から
航空路に沿って東向きに120km進んだ地点とする(航空路から離れた点にはしない)。

索引は環境変数 AIPDS_INDEX(復号済みの .json または .json.gz のパス)から読む。
無ければ何もしない(線を作らず、従来どおりQ項の円・点になる)。
"""

import gzip
import heapq
import html
import json
import math
import os
import re

EARTH_KM = 6371.0088
PROJECT_MAX_KM = 30.0     # 座標や航空路外の地点を航空路に投影してよい最大距離

_index = None             # 読み込み済み索引(RouteIndex)。False=読めなかった/無い
_DIRS = {
    "NORTH": 0, "N": 0, "NORTHEAST": 45, "NE": 45, "EAST": 90, "E": 90, "SOUTHEAST": 135, "SE": 135,
    "SOUTH": 180, "S": 180, "SOUTHWEST": 225, "SW": 225, "WEST": 270, "W": 270, "NORTHWEST": 315, "NW": 315,
}
_RTE = r"[A-Z]{1,2}\d{1,4}"
_COORD = r"[NS]\d{4,6}(?:\.\d+)?\s*[EW]\d{5,7}(?:\.\d+)?"
_EP = (r"(?:\d+(?:\.\d+)?\s*(?:KM|NM)\s+(?:NORTH|SOUTH|EAST|WEST|NORTHEAST|NORTHWEST|SOUTHEAST|SOUTHWEST|NE|NW|SE|SW)"
       r"\s+OF\s+)?(?:" + _COORD + r"|[A-Z]{2,5})")
_EP_PARTS = re.compile(r"(?:(?P<d>\d+(?:\.\d+)?)\s*(?P<u>KM|NM)\s+(?P<dir>[A-Z]+)\s+OF\s+)?(?P<base>.+)$")
_RANGE = rf"(?P<a>{_EP})\s*-\s*(?P<b>{_EP})"
_ATS = r"ATS\s+R(?:OU)?TES?"
# 区間が括弧で囲まれた書き方もある: "SEGMENT (50KM WEST OF SADAN-100KM EAST OF PAMLI) OF ATS RTE W186" (ZWUQ A4996/26)
_PAT_SEG_OF = re.compile(rf"SEGMENT\s+\(?\s*{_RANGE}\s*\)?\s+OF\s+{_ATS}\s+(?P<r>{_RTE})\b")
_PAT_RTE_SEG = re.compile(rf"{_ATS}\s+(?P<r>{_RTE})\s+SEGMENT\s+{_RANGE}")
_PAT_ITEM = re.compile(rf"(?:^|[\s:.,;])(?:\d+\s*\.\s*)?(?P<r>{_RTE})\s*:\s*{_RANGE}")
# 迂回の指示などが始まる所。ここより後ろは閉鎖区間の記述ではない
_CUT = re.compile(r"ALL\s+AFFECTED|\bADJUST|REROUT|ISSUE\s+FPL|FLIGHT\s+PLANS?\b|\bFPL\b|"
                  r"\bFLIGHTS?\s+(?:ALONG|VIA|FM|FROM)\b|SCHEDULED\s+FLIGHTS?")
_NAVAID = re.compile(r"\b[A-Z]+\s+(?:VOR/DME|VORDME|VOR|NDB|DME)\s*'([A-Z0-9]{2,3})'")


# ----------------------------------------------------------------------------- 索引
class RouteIndex:
    def __init__(self, data):
        self.meta = data.get("meta") or {}
        self.points = {k: (v[0], float(v[1]), float(v[2])) for k, v in (data.get("points") or {}).items()}
        self.kinds = {k: (v[3] if len(v) > 3 else "WPT") for k, v in (data.get("points") or {}).items()}
        self.by_name = {}
        for uid, (name, _, _) in self.points.items():
            self.by_name.setdefault(name, []).append(uid)
        routes = data.get("routes") or {}
        self.graph = {}                                   # designator -> {uuid: set(uuid)}
        for r, a, b in data.get("segments") or []:
            des = routes.get(r)
            if not des or a not in self.points or b not in self.points or a == b:
                continue
            g = self.graph.setdefault(des, {})
            g.setdefault(a, set()).add(b)
            g.setdefault(b, set()).add(a)

    def ll(self, uid):
        return self.points[uid][1], self.points[uid][2]


def load_index(path=None):
    """AIPDS_INDEX の索引を一度だけ読む。無い・壊れている場合は None。"""
    global _index
    if path is None and _index is not None:
        return _index or None
    path = path or os.environ.get("AIPDS_INDEX")
    if not path or not os.path.isfile(path):
        _index = False
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        _index = RouteIndex(json.loads(raw))
    except (OSError, ValueError):
        _index = False
        return None
    return _index


def set_index(data):
    """テスト用: dict(索引と同じ形)を直接設定する。None で解除。"""
    global _index
    _index = RouteIndex(data) if data is not None else False


# ----------------------------------------------------------------------------- 幾何
def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _xy(origin, p):
    """origin まわりの局所平面座標(km, 東・北)。"""
    k = math.pi / 180 * EARTH_KM
    return ((p[1] - origin[1]) * k * math.cos(math.radians(origin[0])), (p[0] - origin[0]) * k)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _project(g, idx, p):
    """点 p に最も近い航空路上の位置 -> (距離km, u, v, 投影点)。"""
    best = None
    for u, nbrs in g.items():
        for v in nbrs:
            if u > v:
                continue
            a, b = idx.ll(u), idx.ll(v)
            ax, ay = _xy(p, a)
            bx, by = _xy(p, b)
            dx, dy = bx - ax, by - ay
            L = dx * dx + dy * dy
            t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
            q = _lerp(a, b, t)
            d = _km(p, q)
            if best is None or d < best[0]:
                best = (d, u, v, q)
    return best


def _parse_coord(s):
    m = re.match(r"([NS])(\d{4,6}(?:\.\d+)?)\s*([EW])(\d{5,7}(?:\.\d+)?)$", s)
    if not m:
        return None

    def dms(digits, dl):
        whole, _, frac = digits.partition(".")
        d, rest = int(whole[:dl]), whole[dl:]
        mi = int(rest[:2]) if len(rest) >= 2 else 0
        se = float(rest[2:4] + ("." + frac if frac else "")) if len(rest) >= 4 else 0.0
        return d + mi / 60 + se / 3600

    lat, lon = dms(m[2], 2), dms(m[4], 3)
    return (-lat if m[1] == "S" else lat, -lon if m[3] == "W" else lon)


def _locate(g, idx, base):
    """端点の基準(地点名・座標)を航空路上の位置にする。
    戻り値: ("node", uuid) / ("edge", u, v, (lat, lon)) / None"""
    c = _parse_coord(base)
    if c is None:
        on_route = [u for u in idx.by_name.get(base, []) if u in g]
        if on_route:
            return ("node", on_route[0])
        cands = idx.by_name.get(base, [])
        if not cands:
            return None
        c = idx.ll(cands[0])
    pr = _project(g, idx, c)
    if pr is None or pr[0] > PROJECT_MAX_KM:
        return None
    _, u, v, q = pr
    if _km(q, idx.ll(u)) < 0.05:
        return ("node", u)
    if _km(q, idx.ll(v)) < 0.05:
        return ("node", v)
    return ("edge", u, v, q)


def _walk(g, idx, start, heading, dist_km):
    """航空路上の start(node) から、方位 heading(度)寄りに dist_km だけ線に沿って進む。
    戻り値: ("edge", u, v, 到達点) または ("node", uuid)。"""
    hx, hy = math.sin(math.radians(heading)), math.cos(math.radians(heading))
    origin = idx.ll(start)
    best = None
    for first in g.get(start, ()):
        prev, cur, left, path = start, first, dist_km, [start]
        while True:
            seg = _km(idx.ll(prev), idx.ll(cur))
            if seg >= left:
                end = ("edge", prev, cur, _lerp(idx.ll(prev), idx.ll(cur), left / seg if seg else 0))
                break
            left -= seg
            path.append(cur)
            nxt = [n for n in g.get(cur, ()) if n not in path]
            if not nxt:
                end = ("node", cur)
                break
            # 分岐では、指定方位に最も沿う方へ進む
            prev, cur = cur, max(nxt, key=lambda n: _dot(_xy(idx.ll(cur), idx.ll(n)), hx, hy))
        pt = end[3] if end[0] == "edge" else idx.ll(end[1])
        score = _dot(_xy(origin, pt), hx, hy)
        if best is None or score > best[0]:
            best = (score, end)
    return best[1] if best else None


def _dot(v, hx, hy):
    n = math.hypot(v[0], v[1])
    return (v[0] * hx + v[1] * hy) / n if n else -2.0


def _resolve(g, idx, ep):
    m = _EP_PARTS.match(ep.strip())
    loc = _locate(g, idx, re.sub(r"\s+", "", m["base"]))
    if loc is None or not m["d"]:
        return loc
    heading = _DIRS.get(m["dir"])
    if heading is None:
        return None
    d = float(m["d"]) * (1.852 if m["u"] == "NM" else 1.0)
    if loc[0] == "edge":                   # 基準点が区間の途中: 近い方の端点から歩く(誤差は区間長未満)
        u, v, q = loc[1], loc[2], loc[3]
        loc = ("node", u if _km(q, idx.ll(u)) <= _km(q, idx.ll(v)) else v)
    return _walk(g, idx, loc[1], heading, d)


def _dijkstra(g, idx, src):
    dist, prev, pq = {src: 0.0}, {}, [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        for v in g.get(u, ()):
            nd = d + _km(idx.ll(u), idx.ll(v))
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    return dist, prev


def _chain(prev, src, dst):
    out = [dst]
    while out[-1] != src:
        out.append(prev[out[-1]])
    return out[::-1]


def _path(g, idx, a, b):
    """航空路上の位置 a から b までの線([lat, lon] の列)。つながっていなければ None。"""
    def anchors(loc):             # (ノード, そのノードから loc までの距離, 付け足す点)
        if loc[0] == "node":
            return [(loc[1], 0.0, None)]
        return [(loc[1], _km(idx.ll(loc[1]), loc[3]), loc[3]), (loc[2], _km(idx.ll(loc[2]), loc[3]), loc[3])]

    if a[0] == "edge" and b[0] == "edge" and {a[1], a[2]} == {b[1], b[2]}:
        return [a[3], b[3]]
    best = None
    for na, da, pa in anchors(a):
        dist, prev = _dijkstra(g, idx, na)
        for nb, db, pb in anchors(b):
            if nb not in dist:
                continue
            total = da + dist[nb] + db
            if best is None or total < best[0]:
                nodes = _chain(prev, na, nb)
                pts = ([pa] if pa else []) + [idx.ll(n) for n in nodes] + ([pb] if pb else [])
                best = (total, pts)
    if best is None:
        return None
    pts = [p for i, p in enumerate(best[1]) if i == 0 or _km(p, best[1][i - 1]) > 1e-3]
    return pts if len(pts) >= 2 else None


# ----------------------------------------------------------------------------- 本文
def closure_text(e_text):
    """E項を正規化し、迂回指示より前(閉鎖区間の記述)だけを返す。"""
    t = html.unescape(e_text or "").upper()
    t = re.sub(r"\s+", " ", t)
    m = _CUT.search(t)
    if m:
        t = t[:m.start()]
    return _NAVAID.sub(r"\1", t)


def find_closures(e_text):
    """[(航空路, 端点A, 端点B), ...](本文の順、重複なし)。"""
    t = closure_text(e_text)
    found = []
    for pat in (_PAT_SEG_OF, _PAT_RTE_SEG):
        found += [(m.start(), m["r"], m["a"], m["b"]) for m in pat.finditer(t)]
    # "FLW SEGMENT OF ATS RTE CLSD: 1. W195 : OLSUG - ALGOT." の項目形式
    if re.search(rf"SEGMENTS?\s+OF\s+{_ATS}", t):
        found += [(m.start(), m["r"], m["a"], m["b"]) for m in _PAT_ITEM.finditer(t)]
    out = []
    for _, r, a, b in sorted(found):
        key = (r, a.strip(), b.strip())
        if key not in out:
            out.append(key)
    return out


def route_closure_lines(e_text, index=None):
    """閉鎖区間の線 [[[lon, lat], ...], ...]。作れなければ空リスト。"""
    idx = index or load_index()
    if not idx:
        return []
    lines = []
    for r, a, b in find_closures(e_text):
        g = idx.graph.get(r)
        if not g:
            continue
        la, lb = _resolve(g, idx, a), _resolve(g, idx, b)
        if la is None or lb is None:
            continue
        pts = _path(g, idx, la, lb)
        if pts:
            lines.append([[round(p[1], 6), round(p[0], 6)] for p in pts])
    return lines


def route_closure_geometry(e_text, index=None):
    lines = route_closure_lines(e_text, index)
    if not lines:
        return None
    return {"type": "LineString", "coordinates": lines[0]} if len(lines) == 1 \
        else {"type": "MultiLineString", "coordinates": lines}


# ----------------------------------------------------------------------------- 地点を中心にした円
# "THE AREA WITHIN A CIRCLE CENTERED AT SHIQUANHE VOR 'SQH' WITH RADIUS OF 30KM CLSD" (ZWUQ A5003/26) のように、
# 円の中心が座標ではなく地点名(VOR・NDB・ウェイポイント)で書かれているもの。中心の座標は索引から引く。
_UNIT = r"(?P<r>\d+(?:\.\d+)?)\s*(?P<u>KM|NM|M)\b"
_PT = r"(?P<p>[A-Z]{2,5})\b"
_CENT = r"CENT(?:ER|RE)(?:ED|D)?\s+(?:AT|ON)"
_NAMED_CIRCLES = [
    re.compile(rf"CIRCLE\s+{_CENT}\s+{_PT}\s+(?:WITH\s+)?(?:A\s+)?RADIUS\s+(?:OF\s+)?{_UNIT}"),
    re.compile(rf"RADIUS\s+(?:OF\s+)?{_UNIT}\s+{_CENT}\s+{_PT}"),
    re.compile(rf"{_UNIT}\s+RADIUS\s+(?:OF|AROUND|{_CENT})\s+{_PT}"),
]


def _normalize(e_text):
    t = re.sub(r"\s+", " ", html.unescape(e_text or "").upper())
    return _NAVAID.sub(r"\1", t)


def has_named_center_circle(e_text):
    """地点名を中心にした円の書き方があるか(索引の有無に関係なく判定)。"""
    t = _normalize(e_text)
    return any(p.search(t) for p in _NAMED_CIRCLES)


def named_center_circles(e_text, index=None):
    """[(lat, lon, 半径NM), ...](本文の順)。中心の地点が索引に無いものは捨てる。"""
    idx = index or load_index()
    if not idx:
        return []
    t = _normalize(e_text)
    found = []
    for pat in _NAMED_CIRCLES:
        for m in pat.finditer(t):
            uids = idx.by_name.get(m["p"]) or []
            if not uids:
                continue
            # 同じ名前の地点が複数あるときは航法施設(VOR等)を優先する
            navs = [u for u in uids if u in idx.kinds and idx.kinds[u] != "WPT"]
            uid = navs[0] if navs else uids[0]
            r = float(m["r"])
            r_nm = r / 1.852 if m["u"] == "KM" else r / 1852.0 if m["u"] == "M" else r
            lat, lon = idx.ll(uid)
            found.append((m.start(), lat, lon, r_nm))
    out, seen = [], set()
    for _, lat, lon, r_nm in sorted(found):
        key = (round(lat, 5), round(lon, 5), round(r_nm, 3))
        if key not in seen:
            seen.add(key)
            out.append((lat, lon, r_nm))
    return out
