#!/usr/bin/env python3
"""
notam_cn_probe.py -- NMS-API で「中国が発行するNOTAM」をどう絞り込めるかを実データで確認する調査用スクリプト。

収集スクリプトを作る前に、次の未確定事項を1回の実行で確認するのが目的:
  1. 認証が通るか（環境: fit / staging / prod）
  2. lastUpdatedDate の差分取得が何時間幅まで通るか（408タイムアウトの有無）
  3. 中国・香港・マカオ・台湾のNOTAMが実際に返るか（返る場合、icaoLocation / affectedFir / classification /
     accountId / selectionCode(Qコード) / coordinates+radius / geometry がどう入っているか）
  4. /v1/locationseries から中国のロケーションと accountId(AFTNアドレス)が引けるか
     （引ければ accountability フィルタでサーバ側絞り込みできる可能性がある）

使い方:
  export NMS_CLIENT_ID=...        # 配布Excelの KEY
  export NMS_CLIENT_SECRET=...    # 配布Excelの SECRET
  export NMS_ENV=prod             # fit | staging | prod  （認証情報を発行された環境に合わせる）
  python notam_cn_probe.py

出力: probe_out/report.json, probe_out/cn_sample.json, probe_out/locationseries_cn.json
      （トークン・シークレットはどのファイルにも標準出力にも書かない）

依存: Python 3.9+ 標準ライブラリのみ。
"""
import base64
import collections
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HOSTS = {
    "fit": "https://api-fit.cgifederal-aim.com",
    "staging": "https://api-staging.cgifederal-aim.com",
    "prod": "https://api-nms.aim.faa.gov",
}

# 対象のICAO地名指標の先頭2文字（notam_cn_collect.py の AREA_BY_PREFIX と同じにしておくこと）。
#   中国本土: ZB ZG ZH ZJ ZL ZP ZS ZU ZW ZY / ZX(=ZXXX 複数FIRにまたがるNOTAMの仮コード)
#   香港: VH / マカオ: VM / 台湾: RC
# 4文字トークンにだけ適用する。米国ARTCCの3文字コード(ZBW, ZHU, ZJX, ZLA, ZSE, ZUA 等)と衝突するため。
# A)項が複数地点（例 "ZGZU ZSHA"）の場合は空白等で分割して判定する。
# さらに足したい場合は環境変数 CN_EXTRA_PREFIXES=XX,YY
AREA_BY_PREFIX = {**{p: "CN" for p in ("ZB", "ZG", "ZH", "ZJ", "ZL", "ZP", "ZS", "ZU", "ZW", "ZY", "ZX")},
                  "VH": "HK", "VM": "MO", "RC": "TW"}
for _p in os.environ.get("CN_EXTRA_PREFIXES", "").split(","):
    if _p.strip():
        AREA_BY_PREFIX[_p.strip().upper()] = "EXTRA"
CN_PREFIXES = set(AREA_BY_PREFIX)

# 個別ロケーション問い合わせ。FIR(空域単位のNOTAMはここに付く)＋主要空港。
# FIRコードは要確認の候補。本当の一覧は locationseries と差分データから発見する。
PROBE_LOCATIONS = [
    "ZBPE", "ZSHA", "ZGZU", "ZHWH", "ZJSA", "ZLHW", "ZPKM", "ZWUQ", "ZYSH",  # 中国本土 FIR候補
    "ZBBB", "ZBAA", "ZSPD", "ZSSS", "ZGGG", "ZUUU", "ZYTX", "ZHHH", "ZJHK",   # 国際NOTAM室/空港
    "VHHK", "VHHH", "VMMC", "RCAA", "RCTP", "RCSS",                          # 香港・マカオ・台湾のFIR/空港候補
]

OUT_DIR = os.environ.get("PROBE_OUT", "probe_out")
TIMEOUT = 60


def die(msg, code=1):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def host():
    h = os.environ.get("NMS_HOST")  # テスト用の上書き
    if h:
        return h.rstrip("/")
    env = os.environ.get("NMS_ENV", "prod").lower()
    if env not in HOSTS:
        die(f"NMS_ENV は {sorted(HOSTS)} のいずれか（指定値: {env}）")
    return HOSTS[env]


