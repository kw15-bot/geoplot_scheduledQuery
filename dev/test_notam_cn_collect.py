#!/usr/bin/env python3
"""
notam_cn_collect.py のテスト。データは仕様書(nms-api.yaml 1.0.18)のスキーマに沿った【合成データ】で、
実際のNMS-APIの応答ではない。実データでの確認は notam_cn_probe.py の出力で行うこと。

実行:  python dev/test_notam_cn_collect.py      （リポジトリのルートから。標準ライブラリのみ）
"""
import base64
import gzip
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notam_cn_collect as C  # noqa: E402


def feat(num, icao, fir, q="QRDCA", coords="3900N11600E", radius="30", start="2026-09-21T00:00:00.000Z",
         end="2026-09-23T00:00:00.000Z", text="AREA ACT", ntype="N", ref=None, geom="default",
         updated="2026-09-21T01:00:00.000Z", nid=None):
    """合成Feature。geom: 'default'=点のみ / 'poly'=面あり / None=図形なし"""
    icao_text = f"{num} NOTAM{ntype}" + (f" {ref}" if ref else "") + f"\nQ) {fir}/{q}/IV/BO/W/000/999/{coords}{radius.zfill(3)}\nE) {text}"
    g = {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [116.0, 39.0]}]}
    if geom == "poly":
        g["geometries"].append({"type": "Polygon", "coordinates": [[[110, 30], [111, 30], [111, 31], [110, 31], [110, 30]]]})
    if geom is None:
        g = None
    return {"type": "Feature", "geometry": g, "properties": {"coreNOTAMData": {
        "notam": {"id": "NMS_ID_" + (nid or num.replace("/", "").replace("A", "9")), "series": "A", "number": num,
                  "year": "2026", "type": ntype, "issued": start, "affectedFir": fir, "selectionCode": q,
                  "location": icao[-3:], "icaoLocation": icao, "classification": "INTERNATIONAL",
                  "accountId": "ZBBBYNYX", "effectiveStart": start, "effectiveEnd": end, "estimated": "false",
                  "text": text, "coordinates": coords, "radius": radius, "lowerLimit": "SFC", "upperLimit": "FL200",
                  "lastUpdated": updated},
        "notamTranslation": [{"type": "LOCAL_FORMAT", "simpleText": "!" + num}, {"type": "ICAO", "formattedText": icao_text}]}}}


BASE = [
    feat("A0101/26", "ZBPE", "ZBPE"),                                        # CN FIR、円(30NM)
    feat("A0102/26", "ZSPD", "ZSHA", q="QWMLW", geom="poly"),                # CN、面あり、警告
    feat("A0103/26", "VHHH", "VHHK", q="QXXXX"),                             # HK
    feat("A0104/26", "VMMC", "VHHK"),                                        # MO
    feat("A0105/26", "RCTP", "RCAA", coords="2500N12130E", radius="999"),    # TW、半径999→点
    feat("A0106/26", "KZBW", "ZBW"),                                         # 米ARTCC（除外）
    feat("A0107/26", "ZMUB", "ZMUB"),                                        # モンゴル（除外）
    feat("A0108/26", "RJTG", "RJJJ"),                                        # 福岡FIR（周辺国FIRとして参考対象、REF）
    feat("A0109/26", "ZGZU", "ZGZU", geom=None, coords="", radius=""),       # 図形なし（座標も無い）
]


def run(out, *args, env=None):
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        return C.main(["--out-dir", str(out), *args])
    finally:
        os.environ.clear()
        os.environ.update(old)


def load(out, name="notam_cn.geojson"):
    return json.loads((Path(out) / name).read_text(encoding="utf-8"))


def props(out):
    return {f["properties"]["number"]: f["properties"] for f in load(out)["features"]}


