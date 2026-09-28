#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_backfill_text_polygon.py
================================

2026-09-27、notam_cn_collect.py の E項座標パーサ(`_TEXT_COORD`/`parse_text_polygons`)に
2つの修正を行った(HANDOFF_NOTAM.md参照):

  (a) 「数字が先(DDMMSS[NS]DDDMMSS[EW])」書式への対応を追加(取りこぼし修正・格上げ方向)
  (b) 'CG1(N253957E1095707)' のような、迂回路上の臨時ウェイポイント定義の座標を、
      境界ポリゴンの頂点として誤検出しないよう除外(誤検出修正・格下げ方向)

これらの修正は**今後新規に取り込む/更新されるNOTAMにしか自動では効かない**。
`geometry`/`geometry_source`は取り込み時(notam_cn_collect.make_record())に一度だけ計算されて
state_active.json にキャッシュされる設計のため、修正前に取り込み済みのレコードは、本文が
更新されるか失効してArchiveに移るまで古い(誤った)ジオメトリのまま残ってしまう。

2026-09-27時点で確認済みの対象:
  格上げ(円・点 → 多角形): K2356/26, K2392/26, A4705/26, 09/332
  格下げ(誤った多角形 → 円・点・またはNone): A4957/26, A4958/26

2026-09-28追加: 航空路の区間閉鎖(SEGMENT A-B OF ATS RTE R CLSD 等)を、AIP索引
(環境変数 AIPDS_INDEX、HANDOFF_NOTAM.md §19)で線(geometry_source "route-segment")にする。
多角形が無く区間閉鎖が読めるものは線に格上げする。索引が無い環境では既存の線に触れない。
  AIPDS_INDEX=/path/to/aip_index.json.gz python notam_backfill_text_polygon.py --apply

本スクリプトは、既存の `notam_out/state_active.json` と `notam_out/archive/*.geojson` を
対象に、修正後の `parse_text_polygons()` で再判定し、結果が変わるものだけ上書きする
**一回限りの後方互換バックフィル**。

安全設計:
  - `geometry_source` が "api"(APIが返した本物の面情報)のレコードには**一切触れない**。
    これはE項テキストのパースとは無関係にAPIが直接返した面情報のため、パーサの修正の
    影響を受けない。
  - "text-polygon" のレコードは**再検証する**(格下げの可能性があるため)。修正後のパーサで
    もう多角形が拾えなければ、Q項の円・点にフォールバック(`state_active.json`側は
    `coordinates`/`radius_nm`が残っているため再計算できる)。Q項の座標も無ければ
    `geometry`をNoneに落とす(=ビューアのfeaturesから除外される。元々APIが返した点情報は
    キャッシュされていないため復元不可能だが、これは「境界も円も無い純粋な手続き系NOTAM」
    である可能性が高く、安全側の挙動)。
  - "qline-circle"/"qline-point"/"api-point"/None のレコードは、修正後に多角形が新たに
    拾えたときだけ text-polygon に格上げする(従来の挙動のまま)。
  - Archiveのレコードは `raw_text` に Q)〜G)行を含む生ICAO全文が入っていることがあるため、
    必ず `E)`〜`F)`(無ければ末尾まで)の区間だけを抽出してから座標パーサに渡す
    (state_active側と同じ e_section() のロジック)。また `coordinates`(Q項の生座標)は
    保存されていないため、Archive側で格下げが必要な場合は常に None に落とす
    (Q項の円・点への再フォールバックはできない。実害は「地図に出なくなるだけ」)。
  - `state_active.json` を書き換えた後は `notam_cn.geojson` を必ず再構築すること
    (本スクリプトが自動で行う)。

使い方(リポジトリのルートで、notam_cn_collect.py と同じフォルダから実行):
  python3 notam_backfill_text_polygon.py --out-dir notam_out --apply
  # --apply を付けなければ dry-run(変更点の一覧表示のみ、ファイルは書き換えない)

実行後:
  1. `notam_out/notam_cn.geojson` を state_active.json から再構築(このスクリプトが自動で行う)
  2. 通常のcommit手順(`notam_commit_state.py`)でコミット・push
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

try:
    import notam_cn_collect as C
except ImportError:
    sys.exit("notam_cn_collect.py が同じフォルダに見つかりません。同じフォルダで実行してください。")


def _make_polygon_geom(rings):
    return {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 \
        else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}