def http(method, url, headers=None, data=None):
    """(status, parsed_json_or_text, response_headers) を返す。例外は握りつぶさず status=0 で返す。"""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            status, hdrs = r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        status, hdrs = e.code, dict(e.headers or {})
    except Exception as e:  # ネットワーク不通など
        return 0, f"{type(e).__name__}: {e}", {}
    try:
        return status, json.loads(body), hdrs
    except ValueError:
        return status, body[:500], hdrs


def get_token(base):
    cid, sec = os.environ.get("NMS_CLIENT_ID"), os.environ.get("NMS_CLIENT_SECRET")
    if not cid or not sec:
        die("NMS_CLIENT_ID / NMS_CLIENT_SECRET を環境変数で渡してください")
    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    # FAQ: 認証URLは /nmsapi を含まないホスト直下の /v1/auth/token
    st, body, _ = http(
        "POST", f"{base}/v1/auth/token",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        data=b"grant_type=client_credentials",
    )
    if st != 200 or not isinstance(body, dict) or "access_token" not in body:
        hint = " (認証情報を発行された環境と NMS_ENV が一致しているか確認)" if st in (400, 401) else ""
        die(f"トークン取得に失敗: HTTP {st}{hint}")
    return body["access_token"], int(body.get("expires_in", 0) or 0)


def api(base, token, path, params=None, fmt="GEOJSON"):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if fmt:
        headers["nmsResponseFormat"] = fmt  # /v1/notams では必須
    return http("GET", f"{base}/nmsapi{path}{q}", headers=headers)


def notam_of(feature):
    return ((feature.get("properties") or {}).get("coreNOTAMData") or {}).get("notam") or {}


def area_of(n):
    for key in ("icaoLocation", "affectedFir"):
        for tok in re.split(r"[\s,/;]+", (n.get(key) or "").upper()):
            if len(tok) == 4 and tok[:2] in AREA_BY_PREFIX:
                return AREA_BY_PREFIX[tok[:2]]
    return None


def is_cn(n):
    return area_of(n) is not None


def features_of(body):
    data = (body or {}).get("data") if isinstance(body, dict) else None
    items = (data or {}).get("geojson") or []
    return [f for f in items if isinstance(f, dict)]


def compact(f):
    n = notam_of(f)
    g = f.get("geometry") or {}
    gtypes = [x.get("type") for x in g.get("geometries", [])] if g.get("type") == "GeometryCollection" else [g.get("type")]
    return {
        "number": n.get("number"), "icaoLocation": n.get("icaoLocation"), "location": n.get("location"),
        "affectedFir": n.get("affectedFir"), "classification": n.get("classification"),
        "accountId": n.get("accountId"), "series": n.get("series"), "type": n.get("type"),
        "selectionCode": n.get("selectionCode"), "start": n.get("effectiveStart"), "end": n.get("effectiveEnd"),
        "coordinates": n.get("coordinates"), "radius": n.get("radius"),
        "lower": n.get("lowerLimit"), "upper": n.get("upperLimit"), "geometry": gtypes,
        "lastUpdated": n.get("lastUpdated"),
    }


def top(counter, k=12):
    return [[a, b] for a, b in counter.most_common(k)]


