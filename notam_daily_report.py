#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_daily_report.py
=====================

notam_out/ の収集データ(state_active.json と archive/notam_YYYY-MM.geojson)を読み、
Qコードの主題(2〜3文字目、ICAO Doc 8126 の定義)ごとにまとめた日次レポートを Markdown で書き出す。
.github/workflows/notam-report.yml から1日1回実行する。

出力:
  notam_out/reports/notam_report_YYYY-MM-DD.md   (日付はUTC)
  notam_out/reports/latest.md                    (最新版のコピー)

集計の考え方:
  - 同じNOTAMが地名指標ごとに別レコードで届く(A)ZGZU ZHWH 等)ので、番号+開始時刻で1件にまとめる
    (ビューアの notamDedupe() と同じ考え方)。
  - 「地図に表示」は、ビューアと同じ条件(E項の座標から多角形ができた text-polygon、または
    航空路の区間閉鎖を線にした route-segment、かつDOM以外)。
  - 「過去24時間の新規」は first_seen(手動取り込み分は除く)、「過去24時間の失効・取消」は Archive の valid_end
    (取消は last_updated)が集計時刻から24時間以内のもの。
  - 種別の名前はQコードの一般的な定義(ICAO Doc 8126)。軍事かどうかの判定はしない。

使い方:
  python notam_daily_report.py --out-dir notam_out
  python notam_daily_report.py --out-dir notam_out --now 2026-09-28T00:00:00Z   # 時刻の上書き(テスト用)