def reconcile_state_active_record(rec, e_text_source):
    """state_active方式のdict1件を修正後のロジックで再検証し、必要なら書き換える。
    戻り値は変更したら True。"api" は触らない。"""
    if rec.get("geometry_source") == "api":
        return False

    e_text = C.e_section(e_text_source, e_text_source)
    rings = C.parse_text_polygons(e_text)

    if rings:
        geom = _make_polygon_geom(rings)
        if rec.get("geometry_source") == "text-polygon" and rec.get("geometry") == geom:
            return False  # 既に同じ多角形。変化なし
        rec["geometry"], rec["geometry_source"] = geom, "text-polygon"
        qc = C.parse_qline_coord(rec.get("coordinates"))
        if qc:
            ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
            rec["qline_offset_nm"] = round(C.nm_between(C.ring_centroid(ring), qc), 1)
        else:
            rec["qline_offset_nm"] = None
        return True

    # 航空路の区間閉鎖の線(route-segment、AIP索引=環境変数 AIPDS_INDEX があるときだけ作れる)
    line = C.notam_route_segments.route_closure_geometry(e_text)
    if line:
        if rec.get("geometry_source") == "route-segment" and rec.get("geometry") == line:
            return False
        rec["geometry"], rec["geometry_source"], rec["qline_offset_nm"] = line, "route-segment", None
        return True
    if rec.get("geometry_source") == "route-segment" and not C.notam_route_segments.load_index():
        return False  # 索引が無い環境では線を作り直せないだけなので、既存の線を残す

    if rec.get("geometry_source") not in ("text-polygon", "route-segment"):
        return False  # 元々多角形以外(円・点・None)で、新たな多角形も無い → 触らない

    # ここに来るのは「旧パーサでは多角形と誤認していたが、修正後は多角形が無い」ケース
    # (例: 臨時ウェイポイント定義の誤検出)。Q項の円・点にフォールバックする
    # (build_geometry()と同じ優先順位: 円 > 点)。
    qc = C.parse_qline_coord(rec.get("coordinates"))
    try:
        radius = float(rec["radius_nm"]) if rec.get("radius_nm") not in (None, "") else None
    except (TypeError, ValueError):
        radius = None
    if qc and radius is not None and 0 < radius <= C.CIRCLE_MAX_NM:
        rec["geometry"] = {"type": "Polygon", "coordinates": [C.circle_ring(qc[0], qc[1], radius)]}
        rec["geometry_source"] = "qline-circle"
    elif qc:
        rec["geometry"] = {"type": "Point", "coordinates": [round(qc[1], 6), round(qc[0], 6)]}
        rec["geometry_source"] = "qline-point"
    else:
        # APIが返した元の点情報はキャッシュされていないため復元不可能。安全側で「図形無し」
        # に倒す(次回のnotam_cn.geojson再構築でfeaturesから除外されるだけで、実害は無い)。
        rec["geometry"] = None
        rec["geometry_source"] = None
    rec["qline_offset_nm"] = None
    return True


def reconcile_archive_feature(feat):
    """archive/notam_YYYY-MM.geojson の1 featureを再検証。戻り値は変更したら True。"""
    p = feat.get("properties") or {}
    if p.get("geometry_source") == "api":
        return False

    raw_text = p.get("raw_text")
    e_text = C.e_section(raw_text, raw_text)
    rings = C.parse_text_polygons(e_text)

    if rings:
        geom = _make_polygon_geom(rings)
        if p.get("geometry_source") == "text-polygon" and feat.get("geometry") == geom:
            return False
        feat["geometry"] = geom
        p["geometry_source"] = "text-polygon"
        # Archive側にはQ項の生座標が残っていないため qline_offset_nm は再計算できない(参考値なので省略)。
        return True

    line = C.notam_route_segments.route_closure_geometry(e_text)
    if line:
        if p.get("geometry_source") == "route-segment" and feat.get("geometry") == line:
            return False
        feat["geometry"], p["geometry_source"] = line, "route-segment"
        return True
    if p.get("geometry_source") == "route-segment" and not C.notam_route_segments.load_index():
        return False

    if p.get("geometry_source") not in ("text-polygon", "route-segment"):
        return False

    # Archive側は円・点へのフォールバックに必要なQ項生座標を保持していないため、
    # 安全側で「図形無し」に倒す(ビューアのArchive一覧には残るが地図描画対象から外れる)。
    feat["geometry"] = None
    p["geometry_source"] = None
    return True


def backfill_state_active(path, apply_changes):
    active = C.read_json(path, {})
    changed_numbers = []
    for rid, rec in active.items():
        e_text_source = rec.get("icao_text") or rec.get("text")
        if reconcile_state_active_record(rec, e_text_source):
            changed_numbers.append(f"{rec.get('number')}({rec.get('geometry_source')})")
    if changed_numbers and apply_changes:
        C.write_if_changed(path, active, indent=1)
    return active, changed_numbers


def backfill_archive_file(path, apply_changes):
    fc = C.read_json(path, {"type": "FeatureCollection", "features": []})
    changed_numbers = []
    for feat in fc.get("features", []):
        if reconcile_archive_feature(feat):
            p = feat.get("properties") or {}
            changed_numbers.append(f"{p.get('number')}({p.get('geometry_source')})")
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
        print(f"  -> {len(changed)}件を更新{'(適用済み)' if a.apply else '(dry-run、--applyで反映)'}: "
              + ", ".join(changed))
    else:
        print("  -> 対象なし(修正が必要なレコードは見つかりませんでした)")

    if changed and a.apply:
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
                print(f"  {p.name}: {len(changed)}件を更新"
                      f"{'(適用済み)' if a.apply else '(dry-run)'}: " + ", ".join(changed))
        if not any_changed:
            print("  -> 対象なし")
    else:
        print("  -> archiveディレクトリが見つかりません(スキップ)")

    if not a.apply:
        print("\ndry-runで実行しました。反映するには --apply を付けて再実行してください。")


if __name__ == "__main__":
    sys.exit(main())
