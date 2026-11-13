#!/usr/bin/env python3
"""
notam_xls_import.py
===================
FAA NOTAM Search の Archive Search(過去検索)の書き出し(Excel、fnsNotams_*.xls)を、NOTAM の過去の記録
(notam_out/archive/notam_YYYY-MM.geojson)に取り込む(2026-11-13、HANDOFF_NOTAM.md §33)。

NMS-API は今有効な NOTAM しか返さず、FAA NOTAM Search はクラウドからの自動アクセスを拒むので、
2026-01〜09 の北京発行分はユーザーが手で Archive Search して書き出したファイルを取り込む。

  1) 変換(手元。xlrd が要る: pip install xlrd)
       python notam_xls_import.py convert fnsNotams_*.xls -o fixture.json
     書き出しの「NOTAM Condition or LTA Subject」列に ICAO の全文が入っているので、notam_paste_import の
     読み取りでレコードにする。発行日時(Issue Date)・分類(Class)は表の列を使う。同じ NOTAM は1件にまとめる。
  2) 取り込み(ワークフロー notam-import.yml。data ブランチの notam_out に対して)
       python notam_xls_import.py merge fixture.json --out-dir notam_out
     収集本体(notam_cn_collect.py)を一時フォルダで fixture 取り込みとして走らせ、失効・取消になったものを
     Archive の月ファイルに足す。既にある NOTAM(番号+開始時刻が同じ)は足さない。今も有効なものは足さない
     (いつもの収集が持っている)。取り込んだものには properties.import_source = "faa-archive" を付ける。
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import tempfile
from pathlib import Path

import notam_cn_collect as C
import notam_paste_import as P

CLASS_MAP = {"international": "INTL", "domestic": "DOM", "military": "MIL"}


def _xls_time(s):
    """'01/03/2026 1200' → ISO(UTC)。"""
    m = re.match(r"\s*(\d{2})/(\d{2})/(\d{4})\s+(\d{2})(\d{2})", str(s or ""))
    if not m:
        return None
    mo, d, y, hh, mm = map(int, m.groups())
    return C.iso(dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc))


def convert(paths):
    import xlrd   # 手元で変換するときだけ要る
    feats, seen, problems = [], set(), []
    for path in paths:
        sh = xlrd.open_workbook(path).sheet_by_index(0)
        header = None
        for r in range(sh.nrows):
            row = [str(sh.cell_value(r, c)) for c in range(sh.ncols)]
            if row and row[0] == "Location" and "NOTAM #" in row:
                header = {name: i for i, name in enumerate(row)}
                continue
            if not header or not row[header["NOTAM #"]].strip():
                continue
            text = html.unescape(row[header["NOTAM Condition or LTA Subject"]]).replace("\r", "")
            feat, err = P.parse_one(text, r)
            if err:
                problems.append(f"{Path(path).name} 行{r + 1} {row[header['NOTAM #']]}: {err}")
                continue
            n = feat["properties"]["coreNOTAMData"]["notam"]
            key = (n["number"], n["effectiveStart"])
            if key in seen:
                continue
            seen.add(key)
            issued = _xls_time(row[header.get("Issue Date (UTC)", -1)] if "Issue Date (UTC)" in header else "")
            if issued:
                n["issued"] = n["lastUpdated"] = issued
            cls = row[header["Class"]].strip().lower() if "Class" in header else ""
            n["classification"] = CLASS_MAP.get(cls, cls.upper() or None)
            n["id"] = "FAAARC-" + n["number"].replace("/", "-") + "-" + (n["effectiveStart"] or "")[:10]
            n["importSource"] = "faa-archive"
            feats.append(feat)
    return feats, problems


def _key(p):
    return (p.get("number"), (p.get("valid_start") or "")[:16])


def merge(fixture, out_dir, now=None):
    """fixture は平文の .json か、data_crypt で暗号化した .json.enc(リポジトリに置くのはこちら)。"""
    if str(fixture).endswith(".enc"):
        import data_crypt
        plain = Path(tempfile.mkdtemp()) / Path(fixture).name[:-4]
        plain.write_bytes(data_crypt.decrypt_bytes(Path(fixture).read_bytes(), data_crypt.load_key()))
        fixture = plain
    out = Path(out_dir)
    now = now or C.iso(dt.datetime.now(dt.timezone.utc))
    existing = set()
    for path in (out / "archive").glob("notam_*.geojson"):
        for f in (C.read_json(path, {}) or {}).get("features") or []:
            existing.add(_key(f.get("properties") or {}))
    for rec in (C.read_json(out / "state_active.json", {}) or {}).values():
        existing.add(_key(rec))
    with tempfile.TemporaryDirectory() as tmp:
        rc = C.main(["--fixture", str(fixture), "--out-dir", tmp, "--now", now])
        if rc not in (0, None):
            raise SystemExit(f"収集本体の取り込みに失敗(終了コード {rc})")
        added, skipped = 0, 0
        for tpath in sorted((Path(tmp) / "archive").glob("notam_*.geojson")):
            new = []
            for f in (C.read_json(tpath, {}) or {}).get("features") or []:
                p = f.get("properties") or {}
                if _key(p) in existing:
                    skipped += 1
                    continue
                p["import_source"] = "faa-archive"
                p["first_seen"] = p.get("date") or p.get("valid_start") or p.get("first_seen")
                existing.add(_key(p))
                new.append(f)
            if not new:
                continue
            dest = out / "archive" / tpath.name
            fc = C.read_json(dest, None) or {"type": "FeatureCollection", "features": []}
            fc["features"] = (fc.get("features") or []) + new
            dest.parent.mkdir(parents=True, exist_ok=True)
            C.write_if_changed(dest, fc)
            added += len(new)
            print(f"  {tpath.name}: +{len(new)}")
        still_active = len(C.read_json(Path(tmp) / "state_active.json", {}) or {})
    print(f"取り込み: 追加 {added} 件 / 既にあった {skipped} 件 / 今も有効なので足さない {still_active} 件")
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("convert")
    c.add_argument("xls", nargs="+")
    c.add_argument("-o", "--output", required=True)
    m = sp.add_parser("merge")
    m.add_argument("fixture")
    m.add_argument("--out-dir", default="notam_out")
    m.add_argument("--now")
    a = ap.parse_args(argv)
    if a.cmd == "convert":
        feats, problems = convert(a.xls)
        Path(a.output).write_text(json.dumps(feats, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"変換: {len(feats)} 件(重複を除く)" + (f" / 読めなかった {len(problems)} 件" if problems else ""))
        for p in problems[:20]:
            print("  ", p, file=sys.stderr)
        return 0
    merge(a.fixture, a.out_dir, a.now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
