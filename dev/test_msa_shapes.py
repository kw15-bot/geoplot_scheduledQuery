# -*- coding: utf-8 -*-
"""円・円弧・扇形の読み取り(msa_scraper.py)と描画チェック(msa_check.py)のテスト。
python dev/test_msa_shapes.py"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import msa_scraper as M  # noqa: E402
import msa_check as C    # noqa: E402

LIAO_423 = "渤海，自10月14日0600时至1800时在以38-52.14N121-01.75E为圆心，2海里为半径的范围内执行军事任务。禁止驶入。"
JI_573 = ("渤海，8月30日0000时至8月31日2400时， 以N 39°24′35″、E 119°13′44″为顶点，正东方向至N39°24′35″、E 119°17′32″，"
          "再至N39°19′21″、E 119°29′47″端点，顶点正南偏西6°方向至N39°11′16″、E 119°11′54″端点，"
          "以顶点为中心半径25公里的部分圆弧将两个端点连接，形成不规则的闭合范围。该区域内进行实弹射击活动。禁止驶入。")
JI_617 = ("渤海，9月15日0000时至9月19日2400时， 以N 39°24′35″、E 119°13′44″为顶点，正东方向至正南偏西6°、圆心角为96°的扇形区域。"
          "该扇形区域所对应圆弧的2个端点坐标分别为N 39°24′14″、E 119°28′24″和N 39°13′13″、E 119°11′51″。该扇形区域内进行实弹射击活动。禁止驶入。")
HU_561 = ("东海，2026年7月20日0600时至22日1200时，在以31-12.18N 123-42.69E为圆心，半径3千米的圆形海域内和以31-12.18N 123-42.69E为圆心，"
          "方位158°-194°，半径5千米的扇形的海域进行火箭发射。禁止驶入。")
PLAIN = "南海，8月4日至5日，每天0600时至2000时，以下四点连线海域内进行实弹射击：22-42.17N 115-21.83E; 22-40.10N 115-33.43E; 22-35.50N 115-29.17E；22-38.53N 115-22.32E。禁止驶入。"


def km(a, b):
    lat0 = math.radians((a["lat"] + b["lat"]) / 2)
    return math.hypot((a["lat"] - b["lat"]) * 111.32, (a["lon"] - b["lon"]) * 111.32 * math.cos(lat0))


def test_circle_fanwei():
    g = M.parse_coordinate_groups(LIAO_423)
    assert len(g) == 1 and len(g[0]) == 64 and {p["fmt"] for p in g[0]} == {"CIRCLE"}
    c = {"lat": 38 + 52.14 / 60, "lon": 121 + 1.75 / 60}
    assert abs(km(g[0][0], c) - 2 * 1.852) < 0.05


def test_sector_arc_between_endpoints():
    g = M.parse_coordinate_groups(JI_573)
    assert len(g) == 1
    arc = [p for p in g[0] if p["fmt"] == "ARC"]
    assert len(arc) >= 20
    v = {"lat": 39 + 24 / 60 + 35 / 3600, "lon": 119 + 13 / 60 + 44 / 3600}
    assert all(abs(km(p, v) - 25) < 0.1 for p in arc)


def test_sector_endpoints_listed_after():
    g = M.parse_coordinate_groups(JI_617)
    assert len(g) == 1
    assert len([p for p in g[0] if p["fmt"] == "ARC"]) >= 25     # 96°を約3°おき


def test_bearing_sector_kept_with_circle():
    g = M.parse_coordinate_groups(HU_561)
    assert len(g) == 2
    fm = [{p["fmt"] for p in x} for x in g]
    assert {"CIRCLE"} in fm and {"SECTOR", "ARC"} in fm


def test_plain_polygon_unchanged():
    g = M.parse_coordinate_groups(PLAIN)
    assert [len(x) for x in g] == [4]
    assert not M.needs_shape_regroup(PLAIN)


def _check(text):
    groups = M.parse_coordinate_groups(text)
    item = {"military": True, "raw_text": text, "groups": groups, "validity": {"start": "x", "end": "y"}, "url": "u"}
    gj = M.build_geojson({"u": {**item, "title": "t", "date": "2026-10-13"}})
    return C.check_item(item, gj["features"])


def test_check_clean_after_fix():
    for t in (LIAO_423, JI_573, JI_617, HU_561, PLAIN):
        assert _check(t) == [], (t[:20], _check(t))


def test_check_flags_old_point():
    item = {"military": True, "raw_text": LIAO_423, "validity": {"start": "x"}, "url": "u",
            "groups": [[{"lat": 38.869, "lon": 121.029, "fmt": "NOTAM", "raw": "x"}]]}
    feats = [{"properties": {"group_index": 1}, "geometry": {"type": "Point", "coordinates": [121.029, 38.869]}}]
    assert any("描けていない" in s for s in C.check_item(item, feats))


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
    print(f"ok {n}")
