#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_paste_import.py
======================

NMS-API(FAAへの申請が要る／再配布条件も未確認)を経由せず、FAAの公開NOTAM検索
(NOTAM Search / Archive)から**個別にコピー貼り付けしたICAO形式の生テキスト**を、
notam_cn_collect.py の --fixture がそのまま読める JSON(Feature配列)に変換する。

HANDOFF_NOTAM.md §4 で扱った実物2件と同じ書式（Q)行・A)行・B)/C)行・E)本文）を想定:

    A4703/26 NOTAMN Q)ZXXX/QRTCA/IV/BO/W/000/999/2536N11651E039 A)ZGZU ZSHA
    B)2609172330 C)2609180330
    E) A TEMPORARY RESTRICTED AREA ESTABLISHED BOUNDED BY: N230636E1162812-...
    F)GND G)UNL

貼り付けたテキストの中に複数件のNOTAMが混ざっていても、"<記号+4桁>/<年2桁> NOTAM[NRC]"
の出現位置で自動的に区切る。1件も見つからなければ何も書き出さずエラー終了する。

使い方:
  # 1) テキストファイルから変換（貼り付けたものをそのままファイルに保存しておく）
  python notam_paste_import.py pasted.txt -o pasted_fixture.json

  # 2) 標準入力から（貼り付けて Ctrl-D / Ctrl-Z）
  python notam_paste_import.py -o pasted_fixture.json

  # 3) 変換してそのまま notam_cn_collect.py --fixture に渡すところまで一気に
  python notam_paste_import.py pasted.txt --collect-out-dir ./notam_out_local

変換後のfixtureは通常どおり使える:
  python notam_cn_collect.py --fixture pasted_fixture.json --out-dir ./notam_out_local

注意:
  - 貼り付けテキストには「発行日時(issued)」が含まれないことが多いため、issued は
    有効開始(B)の値で代用する（date表示が多少ずれる可能性がある、実運用上の影響は無い）。
  - accountId(発行元locationseries)は貼り付けテキストからは分からないため None のまま。
  - id(内部ID)は実APIには存在しない値なので "PASTE-<番号>-<年>" を合成する。同じNOTAM
    番号を貼り付け直しても同じidになるため、再取り込み時の冪等性(idempotency)は保たれる。
  - notam_cn_collect.py と同じフォルダに置いて実行すること(このモジュールをimportして
    target_area()等の判定ロジックをそのまま再利用しているため、ロジックが2重管理になる
    のを避けている)。
