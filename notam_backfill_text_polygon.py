#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_backfill_text_polygon.py
================================

2026-09-27、notam_cn_collect.py の E項座標パーサ(`_TEXT_COORD`/`parse_text_polygons`)に
「数字が先(DDMMSS[NS]DDDMMSS[EW])」書式への対応を追加した(HANDOFF_NOTAM.md参照)。

この修正は**今後新規に取り込む/更新されるNOTAMにしか自動では効かない**。
`geometry`/`geometry_source`は取り込み時(notam_cn_collect.make_record())に一度だけ計算されて
state_active.json にキャッシュされる設計のため、修正前に取り込み済みのレコード
(2026-09-27時点で確認済み: K2356/26・K2392/26・A4705/26・09/332)は、本文が更新されるか
Archiveに移るまで古い(円・点)ジオメトリのまま残ってしまう。

本スクリプトは、既存の `notam_out/state_active.json` と `notam_out/archive/*.geojson` を
対象に、修正後の `parse_text_polygons()` で多角形が拾えるかどうかを再判定し、拾えた場合だけ
`geometry`/`geometry_source`(・可能なら`qline_offset_nm`)を上書きする**一回限りの後方互換
バックフィル**。

安全設計:
  - 既に `geometry_source` が "api"(APIが返した本物の面情報)または "text-polygon"
    (旧パーサで既に拾えていた多角形)のレコードは**一切触らない**(格下げしない)。
  - Archiveのレコードは `raw_text` に Q)〜G)行を含む生ICAO全文が入っていることがあるため、
    Q項の座標(数字が先の書式)を誤って拾わないよう、必ず `E)`〜`F)`(無ければ末尾まで)の
    区間だけを抽出してから座標パーサに渡す(state_active側と同じ e_section() のロジック)。
  - `state_active.json` を書き換えた後は `notam_cn.geojson` を必ず再構築すること(下記手順)。
    `meta.json` の `features`/`by_status` はビューアには使われない集計値なので、次回の
    `notam_cn_collect.py` 実行時に自然に更新される(本スクリプトでは更新しない)。

使い方(リポジトリのルートで、notam_cn_collect.py と同じフォルダから実行):
  python3 notam_backfill_text_polygon.py --out-dir notam_out --apply
  # --apply を付けなければ dry-run(変更点の一覧表示のみ、ファイルは書き換えない)

実行後:
  1. `notam_out/notam_cn.geojson` を state_active.json から再構築(このスクリプトが自動で行う)
  2. 通常のcommit手順(`notam_commit_state.py`)でコミット・push
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import notam_cn_collect as C
except ImportError:
    sys.exit("notam_cn_collect.py が同じフォルダに見つかりません。同じフォルダで実行してください。")


def recompute_geometry_if_missed(rec, e_text_source):
    """rec(state_active方式のdict)を書き換える。戻り値は変更したら True。
    e_text_source: E)〜F)を含みうる生テキスト(icao_text優先、無ければtext)。"""
    if rec.get("geometry_source") in ("api", "text-polygon"):
        return False   # 既に本物の面情報を持っているものは触らない(格下げ防止)

    e_text = C.e_section(e_text_source, e_text_source)
    rings = C.parse_text_polygons(e_text)
    if not rings:
        return False

    geom = {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 \
        else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}

    rec["geometry"] = geom
    rec["geometry_source"] = "text-polygon"

    qc = C.parse_qline_coord(rec.get("coordinates"))
    if qc:
        ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        rec["qline_offset_nm"] = round(C.nm_between(C.ring_centroid(ring), qc), 1)
    else:
        rec["qline_offset_nm"] = None
    return True


def backfill_state_active(path, apply_changes):
    active = C.read_json(path, {})
    changed_numbers = []
    for rid, rec in active.items():
        e_text_source = rec.get("icao_text") or rec.get("text")
        if recompute_geometry_if_missed(rec, e_text_source):
            changed_numbers.append(rec.get("number"))
    if changed_numbers and apply_changes:
        C.write_if_changed(path, active, indent=1)
    return active, changed_numbers


def backfill_archive_file(path, apply_changes):
    fc = C.read_json(path, {"type": "FeatureCollection", "features": []})
    changed_numbers = []
    for feat in fc.get("features", []):
        p = feat.get("properties") or {}
        if p.get("geometry_source") in ("api", "text-polygon"):
            continue
        raw_text = p.get("raw_text")
        e_text = C.e_section(raw_text, raw_text)
        rings = C.parse_text_polygons(e_text)
        if not rings:
            continue
        geom = {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 \
            else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        feat["geometry"] = geom
        p["geometry_source"] = "text-polygon"
        # Archive済みfeatureにはQ項の生座標(coordinates)が残っていないため qline_offset_nm は
        # 再計算できない(参考値なので省略。既存値があれば古いまま残るが実害は無い)。
        changed_numbers.append(p.get("number"))
    if changed_numbers and apply_changes:
        fc["features"] = sorted(fc["features"], key=C.sort_key)
        C.write_if_changed(path, fc, indent=None)
    return fc, changed_numbers


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./notam_out", help="notam_cn_collect.py と同じ --out-dir")
    ap.add_argument("--apply", action="store_true", help="指定しなければdry-run(表示のみ)")
    a = ap.parse_args()
    out = Path(a.out_dir)

    print(f"[1/2] {out / 'state_active.json'} を確認中...")
    active, changed = backfill_state_active(out / "state_active.json", a.apply)
    if changed:
        print(f"  -> {len(changed)}件を text-polygon に更新{'(適用済み)' if a.apply else '(dry-run、--applyで反映)'}: "
              + ", ".join(changed))
    else:
        print("  -> 対象なし(修正が必要なレコードは見つかりませんでした)")

    if changed and a.apply:
        # notam_cn.geojson は state_active から作り直す(notam_cn_collect.py本体と同じ規則:
        # type=="C"(取消通知)と 図形無し を除外)。
        now = None  # status計算に「今」が要るが、ここではstatusを変更しないため未使用でよい値でも可
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc)
        features = sorted(
            (C.to_feature(r, now) for r in active.values() if r.get("type") != "C" and r.get("geometry")),
            key=C.sort_key,
        )
        C.write_if_changed(out / "notam_cn.geojson", {"type": "FeatureCollection", "features": features})
        print(f"  -> notam_cn.geojson を再構築しました({len(features)}件)")

    archive_dir = out / "archive"
    print(f"[2/2] {archive_dir} 配下の月別ファイルを確認中...")
    if archive_dir.is_dir():
        any_changed = False
        for p in sorted(archive_dir.glob("notam_*.geojson")):
            fc, changed = backfill_archive_file(p, a.apply)
            if changed:
                any_changed = True
                print(f"  {p.name}: {len(changed)}件を text-polygon に更新"
                      f"{'(適用済み)' if a.apply else '(dry-run)'}: " + ", ".join(changed))
        if not any_changed:
            print("  -> 対象なし")
    else:
        print("  -> archiveディレクトリが見つかりません(スキップ)")

    if not a.apply:
        print("\ndry-runで実行しました。反映するには --apply を付けて再実行してください。")


if __name__ == "__main__":
    sys.exit(main())
