"""
manual_parse.py
===============
ビューアの「手動」タブで貼り付けた文章を、収集と同じ読み取りで図形にする(2026-11-13、ビューア v5.3.0)。
ビューアがブラウザの中の Python(Pyodide)でこのファイルと収集のスクリプトを読み込み、parse(text) を呼ぶ。

  - NOTAM(Q) 行のある ICAO 形式。複数件を続けて貼ってもよい)→ notam_cn_collect.build_geometry
    (E項の多角形・円・回廊、AIP の図の線、Q項の円)。航空路の区間閉鎖は AIP 索引が要るのでここでは線にならない。
  - 航行警報(中国海事局の文面。北纬/东经・N/E・度分秒など)→ msa_scraper.parse_coordinate_groups
    (エリアごとの分け方・円・扇形・円弧)。1点=点、2点=線、3点以上=面(自動の航行警報タブと同じ)。
どちらでもなければ kind=None を返し、ビューアは今までの座標の読み取りで描く。
"""
import json
import re
import sys
import types

# 収集用の通信ライブラリはブラウザでは使わない(読み込みだけ通す)
for _m in ("requests", "urllib3"):
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

import msa_scraper as M          # noqa: E402
import notam_cn_collect as C     # noqa: E402
import notam_paste_import as P   # noqa: E402

_NOTAM_RE = re.compile(r"\bQ\)\s*[A-Z]{4}\s*/\s*Q[A-Z]{4}")
_MSA_RE = re.compile(r"航[行]?警|海事局|北纬|东经|北緯|東経|禁止驶入|实弹|军事")


def _notam(text):
    feats, problems = P.parse_text(text)
    out = []
    for f in feats:
        n = f["properties"]["coreNOTAMData"]["notam"]
        icao = f["properties"]["coreNOTAMData"]["notamTranslation"][0]["formattedText"]
        geom, src = C.build_geometry(f, n, C.e_section(icao, n.get("text")))
        if not geom:
            continue
        out.append({"type": "Feature", "geometry": geom,
                    "properties": {"label": n["number"], "source": src, "fir": n.get("affectedFir"),
                                   "valid_start": n.get("effectiveStart"), "valid_end": n.get("effectiveEnd")}})
    return out, [p[1] for p in problems]


def _msa(text):
    m = re.search(r"[一-龥]{1,4}航警\d+/\d+", text)
    label = m.group(0) if m else None
    out = []
    for grp in M.parse_coordinate_groups(text):
        if not grp:
            continue
        if len(grp) == 1:
            geom = {"type": "Point", "coordinates": [grp[0]["lon"], grp[0]["lat"]]}
        elif len(grp) == 2:
            geom = {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in grp]}
        else:
            ring = [[p["lon"], p["lat"]] for p in M._order_ring_points(grp)]
            geom = {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
        out.append({"type": "Feature", "geometry": geom, "properties": {"label": label, "source": "msa"}})
    return out


def parse(text):
    """{"kind": "notam" | "msa" | None, "features": [...], "problems": [...]} を JSON 文字列で返す。"""
    text = text or ""
    if _NOTAM_RE.search(text):
        feats, problems = _notam(text)
        return json.dumps({"kind": "notam", "features": feats, "problems": problems}, ensure_ascii=False)
    if _MSA_RE.search(text):
        return json.dumps({"kind": "msa", "features": _msa(text), "problems": []}, ensure_ascii=False)
    return json.dumps({"kind": None, "features": [], "problems": []})
