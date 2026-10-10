#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
data_crypt.py
=============

リポジトリ(公開)に置くデータと説明書きを暗号化する(HANDOFF_NOTAM.md §25)。
対象: notam_out/ ・ msa_out/ ・ layers/ の *.json / *.geojson / *.md と、HANDOFF.md ・ HANDOFF_NOTAM.md。
コード(.py)とビューア(geoplot-mil.html)は平文のまま。

リポジトリには暗号化したもの(元の名前 + ".enc"、例 notam_out/notam_cn.geojson.enc)だけを置き、
平文は .gitignore で除外する(平文を誤ってコミットできない)。収集・レポートのワークフローは
「復号 → 処理 → 暗号化 → .enc をコミット」の順に動く。

鍵: ビューアの key(GitHub Secret VIEWER_KEY と同じもの)。次の順に探す:
  1. --key-file のファイル(中身の1行目。前後の空白・改行は無視)
  2. 環境変数 VIEWER_KEY
  3. 環境変数 GEOPLOT_DATA_KEY
  4. 環境変数 GEOPLOT_DATA_KEY_FILE が指すファイル

形式(JSON、aip/cn_routes_layer.enc と同じ箱):
  {"v":1,"kdf":"PBKDF2-SHA256","iter":300000,"salt":b64,"iv":b64,"ct":b64}
  ct = AES-256-GCM(gzip(平文))、末尾16バイトが認証タグ(WebCrypto と同じ並び)。
  鍵 = PBKDF2-HMAC-SHA256(key, salt, 300000) の先頭32バイト。salt は全ファイル共通の固定値
  (ビューアが鍵を作るのを1回で済ませるため)。iv は「同じ平文なら同じ暗号文」になるよう、
  PBKDF2 の続き32バイトを鍵にした HMAC(gzip(平文)) の先頭12バイト(中身が変わらないファイルを
  毎回コミットし直さないため。分かるのは「前と同じ中身かどうか」だけ)。

使い方:
  python data_crypt.py decrypt                 # リポジトリ内の *.enc をすべて平文に戻す
  python data_crypt.py decrypt notam_out       # フォルダやファイルを指定してもよい
  python data_crypt.py encrypt                 # 対象の平文をすべて暗号化(中身が同じなら .enc は書き換えない)
  python data_crypt.py encrypt notam_out/state_active.json
  python data_crypt.py cat notam_out/meta.json.enc     # 復号して標準出力へ
  python data_crypt.py rekey --old-key-file old.txt    # key を変えたとき、全 .enc を新しい key で作り直す
  python data_crypt.py untrack                 # 平文のままコミットされている対象を git から外す(git rm --cached。ファイルは残る)
  必要なもの: pip install cryptography
