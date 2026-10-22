#!/usr/bin/env python3
"""
webpush.py
==========

ビューアの🔔に出る新着(報道・NOTAM・航行警報)を、スマホなどに Web Push で知らせる(2026-10-23〜、HANDOFF_NOTAM.md §30)。
ビューアを開いていなくても届く。サーバーは持たず、収集のワークフロー(news-collect.yml)の中から送る。

仕組み:
  - 鍵(VAPID): layers/webpush_vapid.json(暗号化して .enc だけをリポジトリに置く。VIEWER_KEY で作り直される)。
    公開鍵はビューア(geoplot-mil.html の WEBPUSH_PUBLIC_KEY)にも書いてある。
  - 登録: ビューアの🔔の欄で「この端末に通知を送る」→ 出てきた登録コード(購読情報を key で暗号化したもの)を、
    Actions の「Web Push subscribe」(webpush-subscribe.yml)に貼って実行 → news_out/push_subs.json に足し、確認の通知を送る。
  - 送信: 収集のたびに、ビューアの🔔と同じ規則で「前回までに送っていない新着」を数え、あれば1通にまとめて送る。
    送ったものは news_out/push_state.json に覚える。初めて動いたときは、その時点の分を送ったことにする(一度に大量に送らない)。
  - 端末側で通知をやめた・期限切れ(404/410)の登録は消す。

暗号化(RFC 8291 aes128gcm)と署名(RFC 8292 VAPID、ES256)は cryptography で行う(pywebpush は使わない)。

使い方:
  python webpush.py send --news news_out/news.json --notam notam_out/notam_cn.geojson --msa msa_out/military.geojson
  python webpush.py subscribe <登録コード>
  python webpush.py test                     # 登録しているすべての端末に試しの通知
  python webpush.py keygen                   # 鍵を作る(最初の1回だけ。layers/webpush_vapid.json に書く)
"""
import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VAPID_FILE = ROOT / "layers" / "webpush_vapid.json"
SUBS_FILE = ROOT / "news_out" / "push_subs.json"
STATE_FILE = ROOT / "news_out" / "push_state.json"
VIEWER_URL = "https://kw15-bot.github.io/geoplot_scheduledQuery/geoplot-mil.html"
SUBJECT = "https://kw15-bot.github.io/geoplot_scheduledQuery/"
TTL = 24 * 3600
MAX_LINES = 4          # 通知の本文に並べる見出しの数


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def b64u_dec(s):
    s = str(s).strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ----------------------------------------------------------------------------- 暗号化・署名
def _hkdf_extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk, info, length):
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt_payload(payload, p256dh, auth, as_private=None, salt=None):
    """RFC 8291(aes128gcm)。payload(bytes)を、購読の公開鍵 p256dh・auth で暗号化した本文(bytes)を返す。
    as_private・salt はテスト用(普段は毎回作る)。"""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    ua_public = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, ecdh), b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)          # 最後(1つだけ)の記録の区切り 0x02
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ct


