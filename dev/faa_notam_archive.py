#!/usr/bin/env python3
"""FAA NOTAM Search(https://notams.aim.faa.gov/notamSearch/)の過去検索から、北京発行の NOTAM を集める(2026-11-13)。

GitHub(クラウド)からは FAA に拒否される(HTTP 403)ので、ユーザーの PC(Windows・Python 3.14)で実行する。
標準ライブラリだけで動く(pip install は不要)。

使い方(PowerShell。このファイルを置いたフォルダで):
  1) 下調べ(最初に1回): サイトの仕組みを調べ、数日分を試しに取る。結果は probe_result.txt に保存される
       py faa_notam_archive.py probe
     → probe_result.txt を Claude に渡す(公開情報だけで、個人情報や鍵は入らない)

  2) 本番の一括収集は、下調べの結果を見て Claude がこのファイルを更新してから行う。

注意: FAA の公式な API ではない(サイトの画面が裏で使っている呼び出し)。間隔を空けて、少しずつ読む。
"""
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://notams.aim.faa.gov/notamSearch/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
# 北京(ZBBBYNYX)が NOTAM を出す中国本土の FIR
FIRS = ["ZBPE", "ZGZU", "ZHWH", "ZJSA", "ZLHW", "ZPKM", "ZSHA", "ZUUU", "ZWUQ", "ZYSH"]

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
OUT = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    OUT.append(s)


def get(url, data=None, headers=None, timeout=60):
    h = {"User-Agent": UA, "Accept": "*/*", "Referer": BASE + "nsapp.html"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def search(form):
    data = urllib.parse.urlencode(form).encode()
    return get(BASE + "search", data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest"})


def describe(st, text):
    try:
        d = json.loads(text)
    except ValueError:
        return f"HTTP {st} (JSON でない) {re.sub(r'\s+', ' ', text[:200])!r}"
    lst = d.get("notamList") or []
    head = [f"{n.get('notamNumber')} {n.get('facilityDesignator')} {n.get('startDate')}〜{n.get('endDate')}"
            for n in lst[:3]]
    return (f"HTTP {st} keys={sorted(d)[:10]} total={d.get('totalNotamCount')} 件数={len(lst)} "
            f"error={str(d.get('error') or '')[:120]!r} 例={head}")


def search_retry(form, tries=3):
    for k in range(tries):
        st, t = search(form)
        if st:                       # 0 = 通信エラー(名前解決の失敗など)はやり直す
            return st, t
        time.sleep(5 * (k + 1))
    return st, t


def probe():
    """2回目(2026-11-13): 画面の項目名は archiveDate / archiveDesignator / searchType。過去検索の searchType の番号を探す。"""
    log("=== 今の NOTAM(北京 FIR、比較用) ===")
    st, t = search_retry({"searchType": "0", "designatorsForLocation": "ZBPE", "notamsOnly": "false",
                          "offset": "0", "notamsPerPage": "30"})
    log(describe(st, t))
    log("=== 過去検索 2026-01-10 × 北京 FIR(ZBPE)、searchType を 1〜9 で試す ===")
    for stype in range(1, 10):
        time.sleep(2)
        form = {"searchType": str(stype), "designatorsForLocation": "", "archiveDate": "01/10/2026",
                "archiveDesignator": "ZBPE", "notamsOnly": "false", "offset": "0", "notamsPerPage": "30"}
        st, t = search_retry(form)
        log(f"searchType={stype} →", describe(st, t))
    with open("probe_result.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\nprobe_result.txt に保存しました。この画面の文字をコピーして Claude に渡してください。")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        probe()
    else:
        print(__doc__)