"""

import argparse
import collections
import datetime as dt
import glob
import json
import sys
from pathlib import Path

try:
    import notam_cn_collect as C
except ImportError:
    sys.exit("notam_cn_collect.py が同じフォルダに見つかりません。同じフォルダで実行してください。")

# Qコード1文字目(大分類)。2文字目がこの文字。
Q_GROUP = {
    "A": "空域の構成", "C": "通信・監視", "F": "飛行場の施設", "G": "GNSS", "I": "ILS・MLS",
    "L": "灯火", "M": "移動区域(滑走路・誘導路等)", "N": "航法施設", "O": "その他",
    "P": "航空交通の方式", "R": "空域の制限", "S": "航空交通業務", "W": "警報",
    "X": "その他(平文)",
}
# Qコードの主題(2〜3文字目)。ICAO Doc 8126 の定義を和訳したもの。無いものは記号のまま表示する。
Q_SUBJECT = {
    "AC": "管制圏(CTR)", "AE": "管制区", "AF": "飛行情報区(FIR)", "AN": "RNAV経路", "AR": "ATS経路(航空路)",
    "AT": "ターミナル管制区(TMA)", "AU": "高高度FIR", "AZ": "飛行場交通区域",
    "CA": "対空通信施設", "CE": "航空路監視レーダー", "CS": "二次監視レーダー(SSR)", "CT": "ターミナル監視レーダー",
    "FA": "飛行場", "FF": "消防救難", "FM": "気象業務", "FT": "視程計", "FU": "燃料",
    "GA": "GNSS(飛行場単位)", "GW": "GNSS(広域)",
    "IC": "ILS", "ID": "ILS併設DME", "IG": "グライドパス", "IL": "ローカライザ",
    "LA": "進入灯", "LB": "飛行場灯台", "LC": "滑走路中心線灯", "LE": "滑走路灯", "LF": "連鎖式閃光灯",
    "LH": "高光度滑走路灯", "LI": "滑走路末端識別灯", "LP": "PAPI", "LR": "着陸区域の灯火全般",
    "LT": "滑走路末端灯", "LX": "誘導路中心線灯", "LY": "誘導路灯",
    "MA": "移動区域", "MD": "公示距離", "MK": "駐機場", "MN": "エプロン", "MP": "駐機スポット",
    "MR": "滑走路", "MS": "過走帯", "MT": "滑走路末端", "MX": "誘導路",
    "NA": "無線航法施設全般", "NB": "NDB", "ND": "DME", "NM": "VOR/DME", "NT": "VORTAC", "NV": "VOR",
    "OA": "航空情報業務", "OB": "障害物", "OE": "航空機入場要件", "OL": "障害物灯",
    "PA": "標準到着経路(STAR)", "PD": "標準出発経路(SID)", "PF": "交通流制御の方式", "PI": "計器進入方式",
    "PM": "飛行場運用最低気象条件", "PO": "障害物間隔高度",
    "RA": "空域の留保", "RD": "危険区域", "RM": "軍用運用区域", "RO": "上空通過(特定地域)",
    "RP": "飛行禁止区域", "RR": "飛行制限区域", "RT": "臨時制限区域",
    "SA": "ATIS", "SC": "区域管制所(ACC)", "SE": "飛行情報センター", "SO": "洋上管制", "SP": "進入管制業務",
    "ST": "飛行場管制所", "SV": "VOLMET",
    "WA": "航空ショー", "WB": "曲技飛行", "WC": "係留気球", "WD": "爆発物処理", "WE": "演習",
    "WF": "空中給油", "WG": "グライダー飛行", "WH": "発破", "WJ": "バナー曳航", "WL": "気球の放球",
    "WM": "ミサイル・銃砲・ロケット射撃", "WP": "落下傘降下", "WR": "放射性物質・有毒化学物質",
    "WS": "ガスの燃焼・放出", "WT": "航空機の集団移動", "WU": "無人航空機", "WV": "編隊飛行",
    "WW": "顕著な火山活動", "WY": "航空写真・測量", "WZ": "模型飛行",
    "XX": "平文(該当コードなし)",
}
AREA_LABEL = {"CN": "中国本土", "HK": "香港", "MO": "マカオ", "TW": "台湾", "REF": "周辺国FIR(参考)"}
STATUS_LABEL = {"active": "有効", "upcoming": "有効前", "expired": "失効", "cancelled": "取消", "cancel-notice": "取消通知"}


def subject_of(q):
    s = (q or "")[1:3].upper()
    return s if len(s) == 2 else "??"


def subject_label(s):
    if s == "??":
        return "Qコードなし(SNOWTAM等)"
    return Q_SUBJECT.get(s, "(定義表に無いコード)")


def group_label(s):
    return Q_GROUP.get(s[:1], "不明")


def parse_iso(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_utc(v):
    d = parse_iso(v)
    return d.strftime("%Y-%m-%d %H:%M") if d else "?"


def e_head(text, n=90):
    t = " ".join((text or "").split())
    return (t[:n] + "…") if len(t) > n else t


def firs_of(fir, locations):
    """ビューアの notamFirTokens() と同じ: Q項のFIR。複数FIRのNOTAM(ZXXX等)と無い場合はA項。"""
    firs = (fir or "").split()
    if firs and not any(len(v) == 4 and v.endswith("XX") for v in firs):
        return firs
    return sorted(set(locations)) or ["?"]


def load_items(out, now):
    """state(有効・有効前など)と archive を、1NOTAM=1件にまとめたdictのリストで返す。"""
    items = {}

    def add(key, it, loc):
        if key in items:
            if loc and loc not in items[key]["locations"]:
                items[key]["locations"].append(loc)
            return
        it["locations"] = [loc] if loc else []
        items[key] = it

    active = C.read_json(out / "state_active.json", {})
    for rec in active.values():
        f = C.to_feature(rec, now)
        p = f["properties"]
        raw = rec.get("icao_text") or rec.get("text")
        add(("S", p.get("issuer"), p.get("number"), p.get("valid_start")), {
            "src": "active", "number": p.get("number") or "?", "fir": p.get("fir"), "q_code": rec.get("q_code"),
            "status": p.get("status"), "notam_type": rec.get("type"), "classification": rec.get("classification"),
            "area_group": rec.get("area_group"), "valid_start": p.get("valid_start"), "valid_end": p.get("valid_end"),
            "first_seen": rec.get("first_seen"), "last_updated": rec.get("last_updated"),
            "geometry_source": rec.get("geometry_source"), "has_geometry": bool(rec.get("geometry")),
            "e_text": C.e_section(rec.get("icao_text"), rec.get("text")),
            "keywords": C.keyword_hits(raw),
        }, rec.get("icao_location"))

    for fn in sorted(glob.glob(str(out / "archive" / "notam_*.geojson"))):
        for f in C.read_json(Path(fn), {"features": []}).get("features", []):
            p = f.get("properties") or {}
            raw = p.get("raw_text")
            add(("A", p.get("issuer"), p.get("number"), p.get("valid_start")), {
                "src": "archive", "number": p.get("number") or "?", "fir": p.get("fir"), "q_code": p.get("q_code"),
                "status": p.get("status"), "notam_type": p.get("notam_type"), "classification": p.get("classification"),
                "area_group": p.get("area_group"), "valid_start": p.get("valid_start"), "valid_end": p.get("valid_end"),
                "first_seen": p.get("first_seen"), "last_updated": p.get("last_updated"),
                "geometry_source": p.get("geometry_source"), "has_geometry": bool(f.get("geometry")),
                "e_text": C.e_section(raw, raw),
                "keywords": C.keyword_hits(raw),
                "manual": str(p.get("nms_id") or "").startswith("PASTE-"),
            }, p.get("icao_location"))
    for it in items.values():
        it["firs"] = firs_of(it["fir"], it["locations"])
        it["subject"] = subject_of(it["q_code"])
        it["on_map"] = (it["geometry_source"] in ("text-polygon", "route-segment") and it["has_geometry"]
                        and it["classification"] != "DOM" and it["notam_type"] != "C")
    return list(items.values())


def off_map_reason(it):
    """ビューアの表示条件(notamPassesFilter)で落ちた理由。地図に出るものは None。"""
    if it["on_map"]:
        return None
    if it["notam_type"] == "C":
        return "取消通知(NOTAMC)"
    if it["classification"] == "DOM":
        return "米国国内書式(DOM)"
    gs = it["geometry_source"]
    if not it["has_geometry"]:
        return "位置情報なし(本文にもQ項にも座標なし)"
    if C.notam_route_segments.find_closures(it.get("e_text")):
        return "航空路の区間閉鎖だが線にできなかった(AIP索引が無い、または区間を航空路上で特定できない)"
    if gs in ("qline-circle", "qline-point"):
        return "Q項の中心座標・半径から作った円/点のみ(E項に多角形の座標なし)"
    if gs == "api-point":
        return "APIが返した点のみ(E項に多角形の座標なし)"
    return f"E項から多角形が作れない({gs or '図形の出どころ不明'})"


def in_window(v, start, end):
    d = parse_iso(v)
    return bool(d and start < d <= end)


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


def notam_rows(items):
    rows = []
    for it in sorted(items, key=lambda x: (x["subject"], x["valid_start"] or "", x["number"])):
        rows.append([
            it["number"], " ".join(it["firs"]), f'{it["q_code"] or "?"}（{subject_label(it["subject"])}）',
            f'{fmt_utc(it["valid_start"])} 〜 {fmt_utc(it["valid_end"])}',
            "○" if it["on_map"] else "", e_head(it["e_text"]),
        ])
    return rows


def build_report(items, now, meta):
    start = now - dt.timedelta(hours=24)
    current = [i for i in items if i["src"] == "active" and i["status"] in ("active", "upcoming")]
    # 手動取り込み分(PASTE-)の first_seen は取り込んだ時刻なので、新規には数えない
    new = [i for i in items if in_window(i["first_seen"], start, now) and i["notam_type"] != "C" and not i.get("manual")]
    ended = [i for i in items if i["src"] == "archive" and (
        in_window(i["valid_end"], start, now) or (i["status"] == "cancelled" and in_window(i["last_updated"], start, now)))]

    L = []
    L.append(f"# NOTAM 日次レポート {now:%Y-%m-%d}（UTC）")
    L.append("")
    L.append(f"- 集計時刻: {now:%Y-%m-%d %H:%M} UTC（対象期間: {start:%Y-%m-%d %H:%M} 〜 {now:%Y-%m-%d %H:%M} UTC）")
    L.append(f"- 最終収集成功: {fmt_utc(meta.get('last_success'))} UTC")
    L.append("- 種別は Qコードの主題（2〜3文字目、ICAO Doc 8126 の定義）。軍事かどうかの判定はしていない。")
    L.append("- 同じ NOTAM が地名指標ごとに別レコードで届くものは1件にまとめて数えた。「地図」はビューアで多角形として表示されるもの。")
    L.append("")
    L.append("## 1. 概要")
    L.append("")
    by_status = collections.Counter(i["status"] for i in current)
    L.append(md_table(["区分", "件数"], [
        ["有効（Active）", by_status.get("active", 0)],
        ["有効前（Upcoming）", by_status.get("upcoming", 0)],
        ["　うち地図に表示", sum(1 for i in current if i["on_map"])],
        ["　うち地図に表示されない（理由は §7）", sum(1 for i in current if not i["on_map"])],
        ["過去24時間の新規", len(new)],
        ["過去24時間の失効・取消", len(ended)],
        ["Archive 累計", sum(1 for i in items if i["src"] == "archive")],
    ]))
    L.append("")

    L.append("## 2. 種別ごとの件数（有効・有効前）")
    L.append("")
    by_subj = collections.defaultdict(list)
    for i in current:
        by_subj[i["subject"]].append(i)
    rows = []
    for s, lst in sorted(by_subj.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rows.append([
            f"{s} {subject_label(s)}", group_label(s), len(lst),
            sum(1 for i in lst if i["status"] == "active"), sum(1 for i in lst if i["status"] == "upcoming"),
            sum(1 for i in lst if i in new), sum(1 for i in lst if i["on_map"]),
            "、".join(sorted({f for i in lst for f in i["firs"]})),
        ])
    L.append(md_table(["種別（Qコード主題）", "大分類", "計", "有効", "有効前", "24h新規", "地図", "FIR"], rows) if rows else "（該当なし）")
    L.append("")

    L.append("## 3. 地域・FIRごとの件数（有効・有効前）")
    L.append("")
    by_fir = collections.Counter()
    area_of = {}
    for i in current:
        for f in i["firs"]:
            by_fir[f] += 1
            area_of.setdefault(f, AREA_LABEL.get(i["area_group"], i["area_group"] or "?"))
    L.append(md_table(["FIR", "地域", "件数"], [[f, area_of[f], n] for f, n in sorted(by_fir.items(), key=lambda kv: (-kv[1], kv[0]))])
             if by_fir else "（該当なし）")
    L.append("")

    L.append("## 4. 過去24時間の新規")
    L.append("")
    L.append(md_table(["番号", "FIR", "Qコード（種別）", "有効期間 (UTC)", "地図", "E項（冒頭）"], notam_rows(new)) if new else "（なし）")
    L.append("")

    L.append("## 5. 過去24時間の失効・取消")
    L.append("")
    L.append(md_table(["番号", "FIR", "Qコード（種別）", "有効期間 (UTC)", "地図", "E項（冒頭）"], notam_rows(ended)) if ended else "（なし）")
    L.append("")

    L.append("## 6. 地図に表示されている NOTAM（有効・有効前）")
    L.append("")
    shown = [i for i in current if i["on_map"]]
    L.append(md_table(["番号", "FIR", "Qコード（種別）", "有効期間 (UTC)", "地図", "E項（冒頭）"], notam_rows(shown)) if shown else "（なし）")
    L.append("")

    L.append("## 7. 地図に表示されなかった NOTAM（有効・有効前）")
    L.append("")
    L.append("ビューアの表示条件（E項の座標から多角形が作れること、DOM以外）で落ちたもの。収集はしているので、本文は人間が確認する前提。")
    L.append("")
    hidden = [i for i in current if not i["on_map"]]
    reasons = collections.Counter(off_map_reason(i) for i in hidden)
    L.append(md_table(["理由", "件数"], [[r, n] for r, n in reasons.most_common()]) if hidden else "（なし）")
    L.append("")
    if hidden:
        rows = []
        for it in sorted(hidden, key=lambda x: (off_map_reason(x), x["subject"], x["valid_start"] or "", x["number"])):
            rows.append([it["number"], " ".join(it["firs"]), f'{it["q_code"] or "?"}（{subject_label(it["subject"])}）',
                         f'{fmt_utc(it["valid_start"])} 〜 {fmt_utc(it["valid_end"])}', off_map_reason(it), e_head(it["e_text"])])
        L.append(md_table(["番号", "FIR", "Qコード（種別）", "有効期間 (UTC)", "表示されない理由", "E項（冒頭）"], rows))
        L.append("")

    L.append("## 8. 参考: 語句一致（TEMPORARY・SPECIAL）")
    L.append("")
    L.append("ビューアの語句フィルタは未適用。将来採用する予定の2語（HANDOFF_NOTAM.md §16）に当たる有効・有効前の NOTAM。")
    L.append("")
    kw = [i for i in current if {"TEMPORARY", "SPECIAL"} & set(i["keywords"])]
    L.append(md_table(["番号", "FIR", "Qコード（種別）", "有効期間 (UTC)", "地図", "E項（冒頭）"], notam_rows(kw)) if kw else "（なし）")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./notam_out")
    ap.add_argument("--now", help="集計時刻の上書き(ISO8601、テスト用)")
    a = ap.parse_args()
    out = Path(a.out_dir)
    now = parse_iso(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    items = load_items(out, now)
    report = build_report(items, now, C.read_json(out / "meta.json", {}))
    rep_dir = out / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    path = rep_dir / f"notam_report_{now:%Y-%m-%d}.md"
    path.write_text(report, encoding="utf-8")
    (rep_dir / "latest.md").write_text(report, encoding="utf-8")
    print(f"wrote {path} ({len(items)} NOTAMs)")


if __name__ == "__main__":
    sys.exit(main())
