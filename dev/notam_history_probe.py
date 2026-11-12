#!/usr/bin/env python3
"""NOTAM の過去分(2026-01〜)をどこから取れるかを調べる(2026-11-13、調査用。何も保存しない)。

1) NMS-API(収集で使っている FAA の API)で、過去の日付を指定して返るか
   - effectiveStartDate / effectiveEndDate(1月の1週間)× location(北京 FIR ZBPE)
   - lastUpdatedDate を1月にした場合(窓が24時間までなら断られるはず)
2) FAA NOTAM Search(notams.aim.faa.gov)の過去検索が Actions から使えるか(応答の形だけ)

出すのは件数・状態コード・応答の先頭だけ。認証情報は出さない。
"""
import collections
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import notam_cn_collect as C  # noqa: E402


def summarize(feats):
    issuers = collections.Counter()
    starts = []
    for f in feats:
        p = (f.get("properties") or {}).get("coreNOTAMData", {}).get("notam", {}) or f.get("properties") or {}
        issuers[str(p.get("accountId") or p.get("issuer") or p.get("icaoLocation") or "?")[:10]] += 1
        s = p.get("effectiveStart") or p.get("issued") or ""
        if s:
            starts.append(str(s))
    starts.sort()
    return f"{len(feats)} 件 / 発行元上位 {issuers.most_common(4)} / 期間 {starts[:1]}〜{starts[-1:]}"


def nms():
    try:
        api = C.Nms()
    except C.ApiError as e:
        print("NMS: 認証できない:", e)
        return
    tries = [
        ("effective 1月第2週 × ZBPE", {"location": "ZBPE", "effectiveStartDate": "2026-01-08T00:00:00Z",
                                         "effectiveEndDate": "2026-01-15T00:00:00Z"}),
        ("effective 1月第2週 × ZGZU", {"location": "ZGZU", "effectiveStartDate": "2026-01-08T00:00:00Z",
                                         "effectiveEndDate": "2026-01-15T00:00:00Z"}),
        ("effective 先週 × ZBPE", {"location": "ZBPE", "effectiveStartDate": "2026-11-03T00:00:00Z",
                                    "effectiveEndDate": "2026-11-10T00:00:00Z"}),
        ("location ZBPE のみ(今)", {"location": "ZBPE"}),
        ("lastUpdatedDate 2026-01-10", {"lastUpdatedDate": "2026-01-10T00:00:00Z"}),
        ("lastUpdatedDate 7日前", {"lastUpdatedDate": "2026-11-06T00:00:00Z"}),
    ]
    for label, params in tries:
        st, body = api.get("/v1/notams", params)
        if st == 200 and isinstance(body, dict):
            print(f"NMS {label}: HTTP 200 {summarize(C.features_of(body))}")
        else:
            head = json.dumps(body, ensure_ascii=False)[:200] if isinstance(body, (dict, list)) else str(body)[:200]
            print(f"NMS {label}: HTTP {st} {head}")


def faa_search():
    url = "https://notams.aim.faa.gov/notamSearch/search"
    hdr = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
           "X-Requested-With": "XMLHttpRequest", "Referer": "https://notams.aim.faa.gov/notamSearch/nsapp.html"}
    forms = [
        ("現在 × ZBPE", {"searchType": "0", "designatorsForLocation": "ZBPE", "notamsOnly": "false",
                         "offset": "0", "notamsPerPage": "30"}),
        ("過去 2026-01-10 × ZBPE (archiveDate)", {"searchType": "0", "designatorsForLocation": "ZBPE",
                                                 "archiveDate": "01/10/2026", "archiveDesignator": "ZBPE",
                                                 "notamsOnly": "false", "offset": "0", "notamsPerPage": "30"}),
        ("過去 2026-01-10 × ZBPE (searchType=6)", {"searchType": "6", "archiveDate": "01/10/2026",
                                                  "archiveDesignator": "ZBPE", "offset": "0", "notamsPerPage": "30"}),
    ]
    for label, form in forms:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
            try:
                d = json.loads(text)
                lst = d.get("notamList") or []
                print(f"FAA {label}: HTTP 200 keys={sorted(d)[:8]} totalNotamCount={d.get('totalNotamCount')} "
                      f"list={len(lst)} error={str(d.get('error') or '')[:100]}")
                for n in lst[:3]:
                    print("    ", n.get("notamNumber"), n.get("facilityDesignator"), n.get("startDate"), n.get("endDate"),
                          str(n.get("icaoMessage") or "")[:80].replace("\n", " "))
            except ValueError:
                print(f"FAA {label}: HTTP 200 not JSON: {text[:200]!r}")
        except urllib.error.HTTPError as e:
            print(f"FAA {label}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            print(f"FAA {label}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    nms()
    faa_search()
