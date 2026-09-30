#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
aip_build_routes_layer.py
=========================

ビューアの「レイヤー → 航空路」で表示する、中国の全航空路の GeoJSON を作って暗号化する(HANDOFF_NOTAM.md §22)。

元データは AIP データセットの索引(aip_build_index.py の出力。暗号化したものは aip/cn_aip_index.json.gz.enc)。
データセットの中身を公開しない方針なので、このファイルも暗号化してから置き、ビューアで key を入れたときだけ
ブラウザ内で復号する。鍵はビューアの key(Secret VIEWER_KEY)と同じ。

暗号化: gzip した GeoJSON を AES-256-GCM で暗号化する。鍵は PBKDF2-HMAC-SHA256(key, salt, ITER) で作る。
ブラウザの WebCrypto(PBKDF2 + AES-GCM)と DecompressionStream('gzip') でそのまま復号できる形式。
出力(JSON): {"v":1,"kdf":"PBKDF2-SHA256","iter":ITER,"salt":b64,"iv":b64,"ct":b64,
            "airac":"202611","effective":"2026-10-28T16:00:00Z"}   (airac/effective は資料の時点。暗号化しない)

使い方(通常は Actions の Viewer key sync が実行する):
  AIPDS_INDEX=/path/to/aip_index.json.gz VIEWER_KEY=... python aip_build_routes_layer.py [--out aip/cn_routes_layer.enc]
"""

import argparse
import base64
import gzip
import json
import os
import sys

ITER = 300000
OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aip", "cn_routes_layer.enc")


def load_index(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def build_routes_geojson(idx):
    """航空路ごとに1 Feature(MultiLineString、区間ごとに1本)。properties は航空路名だけ。"""
    points, routes = idx["points"], idx["routes"]
    by_route = {}
    for r, a, b in idx["segments"]:
        des = routes.get(r)
        if not des or a not in points or b not in points:
            continue
        pa, pb = points[a], points[b]
        seg = [[round(pa[2], 5), round(pa[1], 5)], [round(pb[2], 5), round(pb[1], 5)]]
        s = by_route.setdefault(des, set())
        s.add(tuple(map(tuple, sorted(seg))))          # 同じ区間の重複(往復・複数のRoute)を除く
    feats = []
    for des in sorted(by_route):
        lines = [[list(p) for p in seg] for seg in sorted(by_route[des])]
        feats.append({"type": "Feature", "properties": {"route": des},
                      "geometry": {"type": "MultiLineString", "coordinates": lines}})
    return {"type": "FeatureCollection", "features": feats}


def encrypt(data_bytes, key, airac=None, effective=None):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    salt, iv = os.urandom(16), os.urandom(12)
    k = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(key.encode("utf-8"))
    ct = AESGCM(k).encrypt(iv, gzip.compress(data_bytes, mtime=0), None)   # 末尾16バイトが認証タグ(WebCryptoと同じ並び)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct), "airac": airac,
            "effective": effective}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=os.environ.get("AIPDS_INDEX"), help="復号済みの索引(.json / .json.gz)")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args()
    key = os.environ.get("VIEWER_KEY", "")
    if not a.index or not os.path.isfile(a.index):
        sys.exit("索引が見つかりません(AIPDS_INDEX か --index を指定)")
    if not key:
        sys.exit("VIEWER_KEY が未設定です")
    idx = load_index(a.index)
    gj = build_routes_geojson(idx)
    data = json.dumps(gj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    meta = idx.get("meta") or {}
    out = encrypt(data, key, meta.get("pubNo"), meta.get("effectiveTime"))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"routes layer: {len(gj['features'])} routes, "
          f"{sum(len(f['geometry']['coordinates']) for f in gj['features'])} segments -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