def fetch_delta(base, token, report):
    """差分取得。仕様上は24時間窓が上限。408が返る場合は窓を縮めて通る幅を探す。"""
    for label, delta in [("23h", dt.timedelta(hours=23)), ("6h", dt.timedelta(hours=6)),
                         ("1h", dt.timedelta(hours=1)), ("15m", dt.timedelta(minutes=15))]:
        since = (dt.datetime.now(dt.timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
        t0 = time.time()
        st, body, _ = api(base, token, "/v1/notams", {"lastUpdatedDate": since})
        el = round(time.time() - t0, 1)
        report["delta_attempts"].append({"window": label, "http": st, "seconds": el})
        print(f"  lastUpdatedDate 窓={label}: HTTP {st} ({el}s)")
        if st == 200:
            return label, features_of(body)
        if st not in (408, 500, 502, 503, 504, 0):
            break  # 400/401などは窓を縮めても直らない
    return None, []


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    base = host()
    report = {"host": base, "generated": dt.datetime.now(dt.timezone.utc).isoformat(), "delta_attempts": []}
    print(f"[1] 認証 ({base})")
    token, exp = get_token(base)
    report["token_expires_in_sec"] = exp
    print(f"  OK（有効期間 {exp} 秒）")

    st, _, _ = api(base, token, "/v1/ping", fmt=None)
    report["ping_http"] = st
    print(f"  ping: HTTP {st}")

    print("[2] 差分取得（全世界・GeoJSON）")
    window, feats = fetch_delta(base, token, report)
    report["delta_window_ok"] = window
    report["delta_total"] = len(feats)
    cn_feats = [f for f in feats if is_cn(notam_of(f))]
    report["delta_cn_total"] = len(cn_feats)

    if feats:
        notams = [notam_of(f) for f in feats]
        report["delta_by_classification"] = top(collections.Counter(n.get("classification") for n in notams))
        report["delta_by_location_prefix"] = top(collections.Counter((n.get("icaoLocation") or "")[:2] for n in notams), 20)
    print(f"  全世界 {len(feats)} 件 / うち中国判定 {len(cn_feats)} 件")

    if cn_feats:
        cn = [notam_of(f) for f in cn_feats]
        report["cn_by_area_group"] = top(collections.Counter(area_of(n) for n in cn))
        report["cn_multi_location_a_item"] = sum(1 for n in cn if len((n.get("icaoLocation") or "").split()) > 1)
        report["cn_by_classification"] = top(collections.Counter(n.get("classification") for n in cn))
        report["cn_by_accountId"] = top(collections.Counter(n.get("accountId") for n in cn))
        report["cn_by_icaoLocation"] = top(collections.Counter(n.get("icaoLocation") for n in cn), 20)
        report["cn_by_affectedFir"] = top(collections.Counter(n.get("affectedFir") for n in cn))
        report["cn_by_selectionCode"] = top(collections.Counter((n.get("selectionCode") or "")[:3] for n in cn), 20)
        report["cn_with_coordinates_radius"] = sum(1 for n in cn if n.get("coordinates") and n.get("radius"))
        report["cn_geometry_types"] = top(collections.Counter(
            tuple(compact(f)["geometry"]) for f in cn_feats))
        report["cn_sample_compact"] = [compact(f) for f in cn_feats[:8]]

    print("[3] ロケーション個別問い合わせ（FIR候補・主要空港）")
    per_loc, sample = {}, list(cn_feats[:10])
    for loc in PROBE_LOCATIONS:
        st, body, _ = api(base, token, "/v1/notams", {"location": loc})
        fs = features_of(body) if st == 200 else []
        per_loc[loc] = {"http": st, "count": len(fs)}
        sample.extend(fs[:2])
        print(f"  {loc}: HTTP {st}, {len(fs)} 件")
        time.sleep(0.3)
    report["per_location"] = per_loc

    print("[4] Location-Series（中国のロケーションとaccountId）")
    st, body, _ = api(base, token, "/v1/locationseries", fmt=None)
    report["locationseries_http"] = st
    if st == 200 and isinstance(body, dict):
        rows = ((body.get("data") or {}).get("locationSeries")) or []
        cn_rows = [r for r in rows if isinstance(r, dict) and len(r.get("icaoId") or "") == 4
                   and (r.get("icaoId") or "")[:2].upper() in CN_PREFIXES]
        report["locationseries_total"] = len(rows)
        report["locationseries_cn"] = len(cn_rows)
        accs = collections.Counter()
        for r in cn_rows:
            for ser in (r.get("internationalSeries") or {}).values():
                accs[(ser or {}).get("accountId")] += 1
        report["locationseries_cn_accountIds"] = top(accs, 15)
        with open(os.path.join(OUT_DIR, "locationseries_cn.json"), "w", encoding="utf-8") as fh:
            json.dump(cn_rows, fh, ensure_ascii=False, indent=1)
        print(f"  全 {len(rows)} 件 / 中国 {len(cn_rows)} 件 / accountId {dict(accs.most_common(5))}")
    else:
        print(f"  取得失敗: HTTP {st}")

    # 重複除去して保存（NMS_ID 単位）
    seen, uniq = set(), []
    for f in sample:
        k = notam_of(f).get("id") or json.dumps(compact(f), sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    with open(os.path.join(OUT_DIR, "cn_sample.json"), "w", encoding="utf-8") as fh:
        json.dump(uniq[:40], fh, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write("## NMS-API probe\n```json\n" + json.dumps(report, ensure_ascii=False, indent=1)[:60000] + "\n```\n")
    print(f"\n完了: {OUT_DIR}/report.json ほか。中国判定 差分={report['delta_cn_total']} 件、"
          f"個別問い合わせ合計={sum(v['count'] for v in per_loc.values())} 件")


if __name__ == "__main__":
    main()