def arch(out):
    """Archive全月分を number -> properties で返す"""
    res = {}
    for p in sorted((Path(out) / "archive").glob("notam_*.geojson")):
        for f in json.loads(p.read_text(encoding="utf-8"))["features"]:
            res[f["properties"]["number"]] = f["properties"]
    return res


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "o"
        self.fx = Path(self.tmp.name) / "fx.json"

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, feats, kind="list"):
        obj = feats if kind == "list" else {"status": "Success", "data": {"geojson": feats}}
        self.fx.write_text(json.dumps(obj), encoding="utf-8")

    def go(self, now="2026-09-21T12:00:00Z", *extra):
        return run(self.out, "--fixture", str(self.fx), "--now", now, *extra)

    # --- 対象の絞り込み
    def test_target_filter_and_regions(self):
        self.put(BASE)
        self.assertEqual(self.go(), 0)
        p = props(self.out)
        self.assertEqual(set(p), {"A0101/26", "A0102/26", "A0103/26", "A0104/26", "A0105/26", "A0108/26"})   # 0109は図形なし
        self.assertEqual({v["area_group"] for v in p.values()}, {"CN", "HK", "MO", "TW", "REF"})
        state = load(self.out, "state_active.json")
        self.assertEqual(len(state), 7)                                   # 図形なしもstateには保持
        self.assertFalse(any(r["icao_location"] in ("KZBW", "ZMUB") for r in state.values()))

    def test_ref_fir_exact_match_not_prefix(self):
        """周辺国の参考FIR(RPHI/KZAK/RJJJ/RKRR/ZKKP)は4文字完全一致でのみ対象になり、
        同じ2文字を持つ無関係な地点(例: K始まりの米国内, RJ始まりの日本国内の他空港)は
        対象外のままであることを確認する（AREA_BY_PREFIXへの巻き込み事故の回帰防止）。"""
        ref_hits = [
            feat("A0201/26", "RPMM", "RPHI"),   # フィリピン・マニラFIR -> REF
            feat("A0202/26", "PAOA", "KZAK"),   # オークランド洋上管制 -> REF
            feat("A0203/26", "RJFF", "RJJJ"),   # 福岡FIR -> REF
            feat("A0204/26", "RKSI", "RKRR"),   # 仁川FIR -> REF
            feat("A0205/26", "ZKPY", "ZKKP"),   # 平壌FIR -> REF
        ]
        ref_miss = [
            feat("A0301/26", "KJFK", "KZNY"),   # 米国内だが指定FIR(KZAK)ではない -> 対象外
            feat("A0302/26", "RJTT", "RJTG"),   # 日本国内だが指定FIR(RJJJ)ではない -> 対象外
        ]
        self.put(ref_hits + ref_miss)
        self.assertEqual(self.go(), 0)
        p = props(self.out)
        self.assertEqual(set(p), {f["properties"]["coreNOTAMData"]["notam"]["number"] for f in ref_hits})
        self.assertEqual({v["area_group"] for v in p.values()}, {"REF"})

    def test_nms_response_envelope_accepted(self):
        self.put(BASE, kind="envelope")
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(props(self.out)), 6)

    # --- 図形
    def test_geometry_rules(self):
        self.put(BASE)
        self.go()
        p = props(self.out)
        g = {f["properties"]["number"]: f for f in load(self.out)["features"]}
        self.assertEqual(p["A0101/26"]["geometry_source"], "qline-circle")          # 点のみ→Q項の円
        self.assertEqual(g["A0101/26"]["geometry"]["type"], "Polygon")
        ring = g["A0101/26"]["geometry"]["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])
        self.assertEqual(len(ring), 65)
        lat_span = (max(y for _, y in ring) - min(y for _, y in ring)) * 111.32 / 1.852
        self.assertAlmostEqual(lat_span, 60, delta=0.5)                              # 半径30NM→直径60NM
        self.assertEqual(p["A0102/26"]["geometry_source"], "api")                    # 面があれば優先
        self.assertEqual(p["A0105/26"]["geometry_source"], "qline-point")            # 半径999は円にしない
        self.assertEqual(g["A0105/26"]["geometry"]["type"], "Point")

    def test_zero_zero_point_rejected(self):
        # 2026-09-24: 実データ(--bootstrap il)で、複数FIRにまたがるトリガーNOTAM等、APIが
        # Point[0,0](座標未設定のプレースホルダー)を返すケースを確認。実座標として採用してはいけない。
        f = feat("A0110/26", "ZBPE", "ZBPE", coords="", radius="", geom=None)
        f["geometry"] = {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [0, 0]}]}
        self.put(BASE + [f])
        self.go()
        p = props(self.out)
        self.assertNotIn("A0110/26", p)                                             # geojsonには出さない
        st = load(self.out, "state_active.json")
        rec = next(v for v in st.values() if v["number"] == "A0110/26")
        self.assertIsNone(rec["geometry_source"])                                   # state には no_geometry として残す
        self.assertIsNone(rec["geometry"])

    def test_qline_coord_parsing(self):
        self.assertEqual(C.parse_qline_coord("3900N11600E"), (39.0, 116.0))
        self.assertEqual(C.parse_qline_coord("2230S04302W"), (-(22 + 30 / 60), -(43 + 2 / 60)))
        self.assertIsNone(C.parse_qline_coord("garbage"))
        self.assertIsNone(C.parse_qline_coord("9900N11600E"))

    # --- 冪等性・更新
    def test_idempotent(self):
        self.put(BASE)
        self.go()
        before = {p.name: p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.go("2026-09-21T12:10:00Z")
        after = {p.name: p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        # meta.json は「変化なし＆ハートビート未満」なので fixture では更新されない
        self.assertEqual(before, after)

    def test_update_keeps_first_seen(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        first = load(self.out, "state_active.json")["9010126"]["first_seen"]
        upd = [feat("A0101/26", "ZBPE", "ZBPE", text="AREA CHANGED", updated="2026-09-21T13:00:00.000Z")]
        self.put(upd)
        self.go("2026-09-21T13:05:00Z")
        rec = load(self.out, "state_active.json")["9010126"]
        self.assertEqual(rec["text"], "AREA CHANGED")
        self.assertEqual(rec["first_seen"], first)

    # --- 置換・取消
    def test_replace_ends_old_notam(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        r = feat("A0110/26", "ZBPE", "ZBPE", ntype="R", ref="A0101/26", start="2026-09-21T15:00:00.000Z",
                 end="2026-09-25T00:00:00.000Z", text="AREA REPLACED")
        self.put([r])
        self.go("2026-09-21T16:00:00Z")
        p = props(self.out)
        self.assertNotIn("A0101/26", p)                                     # 置換された旧NOTAMは即Archive
        old = arch(self.out)["A0101/26"]
        self.assertEqual(old["status"], "cancelled")
        self.assertEqual(old["valid_end"], "2026-09-21T15:00:00+00:00")
        self.assertEqual(old["ended_by"], "A0110/26")
        self.assertEqual(p["A0110/26"]["status"], "active")

    def test_cancel_notice_not_drawn_but_kept(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        c = feat("A0111/26", "ZBPE", "ZBPE", ntype="C", ref="A0101/26", start="2026-09-21T15:00:00.000Z", geom=None,
                 coords="", radius="")
        self.put([c])
        self.go("2026-09-21T16:00:00Z")
        self.assertNotIn("A0111/26", props(self.out))                       # 取消通知自体は描画しない
        self.assertIn("9011126", load(self.out, "state_active.json"))       # だが記録は残す（順序入れ替わり対策の猶予中）
        self.assertEqual(arch(self.out)["A0101/26"]["status"], "cancelled")
        self.go("2026-09-24T00:00:00Z")                                     # 猶予(2日)経過後は取消通知もArchiveへ
        self.assertNotIn("9011126", load(self.out, "state_active.json"))
        self.assertIn("A0111/26", arch(self.out))

    def test_cancel_arriving_before_target_still_cancels_it(self):
        c = feat("A0111/26", "ZBPE", "ZBPE", ntype="C", ref="A0101/26", start="2026-09-21T15:00:00.000Z", geom=None,
                 coords="", radius="")
        self.put([c])
        self.go("2026-09-21T16:00:00Z")                                     # 取消通知が先に届く
        self.put([feat("A0101/26", "ZBPE", "ZBPE")])                        # 対象NOTAMが後から届く
        self.go("2026-09-21T16:10:00Z")
        self.assertNotIn("A0101/26", props(self.out))
        self.assertEqual(arch(self.out)["A0101/26"]["status"], "cancelled")

    # --- 失効とArchive（即時移行）
    def test_expiry_moves_to_archive_immediately(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        self.assertEqual(props(self.out)["A0101/26"]["status"], "active")
        self.assertFalse((self.out / "archive").exists())
        self.go("2026-09-23T00:00:01Z")                                     # 終了直後
        self.assertNotIn("A0101/26", props(self.out))                       # 地図用geojsonからは消え
        self.assertEqual(arch(self.out)["A0101/26"]["status"], "expired")   # Archiveに入る
        self.assertNotIn("9010126", load(self.out, "state_active.json"))
        self.assertTrue((self.out / "archive" / "notam_2026-09.geojson").exists())

    def test_archive_month_is_expiry_month(self):
        self.put([feat("A0130/26", "ZBPE", "ZBPE", start="2026-09-29T00:00:00.000Z", end="2026-10-02T00:00:00.000Z")])
        self.go("2026-09-30T00:00:00Z")
        self.assertFalse((self.out / "archive").exists())
        self.go("2026-10-03T00:00:00Z")
        self.assertTrue((self.out / "archive" / "notam_2026-10.geojson").exists())    # 失効した月
        self.assertFalse((self.out / "archive" / "notam_2026-09.geojson").exists())

    def test_no_churn_when_archived_items_reappear_in_delta(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        self.go("2026-09-24T00:00:00Z")                                     # 全部失効→Archive
        first = {p.name: p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        n = len(arch(self.out))
        self.go("2026-09-24T00:10:00Z")                                     # 重ね取りで同じものが再登場
        self.go("2026-09-24T00:20:00Z")
        second = {p.name: p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(first, second)                                     # ファイルは一切変わらない（無駄コミットなし）
        self.assertEqual(len(arch(self.out)), n)                            # 重複もしない

    def test_archive_is_append_only_and_dedupes(self):
        self.put(BASE)
        self.go("2026-09-21T12:00:00Z")
        self.go("2026-09-24T00:00:00Z")
        before = set(arch(self.out))
        self.put([feat("A0199/26", "ZBPE", "ZBPE", start="2026-10-01T00:00:00.000Z", end="2026-10-09T00:00:00.000Z")])
        self.go("2026-10-05T00:00:00Z")                                     # 別の新規が入っても既存Archiveは残る
        self.assertTrue(before <= set(arch(self.out)))
        self.assertIn("A0199/26", props(self.out))

    def test_upcoming_and_perm(self):
        self.put([feat("A0120/26", "ZBPE", "ZBPE", start="2026-09-30T00:00:00.000Z"),
                  feat("A0121/26", "ZBPE", "ZBPE", end=None)])
        self.go("2026-09-21T12:00:00Z")
        p = props(self.out)
        self.assertEqual(p["A0120/26"]["status"], "upcoming")
        self.assertEqual(p["A0121/26"]["status"], "active")                 # 終了なし(PERM等)は有効のまま
        self.go("2027-01-01T00:00:00Z")
        self.assertEqual(props(self.out)["A0121/26"]["status"], "active")   # 自動ではArchiveしない

    def test_q_category(self):
        self.put(BASE)
        self.go()
        p = props(self.out)
        self.assertEqual(p["A0101/26"]["category"], "restriction")
        self.assertEqual(p["A0102/26"]["category"], "warning")
        self.assertEqual(p["A0103/26"]["category"], "other")

    def test_q_focus_tag(self):
        # 2026-09-24 staging実データの主要Qコードでの確認（§8-9・HANDOFF §「目的別分類」参照）
        self.assertEqual(C.q_focus_tag("QRDCA"), "restriction")   # 危険区域設定
        self.assertEqual(C.q_focus_tag("QRTCA"), "restriction")   # 臨時制限区域
        self.assertEqual(C.q_focus_tag("QRRLP"), "restriction")   # 制限区域(禁止)
        self.assertEqual(C.q_focus_tag("QWMLW"), "flag")          # 射撃/砲撃(実データでは花火が多数混入=要確認のまま)
        self.assertEqual(C.q_focus_tag("QWELW"), "flag")          # 演習
        self.assertEqual(C.q_focus_tag("QWFXX"), "flag")          # 空中給油
        self.assertEqual(C.q_focus_tag("QWULW"), "watch")         # 無人機(実データは民間ドローンが大半)
        self.assertEqual(C.q_focus_tag("QWLLW"), "watch")         # 自由気球
        self.assertEqual(C.q_focus_tag("QWCXX"), "watch")         # 係留気球/凧
        self.assertEqual(C.q_focus_tag("QWZLW"), "leisure")       # 模型飛行
        self.assertEqual(C.q_focus_tag("QWALW"), "leisure")       # 航空ショー
        self.assertEqual(C.q_focus_tag("QWYXX"), "leisure")       # 航空測量
        self.assertEqual(C.q_focus_tag("QXXXX"), "plaintext")     # 未分類・平文
        self.assertEqual(C.q_focus_tag("QMXLC"), "admin")         # 誘導路閉鎖(空港運用)
        self.assertEqual(C.q_focus_tag("QNMAS"), "admin")         # VOR/DME使用不能
        self.assertEqual(C.q_focus_tag(None), "admin")
        self.assertEqual(C.q_focus_tag(""), "admin")

    def test_keyword_hits(self):
        # 2026-09-24 ユーザー提供の語句リスト。大文字小文字を区別しない部分一致。
        self.assertEqual(C.keyword_hits("A TEMPORARY RESTRICTED AREA ESTABLISHED"), ["TEMPORARY"])
        self.assertEqual(C.keyword_hits("aircraft are forbidden to fly into the area"), ["FORBIDDEN"])
        self.assertEqual(C.keyword_hits("REF AIP CHINA, THE RESTRICTION AREA ACTIVE"), [])
        self.assertEqual(C.keyword_hits(None), [])
        self.assertEqual(C.keyword_hits(""), [])
        # 複数ヒット時はKEYWORD_LISTの順で返る
        self.assertEqual(
            C.keyword_hits("A TEMPORARY DANGER AREA ESTABLISHED, AIRCRAFT FORBIDDEN"),
            ["DANGER", "TEMPORARY", "FORBIDDEN"],
        )
        # CLSD/CLOSEDは空港運用の事務連絡にもヒットする(既知の仕様。focus_tagと併用が前提)
        self.assertEqual(C.keyword_hits("TWY F CLSD BTN TWY Z2 AND TWY M3"), ["CLSD"])

    def test_keyword_hit_in_feature_properties(self):
        self.put(BASE)
        self.go()
        p = props(self.out)
        # BASEフィクスチャのA0102/26は本文に語句が含まれない想定 -> keyword_hit=False
        self.assertIn("keyword_hit", p["A0101/26"])
        self.assertIn("matched_keywords", p["A0101/26"])
        self.assertIsInstance(p["A0101/26"]["matched_keywords"], list)

    def test_classification_in_feature_properties(self):
        # ビューアが米国国内書式(DOM)を除外するのに使う
        self.put(BASE)
        self.go()
        self.assertEqual(props(self.out)["A0101/26"]["classification"], "INTERNATIONAL")



# ----------------------------------------------------------------------------- 実NOTAM（ユーザー提供の本文。APIの包み方は仕様書からの推定）
REAL_4703 = ("A4703/26 NOTAMN Q)ZXXX/QRTCA/IV/BO/W/000/999/2536N11651E039 A)ZGZU ZSHA B)2609172330 C)2609180330 "
             "E) A TEMPORARY RESTRICTED AREA ESTABLISHED BOUNDED BY: N230636E1162812-N234042E1174425-"
             "N233012E1174957-N225820E1163232- N230636E1162812. HEIGHT: 35,000M AND BELOW. F)GND G)UNL")
REAL_4705 = ("A4705/26 NOTAMN Q)ZSHA/QRTCA/IV/BO/W/000/999/2343N11742E017 A)ZSHA B)2609172330 C)2609180330 "
             "E) A TEMPORARY RESTRICTED AREA ESTABLISHED BOUNDED BY: N240013E1174036-N235732E1175242-"
             "N232609E1174457-N232853E1173231- N240013E1174036 . HEIGHT: 50,000M AND BELOW. F)GND G)UNL")


def real_feat(raw, icao, fir, coords, radius, nid):
    """実NOTAM本文から合成Featureを作る。図形はQ項中心の点のみ（APIが面を返さない最悪ケースを想定）。"""
    import re as _re
    num = _re.match(r"(\S+)", raw)[1]
    e = _re.search(r"E\)(.*?)F\)", raw, _re.S)[1].strip()
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"coreNOTAMData": {
        "notam": {"id": "NMS_ID_" + nid, "series": "A", "number": num, "year": "2026", "type": "N",
                  "issued": "2026-09-17T14:00:00.000Z", "affectedFir": fir, "selectionCode": "QRTCA",
                  "icaoLocation": icao, "classification": "INTERNATIONAL", "accountId": "ZBBBYNYX",
                  "effectiveStart": "2026-09-17T23:30:00.000Z", "effectiveEnd": "2026-09-18T03:30:00.000Z",
                  "text": e, "coordinates": coords, "radius": radius, "lastUpdated": "2026-09-17T14:00:00.000Z"},
        "notamTranslation": [{"type": "ICAO", "formattedText": raw}]}}}


class RealNotams(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "o"
        self.fx = Path(self.tmp.name) / "fx.json"

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, feats, now="2026-09-18T01:00:00Z"):
        self.fx.write_text(json.dumps(feats), encoding="utf-8")
        return run(self.out, "--fixture", str(self.fx), "--now", now)

    def feats(self, loc4703="ZGZU ZSHA"):
        return [real_feat(REAL_4703, loc4703, "ZXXX", "2536N11651E", "039", "1001"),
                real_feat(REAL_4705, "ZSHA", "ZSHA", "2343N11742E", "017", "1002")]

    def test_multi_location_and_zxxx_are_targeted(self):
        for loc, fir in (("ZGZU ZSHA", "ZXXX"), ("ZGZU", "ZXXX"), ("KJFK", "ZXXX"), ("ZXXX", "ZXXX")):
            with self.subTest(loc=loc, fir=fir):
                self.assertEqual(C.target_area({"icaoLocation": loc, "affectedFir": fir}), "CN")
        self.assertIsNone(C.target_area({"icaoLocation": "ZBW", "affectedFir": "ZBW"}))     # 米ARTCCは除外のまま

    def test_polygon_from_e_field_is_used(self):
        self.assertEqual(self.go(self.feats()), 0)
        g = {f["properties"]["number"]: f for f in load(self.out)["features"]}
        self.assertEqual(set(g), {"A4703/26", "A4705/26"})
        f = g["A4703/26"]
        self.assertEqual(f["properties"]["geometry_source"], "text-polygon")
        ring = f["geometry"]["coordinates"][0]
        self.assertEqual(len(ring), 5)
        # N230636E1162812 = 23°06'36"N 116°28'12"E
        self.assertAlmostEqual(ring[0][1], 23 + 6 / 60 + 36 / 3600, places=5)
        self.assertAlmostEqual(ring[0][0], 116 + 28 / 60 + 12 / 3600, places=5)
        self.assertEqual(ring[0], ring[-1])

    def test_qline_offset_flags_bad_center(self):
        self.go(self.feats())
        p = props(self.out)
        self.assertGreater(p["A4703/26"]["qline_offset_nm"], 100)      # Q項の緯度が約2.3度ずれている（実測138NM）
        self.assertLess(p["A4705/26"]["qline_offset_nm"], 3)           # こちらは整合

    def test_times_and_category(self):
        self.go(self.feats(), now="2026-09-18T01:00:00Z")
        p = props(self.out)["A4703/26"]
        self.assertEqual(p["valid_start"], "2026-09-17T23:30:00+00:00")   # B) 2609172330
        self.assertEqual(p["valid_end"], "2026-09-18T03:30:00+00:00")     # C) 2609180330
        self.assertEqual((p["status"], p["category"], p["q_code"]), ("active", "restriction", "QRTCA"))
        self.go(self.feats(), now="2026-09-18T04:00:00Z")
        self.assertNotIn("A4703/26", props(self.out))
        self.assertEqual(arch(self.out)["A4703/26"]["status"], "expired")

    def test_q_line_coordinate_not_mistaken_for_text_polygon(self):
        # Q項の '2536N11651E039' は 先頭が数字なので E項パーサに拾われない
        rings = C.parse_text_polygons(C.e_section(REAL_4703, None))
        self.assertEqual(len(rings), 1)
        self.assertEqual(len(rings[0]), 5)

    def test_multiple_closed_areas_and_open_ring(self):
        two = "AREA1 N2300E11600-N2300E11700-N2400E11700-N2300E11600. AREA2 N3000E12000-N3000E12100-N3100E12100"
        rings = C.parse_text_polygons(two)
        self.assertEqual(len(rings), 2)
        self.assertTrue(all(r[0] == r[-1] for r in rings))
        self.assertEqual(C.parse_text_polygons("CENTRE N230636E1162812 RADIUS 5KM"), [])   # 1点だけは面にしない
        self.assertEqual(C.parse_text_polygons("N239999E1162812-N230636E1162812-N234042E1174425"), [])  # 分60超は無効

    def test_digit_first_coordinate_format_supported(self):
        # 2026-09-27追加: 「数字が先」の座標書式2種の取りこぼしを修正。
        # (a) K2356/26等の無人機エリア通知: DDMMSS[NS]DDDMMSS[EW]、ハイフン区切りで先頭点に明示的に戻る
        uas = ("AREA: BOUNDED BY FLW POINTS\n"
               "344747N1352608E - 344745N1352611E - 344744N1352610E - 344746N1352607E"
               "\n- 344747N1352608E \n(IKEDA-SHI IN OSAKA)")
        rings = C.parse_text_polygons(uas)
        self.assertEqual(len(rings), 1)
        self.assertEqual(len(rings[0]), 5)
        self.assertEqual(rings[0][0], rings[0][-1])
        # 344747N = 34°47'47"N
        self.assertAlmostEqual(rings[0][0][1], 34 + 47 / 60 + 47 / 3600, places=5)
        self.assertAlmostEqual(rings[0][0][0], 135 + 26 / 60 + 8 / 3600, places=5)

        # (b) A4705/26等のTO区切り(区切り語は無関係、明示的な先頭点の反復も無し。末尾で自動的に閉じる)
        to_sep = ("AN AREA DEFINED AS 331300N1222400W TO 325400N1233300W TO "
                  "344600N1244000W TO 351300N1233400W TO POINT OF ORIGIN")
        rings2 = C.parse_text_polygons(to_sep)
        self.assertEqual(len(rings2), 1)
        self.assertEqual(len(rings2[0]), 5)      # 4頂点+自動で閉じた終点
        self.assertEqual(rings2[0][0], rings2[0][-1])
        self.assertAlmostEqual(rings2[0][0][1], 33 + 13 / 60, places=5)
        self.assertAlmostEqual(rings2[0][0][0], -(122 + 24 / 60), places=5)

        # 従来の「記号が先」書式と混在しても両方拾えること
        mixed = "N2300E11600-2310N11610E-N2400E11700-N2300E11600"
        self.assertEqual(len(C.parse_text_polygons(mixed)[0]), 4)

    def test_named_waypoint_coords_in_parens_are_not_polygon_vertices(self):
        # 2026-09-27追加: A4957/26等の航空路変更NOTAMで、迂回路の臨時ウェイポイント定義
        # 'CG1(N253957E1095707)' のような座標が、実在しないエリア境界として誤検出されていたのを修正。
        reroute = ("FLIGHTS VIA ENKUS-R343-ABTUD SHALL ADJUST TO "
                   "ENKUS-CG1(N253957E1095707)-CG2(N274629E1112333)-ABTUD. "
                   "ALSO VIA MILOP-CG3 (N275120 E1105724)-SANJIANG.")
        self.assertEqual(C.parse_text_polygons(reroute), [])   # 座標はあるが多角形にはしない

        # 括弧の外に出てくる本物の境界座標は、括弧付きウェイポイントの直後にあっても拾う
        mixed = ("REROUTE VIA CG1(N253957E1095707) THEN AREA BOUNDED BY "
                 "N2300E11600-N2300E11700-N2400E11700-N2300E11600.")
        rings = C.parse_text_polygons(mixed)
        self.assertEqual(len(rings), 1)
        self.assertEqual(len(rings[0]), 4)


# ----------------------------------------------------------------------------- APIモード（同一プロセス内モック）
class MockState:
    feats = []
    il_payload = None
    fail_delta = 0
    seen = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj=None, raw=None):
        b = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        exp = "Basic " + base64.b64encode(b"ID:SECRET").decode()
        if self.path == "/v1/auth/token" and self.headers.get("Authorization") == exp:
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            return self.send(200, {"access_token": "TOK", "expires_in": "1799"})
        self.send(401, {"message": "Unauthorized"})

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self.headers.get("Authorization") != "Bearer TOK":
            return self.send(401, {"message": "Unauthorized"})
        MockState.seen.append((u.path, q))
        if u.path == "/nmsapi/v1/notams":
            if "lastUpdatedDate" in q:
                if MockState.fail_delta > 0:
                    MockState.fail_delta -= 1
                    return self.send(408, {"message": "Request Timeout"})
                return self.send(200, {"status": "Success", "data": {"geojson": MockState.feats}})
            if "location" in q:
                fs = [f for f in MockState.feats
                      if f["properties"]["coreNOTAMData"]["notam"]["icaoLocation"] == q["location"]]
                return self.send(200, {"status": "Success", "data": {"geojson": fs}})
            if "classification" in q:
                return self.send(307, {"status": "Success", "data": {"url": "/nmsapi/v1/content/abc"}})
        if u.path == "/nmsapi/v1/content/abc":
            return self.send(200, raw=MockState.il_payload)
        if u.path == "/nmsapi/v1/locationseries":
            return self.send(200, {"status": "Success", "data": {"locationSeries": [
                {"icaoId": "ZBPE"}, {"icaoId": "VHHH"}, {"icaoId": "KJFK"}]}})
        self.send(404, {})


class ApiMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "o"
        self.env = {"NMS_HOST": f"http://127.0.0.1:{self.port}", "NMS_CLIENT_ID": "ID", "NMS_CLIENT_SECRET": "SECRET"}
        MockState.feats, MockState.fail_delta, MockState.seen = list(BASE), 0, []
        C.time.sleep = lambda s: None                     # テスト高速化（バックオフ待ちを飛ばす）

    def tearDown(self):
        self.tmp.cleanup()

    def test_delta_window_uses_overlap_from_last_success(self):
        self.assertEqual(run(self.out, "--now", "2026-09-21T12:00:00Z", env=self.env), 0)   # 初回=直近6時間
        d1 = [q for p, q in MockState.seen if "lastUpdatedDate" in q][-1]["lastUpdatedDate"]
        self.assertEqual(d1, "2026-09-21T06:00:00Z")
        self.assertEqual(run(self.out, "--now", "2026-09-21T12:10:00Z", env=self.env), 0)
        d2 = [q for p, q in MockState.seen if "lastUpdatedDate" in q][-1]["lastUpdatedDate"]
        self.assertEqual(d2, "2026-09-21T11:50:00Z")                                          # 最終成功-10分

    def test_failure_returns_2_and_does_not_advance(self):
        run(self.out, "--now", "2026-09-21T12:00:00Z", env=self.env)
        meta = (self.out / "meta.json").read_text()
        MockState.fail_delta = 99
        self.assertEqual(run(self.out, "--now", "2026-09-21T13:00:00Z", env=self.env), 2)
        self.assertEqual((self.out / "meta.json").read_text(), meta)                          # last_success据え置き
        MockState.fail_delta = 1                                                              # 1回目408→再試行で成功
        self.assertEqual(run(self.out, "--now", "2026-09-21T13:00:00Z", env=self.env), 0)

    def test_bad_credentials_returns_2(self):
        bad = dict(self.env, NMS_CLIENT_SECRET="WRONG")
        self.assertEqual(run(self.out, env=bad), 2)
        self.assertFalse((self.out / "state_active.json").exists())

    def test_missing_credentials_returns_2(self):
        env = {"NMS_HOST": self.env["NMS_HOST"]}
        old = {k: os.environ.pop(k, None) for k in ("NMS_CLIENT_ID", "NMS_CLIENT_SECRET")}
        try:
            self.assertEqual(run(self.out, env=env), 2)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_gap_beyond_24h_warns_and_clamps(self):
        run(self.out, "--now", "2026-09-21T12:00:00Z", env=self.env)
        run(self.out, "--now", "2026-09-25T12:00:00Z", "--heartbeat-hours", "1", env=self.env)
        d = [q for p, q in MockState.seen if "lastUpdatedDate" in q][-1]["lastUpdatedDate"]
        self.assertEqual(d, "2026-09-24T12:30:00Z")                                           # now-23h30m

    def test_bootstrap_il_gz(self):
        MockState.il_payload = gzip.compress(json.dumps({"status": "Success", "data": {"geojson": BASE}}).encode())
        self.assertEqual(run(self.out, "--bootstrap", "il", "--now", "2026-09-21T12:00:00Z", env=self.env), 0)
        self.assertEqual(len(props(self.out)), 6)
        call = [q for p, q in MockState.seen if "classification" in q][0]
        self.assertEqual(call["classification"], "INTERNATIONAL")
        self.assertEqual(call["allowRedirect"], "false")

    def test_bootstrap_il_unparseable_fails_cleanly(self):
        MockState.il_payload = b"<AIXMBasicMessage/>"
        self.assertEqual(run(self.out, "--bootstrap", "il", env=self.env), 2)

    def test_bootstrap_locations(self):
        self.assertEqual(run(self.out, "--bootstrap", "locations", "--extra-locations", "ZSPD",
                             "--now", "2026-09-21T12:00:00Z", env=self.env), 0)
        asked = sorted(q["location"] for p, q in MockState.seen if "location" in q)
        self.assertEqual(asked, ["VHHH", "ZBPE", "ZSPD"])          # KJFKは対象外なので聞かない
        self.assertEqual(set(props(self.out)), {"A0101/26", "A0102/26", "A0103/26"})

    def test_dry_run_writes_nothing(self):
        self.assertEqual(run(self.out, "--dry-run", "--now", "2026-09-21T12:00:00Z", env=self.env), 0)
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_no_secret_leak_in_outputs(self):
        run(self.out, "--now", "2026-09-21T12:00:00Z", env=self.env)
        blob = "".join(p.read_text() for p in self.out.rglob("*") if p.is_file())
        for s in ("SECRET", "TOK", "Bearer"):
            self.assertNotIn(s, blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