"""

import argparse
import base64
import gzip
import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ITER = 300000
SALT = b"geoplot_scheduledQuery/data-v1"
DATA_DIRS = ("notam_out", "msa_out", "layers")
DATA_SUFFIXES = (".json", ".geojson", ".md")
DATA_FILES = ("HANDOFF.md", "HANDOFF_NOTAM.md")
ENC = ".enc"

_keys = {}


def load_key(key_file=None):
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").splitlines()[0].strip()
    for name in ("VIEWER_KEY", "GEOPLOT_DATA_KEY"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    f = os.environ.get("GEOPLOT_DATA_KEY_FILE", "")
    if f and Path(f).exists():
        return Path(f).read_text(encoding="utf-8").splitlines()[0].strip()
    sys.exit("鍵がありません(--key-file か 環境変数 VIEWER_KEY / GEOPLOT_DATA_KEY / GEOPLOT_DATA_KEY_FILE)")


def _derive(key, salt=SALT, iters=ITER):
    ck = (key, salt, iters)
    if ck not in _keys:
        _keys[ck] = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, iters, 64)
    return _keys[ck]


def encrypt_bytes(data, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    k = _derive(key)
    gz = bytearray(gzip.compress(data, compresslevel=9, mtime=0))
    gz[9] = 3      # ヘッダの OS 欄を Unix(3) に固定(Python 3.13 から 255 になり、同じ中身でも暗号文が変わるため)
    gz = bytes(gz)
    iv = hmac.new(k[32:], gz, hashlib.sha256).digest()[:12]
    ct = AESGCM(k[:32]).encrypt(iv, gz, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    box = {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b64(SALT), "iv": b64(iv), "ct": b64(ct)}
    return (json.dumps(box, separators=(",", ":")) + "\n").encode("ascii")


def decrypt_bytes(enc, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    box = json.loads(enc)
    k = _derive(key, base64.b64decode(box["salt"]), int(box["iter"]))
    try:
        gz = AESGCM(k[:32]).decrypt(base64.b64decode(box["iv"]), base64.b64decode(box["ct"]), None)
    except Exception:
        raise ValueError("復号できません(key が違う可能性)")
    return gzip.decompress(gz)


def is_data_path(p):
    """暗号化の対象(平文の側)か。p はリポジトリ直下からの相対パス。"""
    p = Path(p)
    parts = p.parts
    if len(parts) == 1:
        return p.name in DATA_FILES
    return parts[0] in DATA_DIRS and p.suffix in DATA_SUFFIXES


def _rel(p):
    p = Path(p).resolve()
    try:
        return p.relative_to(ROOT)
    except ValueError:
        sys.exit(f"{p} はリポジトリの外です")


def _walk(targets, want_enc):
    """targets(ファイル/フォルダ、空ならリポジトリ全体の対象)から、対象ファイルの相対パスを返す。
    want_enc=True なら *.enc、False なら平文。"""
    if not targets:
        targets = [ROOT / d for d in DATA_DIRS] + [ROOT / f for f in DATA_FILES]
        targets += [ROOT / (f + ENC) for f in DATA_FILES]
    out = []
    for t in targets:
        t = Path(t)
        if not t.is_absolute():
            t = ROOT / t
        files = sorted(x for x in t.rglob("*") if x.is_file()) if t.is_dir() else [t]
        for f in files:
            if not f.exists():
                continue
            r = _rel(f)
            if want_enc and r.suffix == ENC and is_data_path(r.with_suffix("")):
                out.append(r)
            elif not want_enc and is_data_path(r):
                out.append(r)
    return sorted(set(out))


def encrypt_file(rel, key):
    """平文 rel を rel.enc に暗号化する。中身が同じなら書き換えない。書き換えたら True。"""
    src, dst = ROOT / rel, ROOT / (str(rel) + ENC)
    new = encrypt_bytes(src.read_bytes(), key)
    if dst.exists() and dst.read_bytes() == new:
        return False
    dst.write_bytes(new)
    return True


def decrypt_file(rel_enc, key):
    """rel_enc(*.enc)を平文に戻す。中身が同じなら書き換えない。"""
    src = ROOT / rel_enc
    dst = ROOT / str(rel_enc)[:-len(ENC)]
    data = decrypt_bytes(src.read_bytes(), key)
    if dst.exists() and dst.read_bytes() == data:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return True


def encrypt_paths(paths, key=None):
    """平文のファイル/フォルダを暗号化し、git add すべき .enc の相対パス(posix)の一覧を返す
    (書き換えたかどうかによらず、対象の平文に対応する .enc すべて)。"""
    key = key or load_key()
    outs = []
    for rel in _walk(paths, want_enc=False):
        encrypt_file(rel, key)
        outs.append((str(rel) + ENC).replace("\\", "/"))
    return outs


def git_show_json(rev, rel_path, key=None):
    """git の rev にある rel_path(平文の名前)を、rel_path.enc を復号して JSON で返す。無ければ None。"""
    r = subprocess.run(["git", "show", f"{rev}:{rel_path}{ENC}"], capture_output=True, cwd=ROOT)
    if r.returncode != 0:
        return None
    return json.loads(decrypt_bytes(r.stdout, key or load_key()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["encrypt", "decrypt", "cat", "rekey", "untrack"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--key-file")
    ap.add_argument("--old-key-file", help="rekey: 今の .enc を作った(古い)key のファイル")
    a = ap.parse_args()
    if a.cmd == "untrack":
        r = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, cwd=ROOT, check=True)
        tracked = [p for p in r.stdout.split("\0") if p and is_data_path(p)]
        if tracked:
            subprocess.run(["git", "rm", "-q", "--cached", "--", *tracked], cwd=ROOT, check=True)
        print(f"untrack: {len(tracked)} files")
        return
    key = load_key(a.key_file)

    if a.cmd == "cat":
        for p in a.paths:
            sys.stdout.buffer.write(decrypt_bytes(Path(p).read_bytes(), key))
        return
    if a.cmd == "rekey":
        if not a.old_key_file:
            sys.exit("--old-key-file が要ります")
        old = load_key(a.old_key_file)
        n = 0
        for rel in _walk(a.paths, want_enc=True):
            p = ROOT / rel
            try:
                data = decrypt_bytes(p.read_bytes(), old)
            except ValueError:
                decrypt_bytes(p.read_bytes(), key)      # すでに新しい key なら何もしない(違えばここで止まる)
                continue
            p.write_bytes(encrypt_bytes(data, key))
            n += 1
        print(f"rekey: {n} files")
        return
    if a.cmd == "decrypt":
        files = _walk(a.paths, want_enc=True)
        n = sum(decrypt_file(r, key) for r in files)
        print(f"decrypt: {len(files)} files ({n} written)")
        return
    files = _walk(a.paths, want_enc=False)
    n = sum(encrypt_file(r, key) for r in files)
    print(f"encrypt: {len(files)} files ({n} written)")


if __name__ == "__main__":
    main()