def vapid_auth(endpoint, private_pem, public_b64u, now=None):
    """RFC 8292。Authorization ヘッダの値(vapid t=…, k=…)を返す。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    u = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": SUBJECT}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={head}.{body}.{sig}, k={public_b64u}"


def keygen():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = b64u(k.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))
    return {"private_pem": pem, "public_key": pub, "created": dt.date.today().isoformat()}


def send_one(sub, payload, vapid, post=None):
    """1つの端末に送る。HTTP の状態コードを返す(201 が成功。404・410 はその登録がもう使えない)。"""
    body = encrypt_payload(json.dumps(payload, ensure_ascii=False).encode("utf-8"), sub["keys"]["p256dh"], sub["keys"]["auth"])
    headers = {"TTL": str(TTL), "Urgency": "high", "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream",
               "Authorization": vapid_auth(sub["endpoint"], vapid["private_pem"], vapid["public_key"])}
    if post:
        return post(sub["endpoint"], body, headers)
    req = urllib.request.Request(sub["endpoint"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        print(f"[webpush] {urllib.parse.urlsplit(sub['endpoint']).netloc}: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
        return e.code
    except (urllib.error.URLError, OSError) as e:
        print(f"[webpush] {urllib.parse.urlsplit(sub['endpoint']).netloc}: {e}", file=sys.stderr)
        return 0


def send_all(payload, vapid, subs, post=None):
    """すべての端末に送る。使えなくなった登録(404/410)を除いた subs と、成功数を返す。"""
    keep, ok = [], 0
    for s in subs:
        code = send_one(s, payload, vapid, post)
        if code in (404, 410):
            print(f"[webpush] 登録を消す(端末側で通知をやめた・期限切れ): {urllib.parse.urlsplit(s['endpoint']).netloc}")
            continue
        ok += 200 <= code < 300
        keep.append(s)
    return keep, ok


# ----------------------------------------------------------------------------- 新着(ビューアの🔔と同じ規則)
NOTAM_BY_RE = re.compile(r"\bBY\s+(?:THE\s+)?(CHINA)(?![A-Z])")


def news_events(state):
    """報道: 通知の規則に当たった(notify)、遡った分でない投稿。中国海警局の日時なしの古い記事は除く。"""
    out = []
    for it in (state or {}).get("items", []):
        if not it.get("notify") or it.get("backfilled"):
            continue
        if it.get("source") == "ccg" and "T" not in str(it.get("published") or ""):
            continue
        out.append(("news:" + it["id"], f"報道: {it.get('title', '')}"))
    return out


def notam_events(geo):
    """NOTAM: 地図に描ける図形があり(本文の座標・航空路の区間・AIPの図の線)、DOM でなく、Active/Upcoming のもの。
    台北(RCTPYNYX)発行は、本文に BY CHINA があるものだけ。同じ発行元+番号+開始時刻は1件。"""
    out, seen = [], set()
    for f in (geo or {}).get("features", []):
        p, t = f.get("properties") or {}, (f.get("geometry") or {}).get("type")
        src = p.get("geometry_source")
        real = (src == "text-polygon" and t in ("Polygon", "MultiPolygon")) or \
               (src in ("route-segment", "aip-chart-line") and t in ("LineString", "MultiLineString"))
        if not real or p.get("classification") == "DOM" or (p.get("status") or "active") not in ("active", "upcoming"):
            continue
        if (p.get("issuer") or "ZBBBYNYX") == "RCTPYNYX" and not NOTAM_BY_RE.search(str(p.get("raw_text") or "").upper()):
            continue
        k = "notam:" + "|".join(str(p.get(x) or "") for x in ("issuer", "number", "valid_start"))
        if k in seen:
            continue
        seen.add(k)
        out.append((k, f"NOTAM: {' '.join(x for x in (p.get('fir') or p.get('icao_location'), p.get('number')) if x)}"))
    return out


def msa_events(geo):
    """航行警報: URL のある警告(URL の ? 以降は毎回変わるので除いて1件)。"""
    out, seen = [], set()
    for f in (geo or {}).get("features", []):
        p = f.get("properties") or {}
        if not p.get("url"):
            continue
        k = "msa:" + p["url"].split("?")[0]
        if k in seen:
            continue
        seen.add(k)
        out.append((k, f"航行警報: {p.get('title') or ''}"))
    return out


def pick_new(events_by_kind, state):
    """まだ送っていない新着を返し、state を更新する。初めての種類は今ある分を送ったことにする(新着なし)。
    覚えておくのは今ある分だけ(消えたものは忘れる)。"""
    new = []
    sent = state.setdefault("sent", {})
    for kind, events in events_by_kind.items():
        if events is None:                     # 読めなかった種類は触らない
            continue
        keys = [k for k, _ in events]
        if kind not in sent:
            sent[kind] = sorted(set(keys))
            continue
        old = set(sent[kind])
        new += [(k, label) for k, label in events if k not in old]
        sent[kind] = sorted(set(keys))
    return new


def build_payload(new):
    kinds = {}
    for k, _ in new:
        name = {"news": "報道", "notam": "NOTAM", "msa": "航行警報"}[k.split(":", 1)[0]]
        kinds[name] = kinds.get(name, 0) + 1
    title = "新着 " + "・".join(f"{n}{c}件" for n, c in kinds.items())
    lines = [label for _, label in new[:MAX_LINES]]
    if len(new) > MAX_LINES:
        lines.append(f"ほか {len(new) - MAX_LINES}件")
    return {"title": title, "body": "\n".join(lines), "url": VIEWER_URL, "tag": "geoplot-new"}


# ----------------------------------------------------------------------------- ファイル
def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def load_vapid():
    v = _load_json(VAPID_FILE, None)
    if not v:
        sys.exit(f"{VAPID_FILE} がありません(data_crypt.py decrypt layers で復号する)")
    return v


def decode_code(code, key=None):
    """ビューアの登録コード(購読情報を key で暗号化した箱を base64url にしたもの)を購読情報(dict)に戻す。"""
    import data_crypt
    box = b64u_dec(re.sub(r"\s+", "", code))
    sub = json.loads(data_crypt.decrypt_bytes(box, key or data_crypt.load_key()))
    if not str(sub.get("endpoint", "")).startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
        raise ValueError("登録コードの中身が購読情報ではありません")
    return {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]},
            "label": str(sub.get("label") or "")[:40], "added": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("send")
    s.add_argument("--news")
    s.add_argument("--notam")
    s.add_argument("--msa")
    sb = sp.add_parser("subscribe")
    sb.add_argument("code")
    sp.add_parser("test")
    sp.add_parser("keygen")
    a = ap.parse_args(argv)

    if a.cmd == "keygen":
        if VAPID_FILE.exists():
            sys.exit(f"{VAPID_FILE} は既にあります(作り直すと登録済みの端末に届かなくなる)")
        v = keygen()
        _save_json(VAPID_FILE, v)
        print("public key:", v["public_key"])
        return 0

    subs = _load_json(SUBS_FILE, {"subs": []})
    if a.cmd == "subscribe":
        sub = decode_code(a.code)
        subs["subs"] = [x for x in subs["subs"] if x["endpoint"] != sub["endpoint"]] + [sub]
        _save_json(SUBS_FILE, subs)
        keep, ok = send_all({"title": "通知の登録ができました", "body": "警報状況表示の新着をこの端末に送ります。",
                             "url": VIEWER_URL, "tag": "geoplot-test"}, load_vapid(), [sub])
        print(f"登録しました(登録数 {len(subs['subs'])})。確認の通知: {'送りました' if ok else '送れませんでした'}")
        return 0 if ok else 1

    if not subs["subs"]:
        print("登録している端末がありません")
        if a.cmd == "test":
            return 1
    vapid = load_vapid() if subs["subs"] else None
    if a.cmd == "test":
        keep, ok = send_all({"title": "試しの通知", "body": "警報状況表示からの通知のテストです。", "url": VIEWER_URL,
                             "tag": "geoplot-test"}, vapid, subs["subs"])
        subs["subs"] = keep
        _save_json(SUBS_FILE, subs)
        print(f"送りました: {ok}/{len(keep)}")
        return 0 if ok else 1

    # send
    load = lambda p: _load_json(p, None) if p and Path(p).exists() else None
    news, notam, msa = load(a.news), load(a.notam), load(a.msa)
    events = {"news": news_events(news) if news else None, "notam": notam_events(notam) if notam else None,
              "msa": msa_events(msa) if msa else None}
    state = _load_json(STATE_FILE, {})
    new = pick_new(events, state)
    if new and subs["subs"]:
        payload = build_payload(new)
        keep, ok = send_all(payload, vapid, subs["subs"])
        print(f"[webpush] {payload['title']} → {ok}/{len(subs['subs'])} 台に送った")
        if len(keep) != len(subs["subs"]):
            subs["subs"] = keep
            _save_json(SUBS_FILE, subs)
    else:
        print(f"[webpush] 新着 {len(new)} 件(登録 {len(subs['subs'])} 台)")
    _save_json(STATE_FILE, state)          # 時刻などは書かない(新着が無ければ中身が変わらず、コミットも出ない)
    return 0


if __name__ == "__main__":
    sys.exit(main())
