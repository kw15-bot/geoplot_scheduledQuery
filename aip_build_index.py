#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
aip_build_index.py
==================

中国民航局 航行情報服務センター(AISC)が eAIP サイトで配布している AIP データセット
(AIP-DS、AIXM 5.1.1、例: CN_AIP-DS_EFF202610281600_AIRAC2611_V0_1.zip)から、NOTAM の
航空路閉鎖を地図化するのに必要な部分だけを抜き出した小さな索引(JSON)を作り、暗号化して保存する。

リポジトリ(公開)にデータセットそのものを置かないための仕組み(HANDOFF_NOTAM.md §19)。
暗号化した索引 aip/cn_aip_index.json.gz.enc だけをコミットし、鍵は GitHub の Secret
AIPDS_KEY に入れる。notam-collect.yml が収集前に復号して notam_cn_collect.py に渡す。

索引の中身:
  meta     : AIRAC番号・発効日時・データセットの利用条件(useLimitation)
  points   : {uuid: [designator, lat, lon, kind]}   kind = WPT(DesignatedPoint) / VOR_DME / NDB など
  routes   : {uuid: designator}                      例 "W187"
  segments : [[route_uuid, start_uuid, end_uuid], ...]

使い方(AIRAC更新のたびに、新しいZIPで作り直す):
  export AIPDS_KEY=...          # GitHub Secret と同じ値
  python aip_build_index.py CN_AIP-DS_EFF..._AIRAC2611_V0_1.zip
  # -> aip/cn_aip_index.json.gz.enc を書き出す(平文は残さない)
  python aip_build_index.py ZIP --plain out.json   # 確認用に平文で書く(コミットしないこと)

暗号化: openssl enc -aes-256-cbc -pbkdf2 -salt (鍵は環境変数 AIPDS_KEY)。復号は
  openssl enc -d -aes-256-cbc -pbkdf2 -pass env:AIPDS_KEY -in aip/cn_aip_index.json.gz.enc | gunzip
"""

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

NS = {
    "aixm": "http://www.aixm.aero/schema/5.1.1",
    "gml": "http://www.opengis.net/gml/3.2",
    "xlink": "http://www.w3.org/1999/xlink",
    "gmd": "http://www.isotc211.org/2005/gmd",
    "gco": "http://www.isotc211.org/2005/gco",
}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aip", "cn_aip_index.json.gz.enc")


def _baseline_members(zf, feature):
    """Baseline/CN_AIP-DS_<feature>_BASELINE_*.xml の各 feature 要素(最新のtimeSlice)を返す。"""
    names = [n for n in zf.namelist() if re.search(rf"Baseline/CN_AIP-DS_{feature}_BASELINE_.*\.xml$", n)]
    if not names:
        return
    with zf.open(names[0]) as fh:
        for _, el in ET.iterparse(fh, events=("end",)):
            if el.tag == f"{{{NS['aixm']}}}{feature}":
                uid = el.findtext("gml:identifier", namespaces=NS)
                ts = el.find(f"aixm:timeSlice/aixm:{feature}TimeSlice", NS)
                if uid and ts is not None:
                    yield uid.strip(), ts
                el.clear()


def _pos(ts):
    p = ts.findtext("aixm:location//gml:pos", namespaces=NS)
    if not p or not p.strip():
        return None
    lat, lon = (float(v) for v in p.split()[:2])
    return round(lat, 7), round(lon, 7)


def _ref(el):
    href = el.get(XLINK_HREF) if el is not None else None
    return href.split(":")[-1] if href else None


def build_index(zip_path):
    zf = zipfile.ZipFile(zip_path)
    meta_xml = zf.read("checksum.xml").decode("utf-8")
    meta = {k: (re.search(rf"<aisc-ds:{k}>(.*?)</aisc-ds:{k}>", meta_xml) or [None, None])[1]
            for k in ("pubNo", "effectiveTime", "issueTime", "version", "dataVariant")}
    dp_name = [n for n in zf.namelist() if "DesignatedPoint_BASELINE" in n][0]
    head = zf.open(dp_name).read(200000).decode("utf-8", "replace")
    m = re.search(r"<gmd:useLimitation>\s*<gco:CharacterString>(.*?)</gco:CharacterString>", head, re.S)
    meta["useLimitation"] = m[1].strip() if m else None
    meta["source"] = os.path.basename(zip_path)

    points = {}
    for uid, ts in _baseline_members(zf, "DesignatedPoint"):
        pos = _pos(ts)
        d = ts.findtext("aixm:designator", namespaces=NS)
        if pos and d:
            points[uid] = [d.strip(), pos[0], pos[1], "WPT"]
    for uid, ts in _baseline_members(zf, "Navaid"):
        pos = _pos(ts)
        d = ts.findtext("aixm:designator", namespaces=NS)
        if pos and d:
            points[uid] = [d.strip(), pos[0], pos[1], (ts.findtext("aixm:type", namespaces=NS) or "NAV").strip()]

    routes = {}
    for uid, ts in _baseline_members(zf, "Route"):
        des = "".join((ts.findtext(f"aixm:{k}", namespaces=NS) or "").strip()
                      for k in ("designatorPrefix", "designatorSecondLetter", "designatorNumber"))
        if des:
            routes[uid] = des

    segments = []
    for uid, ts in _baseline_members(zf, "RouteSegment"):
        r = _ref(ts.find("aixm:routeFormed", NS))
        ends = []
        for side in ("start", "end"):
            pt = ts.find(f"aixm:{side}/aixm:EnRouteSegmentPoint", NS)
            ref = None
            if pt is not None:
                for child in pt:
                    if child.tag.split("}")[-1].startswith("pointChoice_") and _ref(child):
                        ref = _ref(child)
                        break
            ends.append(ref)
        if r in routes and all(e in points for e in ends):
            segments.append([r, ends[0], ends[1]])
    return {"meta": meta, "points": points, "routes": routes, "segments": segments}


def encrypt_to(data_bytes, out_path, key):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    gz = gzip.compress(data_bytes, mtime=0)
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:AIPDS_KEY", "-out", out_path],
                   input=gz, check=True, env=dict(os.environ, AIPDS_KEY=key))


def load_encrypted(path, key):
    """暗号化した索引を復号して dict で返す(テスト・手元確認用)。"""
    r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:AIPDS_KEY", "-in", path],
                       capture_output=True, check=True, env=dict(os.environ, AIPDS_KEY=key))
    return json.loads(gzip.decompress(r.stdout))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", help="AIP-DS のZIP(CN_AIP-DS_EFF..._AIRAC...zip)")
    ap.add_argument("--out", default=OUT_DEFAULT, help="暗号化した索引の出力先")
    ap.add_argument("--plain", help="暗号化せずにこのパスへJSONで書く(確認用。コミットしないこと)")
    a = ap.parse_args()
    idx = build_index(a.zip)
    data = json.dumps(idx, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    print(f"AIRAC {idx['meta']['pubNo']} eff {idx['meta']['effectiveTime']}: "
          f"{len(idx['points'])} points, {len(idx['routes'])} routes, {len(idx['segments'])} segments", file=sys.stderr)
    if a.plain:
        with open(a.plain, "wb") as fh:
            fh.write(data)
        return 0
    key = os.environ.get("AIPDS_KEY")
    if not key:
        sys.exit("AIPDS_KEY が未設定です(GitHub Secret と同じ値を環境変数に入れてください)")
    encrypt_to(data, a.out, key)
    print(f"wrote {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