"""

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

try:
    import notam_cn_collect as C
except ImportError:
    sys.exit("notam_cn_collect.py が同じフォルダに見つかりません。同じフォルダで実行してください。")

# 1件のNOTAMの開始位置: 例 "A4703/26 NOTAMN" / "G4294/26 NOTAMC" 。番号は英字+数字+/年2桁。
_NOTAM_START = re.compile(r"(?=\b[A-Z]\d{3,4}/\d{2}\s+NOTAM[NRC]\b)")
_NUMBER_TYPE = re.compile(r"^(\S+/\d{2})\s+NOTAM([NRC])(?:[ \t]+([A-Z]\d{3,4}/\d{2})(?=\s|$))?")
_QLINE = re.compile(
    r"Q\)\s*([A-Z]{4})/(Q[A-Z]{4})/([A-Z]*)/([A-Z]*)/([A-Z]*)/(\d{3})/(\d{3})/"
    r"(?:(\d{4}[NS]\d{5}[EW])(\d{2,3}))?"
)
_ALINE = re.compile(r"A\)\s*(.*?)\s*B\)", re.S)
_BLINE = re.compile(r"B\)\s*(\d{10})")
_CLINE = re.compile(r"C\)\s*(PERM|\d{10})\s*(EST)?")
_ELINE = re.compile(r"E\)(.*?)(?:F\)|$)", re.S)


def _yymmddhhmm_to_iso(token):
    """'2609172330' -> ISO8601(UTC)。西暦は 20YY 固定（近未来運用のみを想定）。"""
    yy, mo, dd, hh, mi = int(token[0:2]), int(token[2:4]), int(token[4:6]), int(token[6:8]), int(token[8:10])
    try:
        d = dt.datetime(2000 + yy, mo, dd, hh, mi, tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return C.iso(d)


def parse_one(block, seq):
    """1件ぶんの生テキストを notam_cn_collect.make_record() が食べられる Feature に変換する。
    必須要素(番号・Q行・A行・B行)が欠けていれば (None, 理由) を返す。"""
    block = block.strip()
    m = _NUMBER_TYPE.match(block)
    if not m:
        return None, "先頭が '<番号>/<年> NOTAM[NRC]' の形式で始まっていません"
    number, ntype, ref = m[1], m[2], m[3]
    year2 = number.split("/")[-1]
    year4 = f"20{year2}" if year2.isdigit() else None

    qm = _QLINE.search(re.sub(r"\s+", "", block[block.find("Q)"):block.find("A)")] if "A)" in block else block))
    if not qm:
        return None, "Q)行が見つからない、または想定外の書式です"
    fir, qcode, traffic, purpose, scope, lower, upper, qcoord, qradius = qm.groups()

    am = _ALINE.search(block)
    if not am:
        return None, "A)行(対象空港/FIR)が見つかりません"
    icao_location = re.sub(r"\s+", " ", am[1]).strip()

    bm = _BLINE.search(block)
    if not bm:
        return None, "B)行(有効開始時刻)が見つかりません"
    eff_start = _yymmddhhmm_to_iso(bm[1])

    cm = _CLINE.search(block)
    eff_end, estimated = None, False
    if cm and cm[1] != "PERM":
        eff_end = _yymmddhhmm_to_iso(cm[1])
        estimated = bool(cm[2])

    em = _ELINE.search(block)
    e_text = re.sub(r"\s+", " ", em[1]).strip() if em else ""

    nid = f"PASTE-{number.replace('/', '-')}"
    # 貼り付け元によっては末尾の座標/半径がQ項に無いことがある(実データA0799/26で確認、
    # 区間閉鎖NOTAM等でFIR全体を指すため点座標を省略する書式)。その場合はNone/空欄のまま
    # 扱い、E)本文のポリゴンを geometry の主ソースとするbuild_geometry()のロジックに委ねる。
    icao_full = f"{fir}/{qcode}/{traffic}/{purpose}/{scope}/{lower}/{upper}/" + \
        (f"{qcoord}{qradius}" if qcoord else "")
    formatted = f"{number} NOTAM{ntype}" + (f" {ref}" if ref else "") + \
        f" Q){icao_full} A){icao_location} B){bm[1]} C)" + (cm[1] if cm else "?") + \
        (" EST" if estimated else "") + f" E) {e_text}"

    feature = {
        "type": "Feature",
        # 貼り付けテキストには図形(GeoJSON)が付いてこないため、API側の面情報は無いものとして
        # 扱う(空のGeometryCollection)。build_geometry() が E)本文のポリゴン→Q項の円→点の順で
        # フォールバックするので、これで既存ロジックがそのまま働く。
        "geometry": {"type": "GeometryCollection", "geometries": []},
        "properties": {"coreNOTAMData": {
            "notam": {
                "id": nid, "series": number[0], "number": number, "year": year4, "type": ntype,
                "issued": eff_start,  # 貼り付けテキストに発行日時は無いため有効開始で代用(注意書き参照)
                "icaoLocation": icao_location, "affectedFir": fir,
                "classification": None, "accountId": None,
                "selectionCode": qcode, "traffic": traffic, "purpose": purpose, "scope": scope,
                "lowerLimit": lower, "upperLimit": upper,
                "effectiveStart": eff_start, "effectiveEnd": eff_end,
                "estimated": "true" if estimated else "false",
                "coordinates": qcoord, "radius": qradius,
                "text": e_text, "lastUpdated": eff_start,
            },
            "notamTranslation": [{"type": "ICAO", "formattedText": formatted}],
        }},
    }
    return feature, None


def parse_text(raw_text):
    """貼り付けテキスト全体を分割し、Feature配列と、変換できなかったブロックの理由一覧を返す。"""
    blocks = [b for b in _NOTAM_START.split(raw_text) if b.strip()]
    features, problems = [], []
    for i, b in enumerate(blocks, 1):
        feat, err = parse_one(b, i)
        if err:
            problems.append((i, err, b.strip().splitlines()[0][:60] if b.strip() else ""))
        else:
            features.append(feat)
    return features, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="貼り付けテキストを保存したファイル。省略時は標準入力から読む")
    ap.add_argument("-o", "--out", default="pasted_fixture.json", help="出力するfixture JSONのパス（既定 pasted_fixture.json）")
    ap.add_argument("--collect-out-dir", help="変換後、そのまま notam_cn_collect.py --fixture <out> --out-dir <ここ> まで実行する")
    ap.add_argument("--now", help="notam_cn_collect.py に渡す --now（テスト用の時刻上書き）")
    a = ap.parse_args()

    raw_text = Path(a.input).read_text(encoding="utf-8") if a.input else sys.stdin.read()
    features, problems = parse_text(raw_text)

    if not features and not problems:
        sys.exit("入力からNOTAMらしきブロックが1件も見つかりませんでした（'<番号>/<年> NOTAMN' 等で始まる形式を想定）")

    for i, err, head in problems:
        print(f"[skip] ブロック{i}: {err}（先頭: {head!r}）", file=sys.stderr)

    Path(a.out).write_text(json.dumps(features, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(features)}件を変換 / {len(problems)}件をスキップ -> {a.out}")
    for f in features:
        n = f["properties"]["coreNOTAMData"]["notam"]
        area = C.target_area(n) or "対象外(CN/HK/MO/TW/REF以外)"
        print(f"  {n['number']:>10}  {n['icaoLocation']:<16} Q){n['selectionCode']}  -> {area}")

    if a.collect_out_dir:
        import subprocess
        cmd = [sys.executable, "notam_cn_collect.py", "--fixture", a.out, "--out-dir", a.collect_out_dir]
        if a.now:
            cmd += ["--now", a.now]
        print(f"\n$ {' '.join(cmd)}")
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)


if __name__ == "__main__":
    main()
