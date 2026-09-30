#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
aip_chart_extract.py
====================

eAIP の図(Terminal フォルダの PDF)から、「点の名前:座標」(例 G:N43 52.5 E087 13.1)が書かれた図だけを
抜き出して、小さな JSON 1つにまとめる(HANDOFF_NOTAM.md §24、aip_chart_areas.py の点の一覧の元)。
図の中身(PDF)はそのままでは送らず、この JSON だけを渡す。

使い方(Windows):
  py -m pip install pymupdf
  py aip_chart_extract.py "C:\...\EAIP2026-09.V1.3_Web\Data\EAIP2026-09.V1.3\Terminal"
  -> 同じフォルダに aip_chart_points.json ができる(--out で変更可)

出力: {"charts": [{"file": "<PDF名>", "chart": "ZWWW-3P-5", "title": "...", "eff": "EFF2507091600",
                   "points": {"G": [43.875, 87.218333], ...}, "notes": ["When the restriction area ..."]}, ...],
       "scanned": 3012, "errors": [...]}
"""

import argparse
import json
import os
import re
import sys

# G:N43 52.5 E087 13.1 / G: N43°52.5′ E087°13.1′ など
_PT = re.compile(
    r"(?<![A-Z0-9])([A-Z][A-Z0-9]{0,4})\s*[:：]\s*"
    r"([NS])\s*(\d{2})[°\s]*(\d{2}(?:\.\d+)?)?(?:[′'\s]*(\d{2}(?:\.\d+)?))?[″\"′'\s]*"
    r"([EW])\s*(\d{3})[°\s]*(\d{2}(?:\.\d+)?)?(?:[′'\s]*(\d{2}(?:\.\d+)?))?")
_NOTE = re.compile(r"(RESTRICT|PROHIBIT|FORBID|DANGER|ACTIVE)", re.I)
_CHART = re.compile(r"AIP-([A-Z]{4}-[0-9A-Z]+(?:-[0-9A-Z]+)*)")
_EFF = re.compile(r"EFF\d{10}")


def _deg(d, m, s):
    d = float(d)
    if m is None:
        return d
    if s is None:
        return d + float(m) / 60
    return d + float(m) / 60 + float(s) / 3600


def parse_points(text):
    pts = {}
    for m in _PT.finditer(text.replace("\n", " ")):
        name, ns, ld, lm, ls, ew, od, om, os_ = m.groups()
        lat = _deg(ld, lm, ls) * (-1 if ns == "S" else 1)
        lon = _deg(od, om, os_) * (-1 if ew == "W" else 1)
        if lm is None or om is None:
            continue                                   # 度だけ(分が無い)は点の定義ではないとみなす
        pts.setdefault(name, [round(lat, 6), round(lon, 6)])
    return pts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="Terminal フォルダ(中のフォルダもすべて読む)")
    ap.add_argument("--out", default="aip_chart_points.json")
    a = ap.parse_args()
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            sys.exit("PyMuPDF がありません: py -m pip install pymupdf")
    charts, errors, n = [], [], 0
    files = [os.path.join(r, f) for r, _, fs in os.walk(a.folder) for f in fs if f.lower().endswith(".pdf")]
    for i, path in enumerate(sorted(files), 1):
        n += 1
        try:
            doc = fitz.open(path)
            title = (doc.metadata or {}).get("title") or ""
            text = "\n".join(p.get_text() for p in doc)
            doc.close()
        except Exception as e:                          # 壊れたPDFなどは飛ばす
            errors.append({"file": os.path.basename(path), "error": str(e)[:200]})
            continue
        pts = parse_points(text)
        if pts:
            cm = _CHART.search(title)
            em = _EFF.search(text)
            notes = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if _NOTE.search(ln)]
            charts.append({"file": os.path.basename(path), "chart": cm.group(1) if cm else None, "title": title,
                           "eff": em.group(0) if em else None, "points": pts, "notes": notes[:20]})
        if i % 200 == 0:
            print(f"{i}/{len(files)} ... 見つかった図 {len(charts)}", file=sys.stderr)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump({"charts": charts, "scanned": n, "errors": errors}, fh, ensure_ascii=False, indent=1)
    print(f"完了: {n} 件を読み、点のある図 {len(charts)} 件 -> {os.path.abspath(a.out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
