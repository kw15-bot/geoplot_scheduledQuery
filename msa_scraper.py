#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
msa_scraper.py — 中国海事局(MSA) 航行警告スクレイパー（軍事関連抽出版）

サーバー側（自分のPC/VPS）で実行することを想定した、GeoPlot MIL の補完ツールです。
ブラウザ内のCORSプロキシに頼らず、requestsで直接アクセスするため403/CORSの問題を回避できます。

使い方:
  pip install requests beautifulsoup4
  python3 msa_scraper.py --once                 # 1回だけ実行
  python3 msa_scraper.py --loop --interval 15   # 15分ごとに繰り返し実行（Ctrl+Cで停止）

  初回実行（state.json未作成）は各海事局の一覧を既定3ページ分遡って取得し、取りこぼしを減らす。
  2回目以降は既定1ページのみ。--first-run-pages / --pages で変更可能。

出力（既定では ./msa_out/ 以下）:
  state.json              全既知警告（軍事/非軍事問わず）の累積データ
  military.geojson         軍事関連＆座標抽出済みのGeoJSON（GeoPlot MILにインポート可能）
  military_latest.json     直近実行で新規に見つかった軍事関連候補（座標有無問わず）のサマリ

注意（マナー）:
  - 公開されている安全情報（航行警告）を読むだけの用途です。認証突破や非公開情報の取得は行いません。
  - --delay で十分な間隔（既定1.5秒）を空けています。短くしすぎず、過度な高頻度実行は避けてください。
  - サイト構造の変更で解析が崩れる可能性があります。
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
import urllib3


def _make_windows_stdio_safe():
    """On Windows, when stdout/stderr are attached to a real interactive console,
    Python uses a Unicode-safe console I/O path that can print any character
    regardless of the OS locale's codepage. But the moment stdout is piped or
    redirected (e.g. `python msa_scraper.py ... | powershell ... Tee-Object`, output
    redirected to a log file, or run headless via Task Scheduler/pythonw.exe), that
    special path no longer applies and Python falls back to the locale's codepage
    (cp932 on Japanese Windows) -- which can't represent many simplified-Chinese
    characters used in MSA bureau/warning text (e.g. 辽宁, 军事), crashing with
    UnicodeEncodeError mid-run. Force UTF-8 (replacing anything still unmappable
    instead of crashing) in exactly that non-console situation; a real interactive
    console is left alone since it already handles full Unicode correctly on its own."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue  # e.g. pythonw.exe, which has no console/streams at all
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # best-effort -- never let this safety net itself crash the script


_make_windows_stdio_safe()

try:
    import certifi
    _CERT_PATH = certifi.where()
except ImportError:
    _CERT_PATH = True  # fall back to requests' default handling

try:
    from bs4 import BeautifulSoup
    HAVE_BS4 = True
except ImportError:
    HAVE_BS4 = False

# ------------------------------------------------------------------
# 監視対象の海事局
# ------------------------------------------------------------------
BUREAUS = [
    {"id": "sh", "name": "上海海事局", "url": "https://www.msa.gov.cn/94df14ce1110415da44e67593e76619f/index.jhtml", "coastal": True},
    {"id": "tj", "name": "天津海事局", "url": "https://www.msa.gov.cn/bdba5fad6e5d48679f970fcf8efb8636/index.jhtml", "coastal": True},
    {"id": "ln", "name": "辽宁海事局", "url": "https://www.msa.gov.cn/c8896863b1014c438705536a03eb46ff/index.jhtml", "coastal": True},
    {"id": "hb", "name": "河北海事局", "url": "https://www.msa.gov.cn/93b73989d22045f9bc3270a6eba35180/index.jhtml", "coastal": True},
    {"id": "sd", "name": "山东海事局", "url": "https://www.msa.gov.cn/36ea3354c8f84953aba082d6d989c750/index.jhtml", "coastal": True},
    {"id": "zj", "name": "浙江海事局", "url": "https://www.msa.gov.cn/8e10ea74eb9e4c9690f8f891968add80/index.jhtml", "coastal": True},
    {"id": "fj", "name": "福建海事局", "url": "https://www.msa.gov.cn/7b08405760384570a0fb44e9204c4b1d/index.jhtml", "coastal": True},
    {"id": "gd", "name": "广东海事局", "url": "https://www.msa.gov.cn/1e478d409e854918bf12478b8a19f4a8/index.jhtml", "coastal": True},
    {"id": "gx", "name": "广西海事局", "url": "https://www.msa.gov.cn/86de2fffff2c47f98359fd1f20d6508f/index.jhtml", "coastal": True},
    {"id": "hn", "name": "海南海事局", "url": "https://www.msa.gov.cn/d3340711057b494b8fa09eedc4c5ead9/index.jhtml", "coastal": True},
    {"id": "cj", "name": "长江海事局", "url": "https://www.msa.gov.cn/9340423406cc4507b2fb8af2492d2a3d/index.jhtml", "coastal": False},
    {"id": "js", "name": "江苏海事局", "url": "https://www.msa.gov.cn/b5b0f3c7630d4967b1e6b06208575d15/index.jhtml", "coastal": True},
    {"id": "sz", "name": "深圳海事局", "url": "https://www.msa.gov.cn/325fdc0892b44313a63ee5c165be98ec/index.jhtml", "coastal": True},
    {"id": "ly", "name": "连云港海事局", "url": "https://www.msa.gov.cn/fa4501f3dbe44f70bc726f27132d4e04/index.jhtml", "coastal": True},
]

# 「サイト側で恒久的にアクセス不能になった（リンク切れ・ページ削除等）と判断した」
# 記事を、地図の結果一覧から個別に除外するための手動ブロックリスト。state.json内の
# データそのものは消さず(履歴として残る)、build_geojson()の出力からだけ除外する。
# own_id(記事タイトルから自動抽出される「琼航警132/26」のような通し番号、
# extract_own_id参照)をキーにしているので、URLのアクセストークンが変わっても
# 影響を受けない。対応不能と判断した記事が増えたら、ここに1行足すだけでよい。
MANUALLY_EXCLUDED_OWN_IDS = {
    "琼航警132/26",  # 2026-08 時点で本文に恒久的にアクセスできず(detail_gave_up)、除外
    "琼航警127/26",  # 同上
}

# Bump this whenever parse_coordinate_groups / summarize_validity / extract_article_body
# logic changes in a way that could produce different results for already-fetched items.
# Cached state entries stamped with an older version get re-fetched automatically, so a
# past failure (e.g. "validity": None from a regex that didn't yet handle some date
# format) doesn't get stuck forever just because the "validity" key already exists.
#
# v7: fetch() started detecting being redirected away from the article path (stale WAF
# access token) and treating it as a failure instead of silently caching whatever wrong
# page it landed on -- forced a retry of anything previously (mis)recorded as
# "successfully" fetched with 0 coordinates/validity.
# v8: extract_validity_periods() now also accepts a bare date range with no time clause
# at all (e.g. "7月28日至31日，在...") as a full-day validity window, instead of
# silently discarding it -- forces a retry of anything cached with "validity": None
# purely because of that gap.
# v9: fetch()'s stale-token redirect check now verifies the final URL matches the exact
# article requested (not just the generic "/hxaq/article/" path shape). A stale-token
# redirect can land on a different REAL article that still matches that shape -- this
# happened in practice (鲁航警628/26 was cached with a real but wrong article's
# coordinates, off the coast of Cyprus). Forces a retry of anything that might have been
# silently mixed up with a different article's content under the old, looser check.
# v10: now also stores the full extracted body text ("raw_text") for geoplot-mil.html's
# list-view mode (title + full original text per item). Cached entries from before this
# don't have raw_text at all -- bumping forces a one-time backfill.
# v11: extract_article_body() now also strips the leading "HH:MM [字体：...] 分享到："
# toolbar row and the duplicated "◯航警NNN/YY，" warning-ID prefix from the body text --
# previously only the structural-fallback path trimmed anything, so any article whose
# body came from an ARTICLE_BODY_SELECTORS match (the common case) kept this chrome
# verbatim in raw_text. Bumping forces a one-time re-fetch to clean up already-cached text.
# v12: now also detects "撤销"/"撤消" (revocation) notices. Their target ID is usually
# right in the title (extracted for free, no fetch needed); only when the title names
# no target at all does this fetch the body to look there instead -- see
# extract_cancelled_ids() / _cancellation_worth_fetching() in run_once(). Used to mark a
# still-nominally-valid military warning as revoked even though its stated validity
# window hasn't ended yet. Bumping forces a one-time re-fetch so already-cached
# cancellation notices with no title-target get scanned for this too.
# v13: extract_validity_periods() now also accepts a bare date with no time clause
# (see v8) when it's immediately followed by "以下.../如下..." introducing an itemized
# list of points (e.g. "8月8日，以下四点连线海域内进行射击训练：（1）..."), not just
# by "在.../将在.../拟在..." directly naming the area. Forces a retry of anything cached
# with "validity": None purely because of that gap.
# v14: found via a --pages 10 deep re-scan + audit of the resulting logs (see
# deep_scan_report.py). Three independent fixes, all narrow/additive:
#   - DDM_HYPHEN coordinates (e.g. "38-42.80N") now also accept a prime-symbol-turned-
#     apostrophe between the minutes value and the hemisphere letter (e.g. "38-42.80'N",
#     from source text using "38-42.80′N") -- previously only the plain "°...'" DDM
#     format accepted that apostrophe, not the hyphen-separated one.
#   - The daily time-range clause ("每天HHMM时至HHMM时") now also accepts the "至"
#     being altogether missing (a real typo seen in at least one bureau's text, e.g.
#     "每天0800时1900时" instead of "每天0800时至1900时").
#   - A bare date range with no time clause is now also accepted as a validity window
#     when it's immediately preceded by a "活动时间："/"时间：" label, as used in the
#     numbered-clause notice template some bureaus use (e.g. "一、活动时间： 2026年
#     6月24日至7月8日。"), not just when immediately followed by 在/以下/如下 etc.
# Bumping forces a one-time re-fetch of anything cached with a validity/coords gap
# purely because of one of these three gaps.
# v15: two more fixes found via real-world notices (粤航警551/26, 粤航警552/26):
#   - New DMS_HYPHEN coordinate pattern for degrees-minutes-seconds all hyphen-separated
#     with no space before the hemisphere letter or the next coordinate (e.g.
#     "23-14-12N117-20-42E"). Previously this silently fell through to DDM_HYPHEN
#     partially matching starting at the *minutes* segment (e.g. "14-12N" inside
#     "23-14-12N"), dropping the leading degrees and producing a wildly wrong position
#     (551 ended up plotted in South Sudan instead of the South China Sea).
#   - The date-list clause in _DATEPART_RE now also accepts "和"/"及"/"以及" ("and") as
#     a separator between listed dates, not just "、"/","/"，" (e.g. "8月12日和14日，
#     每天0800时至1800时"). Previously this failed to match at all, leaving the whole
#     validity window undetected ("期間不明").
# Bumping forces a one-time re-fetch of anything cached with a coords/validity gap
# purely because of one of these two gaps.
DETAIL_EXTRACTION_VERSION = 15

# If an article's detail fetch keeps failing with a stale-access-token redirect (see
# fetch()'s "想定外のURLへリダイレクトされました" check below), it usually means the
# article has scrolled off page 1 of its bureau's list, so it can never get re-listed
# with a fresh ?hav=... token under routine --pages 1 loop runs -- detail_fetched would
# otherwise stay False forever and raw_text would never appear in geoplot-mil.html's
# list mode. Once an item has failed this many times, its bureau gets a deeper page scan
# (STALE_RESCAN_PAGES instead of the usual 1) on the next run, to give it a chance to be
# re-listed and re-tokened. Bureaus with no stuck items are unaffected, so this doesn't
# add request volume across the board -- only for bureaus that actually need it.
STALE_DETAIL_RETRY_THRESHOLD = 3
STALE_RESCAN_PAGES = 3
# If detail fetch is STILL failing after this many attempts (including the deep-rescan
# attempts above), the article is treated as permanently unavailable (link genuinely
# dead / warning long since removed from the bureau's site) rather than retried forever
# -- at the default 30-minute loop interval this is roughly 10 hours of retrying, which
# comfortably covers normal re-listing delays without hammering the site indefinitely
# for something that's never coming back.
DETAIL_GIVE_UP_THRESHOLD = 20

DEFAULT_KEYWORDS = [
    "军事", "演习", "实弹", "实弹射击", "实弹发射", "军事训练", "军演", "火箭", "导弹",
    "炮击", "军舰", "潜艇", "布雷", "扫雷", "军用",
    "military", "exercise", "gun firing", "live-fire", "live fire", "missile",
    "rocket", "naval", "warship", "submarine", "minesweeping", "artillery", "gunnery",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
}

# ------------------------------------------------------------------
# 座標パーサー（GeoPlot MIL の app.js のロジックを移植）
# ------------------------------------------------------------------
ICAO_TABLE = {
    "RJTT": (35.5523, 139.7798), "RJAA": (35.7647, 140.3864), "RJOO": (34.7855, 135.4382),
    "RJBB": (34.4273, 135.2440), "RJFF": (33.5859, 130.4506), "RJCC": (42.7752, 141.6923),
    "RJOA": (34.2578, 133.8236), "RJGG": (34.8584, 136.8048), "RKSI": (37.4602, 126.4407),
    "RKPC": (33.5113, 126.4930), "RKPK": (35.1795, 128.9382), "ZBAA": (40.0801, 116.5846),
    "ZBAD": (39.5098, 116.4109), "ZSPD": (31.1443, 121.8083), "ZSSS": (31.1979, 121.3363),
    "ZGGG": (23.3924, 113.2988), "ZGSZ": (22.6393, 113.8107), "ZUUU": (30.5785, 103.9468),
    "ZPPP": (25.1019, 102.9292), "VHHH": (22.3080, 113.9185), "RCTP": (25.0777, 121.2328),
    "RCSS": (25.0694, 121.5519), "RPLL": (14.5086, 121.0198), "WSSS": (1.3644, 103.9915),
    "VTBS": (13.6900, 100.7501), "VVNB": (21.2212, 105.8072), "VVTS": (10.8188, 106.6520),
}


def normalize_symbols(t: str) -> str:
    t = re.sub(r"[°º˚]", "°", t)
    t = re.sub(r"[′’']", "'", t)
    t = re.sub(r"[″”\"]", '"', t)
    t = t.replace("，", ",")
    for dash in ["－", "―", "—", "–", "‐", "‑", "‒", "−"]:
        t = t.replace(dash, "-")
    t = t.replace("Ｎ", "N").replace("Ｓ", "S").replace("Ｅ", "E").replace("Ｗ", "W")
    return t


def convert_chinese_labels(t: str) -> str:
    def lat_sub(m):
        direction, d, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
        hemi = "N" if direction == "北纬" else "S"
        s = d + "°"
        if mm:
            s += mm + "'"
        if ss:
            s += ss + '"'
        return s + hemi

    def lon_sub(m):
        direction, d, mm, ss = m.group(1), m.group(2), m.group(3), m.group(4)
        hemi = "E" if direction == "东经" else "W"
        s = d + "°"
        if mm:
            s += mm + "'"
        if ss:
            s += ss + '"'
        return s + hemi

    t = re.sub(r"(北纬|南纬)\s*(\d{1,2}(?:\.\d+)?)°?\s*(\d{1,2}(?:\.\d+)?)?'?\s*(\d{1,2}(?:\.\d+)?)?\"?", lat_sub, t)
    t = re.sub(r"(东经|西经)\s*(\d{1,3}(?:\.\d+)?)°?\s*(\d{1,2}(?:\.\d+)?)?'?\s*(\d{1,2}(?:\.\d+)?)?\"?", lon_sub, t)
    return t


def dms_to_decimal(deg, minute, sec, hemi):
    v = float(deg or 0) + float(minute or 0) / 60 + float(sec or 0) / 3600
    if hemi in ("S", "W"):
        v = -v
    return v


def _valid_lat(v):
    return -90 <= v <= 90


def _valid_lon(v):
    return -180 <= v <= 180


# (priority order matters: more specific formats first)
_PATTERNS = []


def _pat(name, regex, build):
    # IMPORTANT: re.ASCII forces \b to use ASCII-only word-boundary semantics.
    # Without this, Python 3's \b treats CJK characters as "word" characters
    # (unlike JavaScript's \b, which is ASCII-only), so a boundary like
    # "在21-31.97N" would fail to match right after the Chinese character and
    # silently swallow only "31.97N", dropping the "21-" degree prefix.
    _PATTERNS.append((name, re.compile(regex, re.ASCII), build))


def _b_notam_lat(m):
    deg, mn, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if deg > 90 or mn >= 60 or sec >= 60:
        return None
    return ("lat", dms_to_decimal(deg, mn, sec, m.group(4)))


def _b_notam_lon(m):
    deg, mn, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if deg > 180 or mn >= 60 or sec >= 60:
        return None
    return ("lon", dms_to_decimal(deg, mn, sec, m.group(4)))


def _b_navarea_lat(m):
    deg, mn = int(m.group(1)), int(m.group(2))
    if deg > 90 or mn >= 60:
        return None
    return ("lat", dms_to_decimal(deg, mn, 0, m.group(3)))


def _b_navarea_lon(m):
    deg, mn = int(m.group(1)), int(m.group(2))
    if deg > 180 or mn >= 60:
        return None
    return ("lon", dms_to_decimal(deg, mn, 0, m.group(3)))


def _b_dms(m):
    deg, mn, sec, hemi = int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(4)
    is_lat = hemi in ("N", "S")
    if is_lat and (deg > 90 or mn >= 60):
        return None
    if not is_lat and (deg > 180 or mn >= 60):
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, mn, sec, hemi))


def _b_ddm(m):
    deg, mn, hemi = int(m.group(1)), float(m.group(2)), m.group(3)
    is_lat = hemi in ("N", "S")
    if is_lat and (deg > 90 or mn >= 60):
        return None
    if not is_lat and (deg > 180 or mn >= 60):
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, mn, 0, hemi))


def _b_dms_hyphen(m):
    # "23-14-12N" -- degrees-minutes-seconds, hyphen separated, hemisphere suffix directly
    # attached with no space (and the next coordinate's longitude often follows immediately
    # with no space either, e.g. "23-14-12N117-20-42E"). This is distinct from _b_ddm's
    # "21-47.00N" (degrees-DECIMAL MINUTES, only 2 hyphen-separated numbers) -- without this
    # pattern, the DDM_HYPHEN pattern below would partially match starting at the *minutes*
    # segment (e.g. "14-12N" inside "23-14-12N"), silently dropping the leading degrees and
    # producing a wildly wrong position.
    deg, mn, sec, hemi = int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(4)
    is_lat = hemi in ("N", "S")
    if is_lat and (deg > 90 or mn >= 60 or sec >= 60):
        return None
    if not is_lat and (deg > 180 or mn >= 60 or sec >= 60):
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, mn, sec, hemi))


def _b_dms_prefix(m):
    hemi, deg, mn, sec = m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4))
    is_lat = hemi in ("N", "S")
    if is_lat and (deg > 90 or mn >= 60):
        return None
    if not is_lat and (deg > 180 or mn >= 60):
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, mn, sec, hemi))


def _b_ddm_prefix(m):
    hemi, deg, mn = m.group(1), int(m.group(2)), float(m.group(3))
    is_lat = hemi in ("N", "S")
    if is_lat and (deg > 90 or mn >= 60):
        return None
    if not is_lat and (deg > 180 or mn >= 60):
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, mn, 0, hemi))


def _b_dd(m):
    deg, hemi = float(m.group(1)), m.group(2)
    is_lat = hemi in ("N", "S")
    if is_lat and deg > 90:
        return None
    if not is_lat and deg > 180:
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, 0, 0, hemi))


def _b_dd_prefix(m):
    hemi, deg = m.group(1), float(m.group(2))
    is_lat = hemi in ("N", "S")
    if is_lat and deg > 90:
        return None
    if not is_lat and deg > 180:
        return None
    return ("lat" if is_lat else "lon", dms_to_decimal(deg, 0, 0, hemi))


def _b_icao(m):
    code = m.group(1)
    if code not in ICAO_TABLE:
        return None
    return ("point", ICAO_TABLE[code])


_pat("NOTAM_LAT", r"(?<!\d)(\d{2})(\d{2})(\d{2})([NS])(?![A-Za-z])", _b_notam_lat)
_pat("NOTAM_LON", r"(?<!\d)(\d{3})(\d{2})(\d{2})([EW])(?![A-Za-z])", _b_notam_lon)
_pat("NAVAREA_LAT", r"(?<!\d)(\d{2})(\d{2})([NS])(?![A-Za-z])", _b_navarea_lat)
_pat("NAVAREA_LON", r"(?<!\d)(\d{3})(\d{2})([EW])(?![A-Za-z])", _b_navarea_lon)
_pat("DMS", r"(\d{1,3})°\s*(\d{1,2})'\s*(\d{1,2}(?:\.\d+)?)\"?\s*([NSEW])", _b_dms)
_pat("DMS_PREFIX", r"([NSEW])\s*(\d{1,3})°\s*(\d{1,2})'\s*(\d{1,2}(?:\.\d+)?)\"?", _b_dms_prefix)
_pat("DDM", r"(\d{1,3})°\s*(\d{1,2}(?:\.\d+)?)'?\s*([NSEW])", _b_ddm)
_pat("DDM_PREFIX", r"([NSEW])\s*(\d{1,3})°\s*(\d{1,2}(?:\.\d+)?)'?", _b_ddm_prefix)
_pat("DDM_HYPHEN", r"(?<!\d)(\d{1,3})-(\d{1,2}(?:\.\d+)?)'?\s*([NSEW])(?![A-Za-z])", _b_ddm)
_pat("DMS_HYPHEN", r"(?<!\d)(\d{1,3})-(\d{1,2})-(\d{1,2}(?:\.\d+)?)([NSEW])(?![A-Za-z])", _b_dms_hyphen)
_pat("DD", r"(?<!\d)(\d{1,3}(?:\.\d+)?)°?\s*([NSEW])(?![A-Za-z])", _b_dd)
_pat("DD_PREFIX", r"(?<![A-Za-z0-9])([NSEW])\s*(\d{1,3}(?:\.\d+)?)°?\b", _b_dd_prefix)
_pat("ICAO", r"\b([A-Z]{4})\b", _b_icao)


def extract_tokens(text):
    found = []
    for name, regex, build in _PATTERNS:
        for m in regex.finditer(text):
            built = build(m)
            if not built:
                continue
            axis, value = built[0], built[1]
            found.append({"start": m.start(), "end": m.end(), "axis": axis, "value": value, "raw": m.group(0), "fmt": name})
    # Resolve overlaps by leftmost start position (greedy), not pattern priority. This is
    # what correctly disambiguates prefix-style coordinates ("N 22°10.5' E 113°35.2'") from
    # suffix-style ones ("22°10.5'N 113°35.2'E") -- a hemisphere letter that begins a new
    # prefix-style token earlier in the text wins over a later-starting suffix pattern that
    # would otherwise swallow it as someone else's trailing hemisphere.
    order = {name: i for i, (name, _, _) in enumerate(_PATTERNS)}
    found.sort(key=lambda t: (t["start"], order[t["fmt"]], -(t["end"] - t["start"])))
    accepted = []
    for tok in found:
        if any(tok["start"] < a["end"] and tok["end"] > a["start"] for a in accepted):
            continue
        accepted.append(tok)
    accepted.sort(key=lambda t: t["start"])
    return accepted


_CIRCLED = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10}


def find_markers(text):
    """Detects list markers like '1、' '1.' '(1)' '1）' '①' 'A：' that typically precede
    each point in a Chinese maritime-warning point list. Used to tell apart multiple
    distinct polygons described in the same article (numbering restarts for each new area).
    The negative lookahead (?!\\d) is essential: without it, the decimal point inside a
    coordinate value like "31.00" gets mistaken for a "31." list marker.
    NOTE: real warnings very commonly use the FULL-WIDTH "）" (not ASCII ")") -- e.g.
    "1）23-41.31N" -- so both must be recognized or numbering resets get missed entirely."""
    markers = []
    for m in re.finditer(r"(?:^|[^0-9])([1-9]\d?)[、.．)）](?!\d)", text):
        num_str = m.group(1)
        idx = m.end() - len(num_str) - 1
        markers.append({"index": idx, "num": int(num_str)})
    for m in re.finditer(r"[①②③④⑤⑥⑦⑧⑨⑩]", text):
        markers.append({"index": m.start(), "num": _CIRCLED[m.group(0)]})
    # Letter markers, e.g. "A：18-42.60N/110-51.92E、B：19-45.38N..."
    for m in re.finditer(r"\b([A-Za-z])[：:](?=\d)", text, re.ASCII):
        markers.append({"index": m.start(), "num": ord(m.group(1).upper()) - 64})  # A=1, B=2, ...
    markers.sort(key=lambda x: x["index"])
    return markers


# Phrases that typically CLOSE a point list in MSA warning text, e.g.
# "...21-31.17N 108-12.60E两点连线水域以及21-31.00N..." describes TWO separate
# unnumbered 2-point lines back to back -- the "点连线"/"连线水域" phrase between
# them is the only signal that a new, unrelated point list is starting.
_BOUNDARY_RE = re.compile(r"(点连线|连线水域|连线范围|顺次连接|顺序连接|连线内|以及|另在|并在)")


def find_group_boundaries(text):
    return [m.end() for m in _BOUNDARY_RE.finditer(text)]


def group_points(points, markers, boundaries):
    GAP_LIMIT = 400   # chars; a big unrelated stretch of text between points implies unrelated points
    NEAR_LIMIT = 50    # a list marker must be within this many chars before a point to "belong" to it
    # NOTE: no distance-based sanity check here on purpose -- real exercise/firing zones can
    # legitimately span hundreds of km (a real 8-point Hainan warning spans ~400km), so any
    # distance threshold ends up breaking legitimate large areas. Rely on marker/boundary/gap only.
    groups = []
    current = []
    last_marker_num = None
    last_end = None

    for p in points:
        nearest = None
        for mk in markers:
            if mk["index"] <= p["start"] and p["start"] - mk["index"] <= NEAR_LIMIT:
                if nearest is None or mk["index"] > nearest["index"]:
                    nearest = mk
        marker_num = nearest["num"] if nearest else None
        start_new = False
        if current:
            if marker_num is not None and last_marker_num is not None and marker_num <= last_marker_num:
                start_new = True  # numbering restarted -> new polygon
            elif last_end is not None and (p["start"] - last_end) > GAP_LIMIT:
                start_new = True  # big gap of unrelated text -> likely unrelated points
            elif last_end is not None and any(last_end < b <= p["start"] for b in boundaries):
                start_new = True  # a "点连线/连线水域/以及..." style closing phrase sits between these two points
        if start_new:
            groups.append(current)
            current = []
            last_marker_num = None
        current.append(p)
        if marker_num is not None:
            last_marker_num = marker_num
        last_end = p["end"]
    if current:
        groups.append(current)
    return groups


# ------------------------------------------------------------------
# Validity-period extraction ("自...至..." / "YYYY年M月D日HHMM时至...")
# MSA warnings always state a validity window in China Standard Time (UTC+8).
# Real-world text uses many variations, e.g.:
#   "2026年7月24日0000时至2026年7月31日2400时"   (full date both sides)
#   "自7月24日0600时至7月29日1800时"              ("自" prefix, day-only end omits month)
#   "5月28日0730时至29日2350时"                    (cross-day, end has no month)
#   "5月26日，0800时至1600时"                      (single day, COMMA before time)
#   "5月27日至30日，每天0800时至1700时"            (day RANGE, separate daily time range)
#   "3月19日、3月21日，0900时至1500时"             (LIST of discrete dates, shared time)
#   "自11月23日16:00时至12月7日16：00时"           (colon-separated HH:MM, half/full width)
#   "7月28日至31日，在37-46.86N..."                (date range, NO time at all -> full days)
# A single article can state several windows (e.g. one per day/zone), so this
# returns ALL of them; callers typically care about the overall [min start, max end].
# ------------------------------------------------------------------
_TIME_TOK = r"(\d{1,2}[:：]\d{2}|\d{1,4})时(?:(\d{1,2})分)?"
# The closing time of a range sometimes omits the trailing 时 in real MSA text
# (e.g. "自7月29日0300时至1500在..." -- should be "1500时" but isn't), so the
# *final* time token in a range is more lenient than the others.
_TIME_TOK_END = r"(\d{1,2}[:：]\d{2}|\d{1,4})时?(?:(\d{1,2})分)?"

# Pattern A: date and time written back-to-back with no separator
_DIRECT_RE = re.compile(
    r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日" + _TIME_TOK +
    r"\s*至\s*(?:(\d{4})年)?(?:(?:(\d{1,2})月)?(\d{1,2})日)?" + _TIME_TOK_END
)
# Pattern B: date part (single day / day range / list of days) separated from the
# time part by a comma or "每天"/"每日". Lists of discrete days are usually written with
# 、/,/，ばかりでなく、"8月12日和14日"のように「和」（および「及」「以及」）で
# つながれることもある -- これを区切りとして認識しないと、日付部分ごとマッチが失敗し、
# 有効期間が丸ごと未検出("期間不明")になる。
_DATELIST_SEP = r"(?:、|,|，|和|及|以及)"
_DATEPART_RE = re.compile(
    r"(?:自)?\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日"
    r"((?:" + _DATELIST_SEP + r"\s*(?:\d{1,2}月)?\d{1,2}日)*)"
    r"(?:至\s*(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日)?"
)
_TIMEPART_RE = re.compile(
    r"^\s*[，,]?\s*(?:每天|每日)?\s*(\d{1,2}[:：]\d{2}|\d{1,4})时?(?:(\d{1,2})分)?\s*(?:[至\-－]\s*)?" + _TIME_TOK_END
)
_LISTDATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日|(\d{1,2})日")


def _parse_time_token(raw, minute_suffix=None):
    if ':' in raw or '：' in raw:
        h_str, mi_str = raw.replace('：', ':').split(':')
        return int(h_str), int(mi_str)
    if minute_suffix:
        return int(raw), int(minute_suffix)
    if len(raw) <= 2:
        return int(raw), 0
    s = raw.zfill(4)
    return int(s[:-2]), int(s[-2:])


def _mk_date_cst(y, mo, d, h, mi):
    day_offset = 0
    if h >= 24:  # "2400时" means midnight of the next day
        day_offset = 1
        h -= 24
    naive_cst = datetime(y, mo, d, h, mi) + timedelta(days=day_offset)
    utc_instant = naive_cst - timedelta(hours=8)  # CST (UTC+8) -> true UTC instant
    return utc_instant.replace(tzinfo=timezone.utc)  # tag as UTC so isoformat()/JSON export
    # round-trips correctly through JS's new Date() (a naive/offset-less ISO string gets
    # misread as the *browser's local time* instead of UTC, silently shifting every
    # imported validity window by the viewer's UTC offset)


def extract_validity_periods(text):
    periods = []
    used_spans = []
    last_year = datetime.now().year

    for m in _DIRECT_RE.finditer(text):
        y1 = int(m.group(1)) if m.group(1) else last_year
        mo1, d1 = int(m.group(2)), int(m.group(3))
        h1, mi1 = _parse_time_token(m.group(4), m.group(5))
        last_year = y1
        y2 = int(m.group(6)) if m.group(6) else y1
        mo2 = int(m.group(7)) if m.group(7) else mo1
        d2 = int(m.group(8)) if m.group(8) else d1
        h2, mi2 = _parse_time_token(m.group(9), m.group(10))
        try:
            periods.append({
                "start": _mk_date_cst(y1, mo1, d1, h1, mi1),
                "end": _mk_date_cst(y2, mo2, d2, h2, mi2),
                "raw": m.group(0),
            })
            used_spans.append((m.start(), m.end()))
        except ValueError:
            continue  # malformed date (e.g. day 31 in a 30-day month) -- skip rather than crash

    for m in _DATEPART_RE.finditer(text):
        if not m.group(0).strip():
            continue
        dp_start, dp_end = m.start(), m.end()
        if any(dp_start < e and dp_end > s for s, e in used_spans):
            continue  # already matched by pattern A

        y1 = int(m.group(1)) if m.group(1) else last_year
        mo1, d1 = int(m.group(2)), int(m.group(3))
        list_suffix = m.group(4) or ""
        range_year, range_month, range_day = m.group(5), m.group(6), m.group(7)
        last_year = y1

        rest = text[dp_end:dp_end + 40]
        tm = _TIMEPART_RE.match(rest)
        if not tm:
            # No adjacent time clause. Real MSA text sometimes states only a bare date
            # range with no times at all, e.g. "鲁航警602/26，渤海海峡，7月28日至31日，
            # 在37-46.86N ...", meaning "for these entire days" (interpreted as CST
            # 0000 on the start day through 2400 on the end day). Only accept this as a
            # validity window -- rather than some unrelated date mention elsewhere in
            # the text -- when either:
            #  (a) it's immediately followed by the warning's action clause -- almost
            #      always "在..." or "将/拟在...", introducing the affected area
            #      directly, but sometimes instead "以下/如下...", introducing an
            #      itemized list of points (e.g. "8月8日，以下四点连线海域内进行射击
            #      训练：（1）21-24.3N ..."); or
            #  (b) it's immediately preceded by a "活动时间："/"时间：" label, as used
            #      in the numbered-clause notice template some bureaus use (e.g.
            #      "一、活动时间： 2026年6月24日至7月8日。" -- here the actual action
            #      clause is elsewhere in the document, e.g. under a later "二、活动
            #      水域：" heading, not right after the date).
            tail = rest.lstrip("，,、 ")
            before = text[max(0, dp_start - 20):dp_start]
            has_label_before = re.search(r"(?:活动)?时间[:：]\s*$", before)
            if not (re.match(r"(?:将|拟|将于)?(?:在|以下|如下)", tail) or has_label_before):
                continue  # not adjacent to the action clause -- skip, as before
            h1 = mi1 = 0
            h2, mi2 = 24, 0
            full_end = dp_end
        else:
            h1, mi1 = _parse_time_token(tm.group(1), tm.group(2))
            h2, mi2 = _parse_time_token(tm.group(3), tm.group(4))
            full_end = dp_end + tm.end()

        end_year, end_month, end_day = y1, mo1, d1
        if range_day:
            end_year = int(range_year) if range_year else y1
            end_month = int(range_month) if range_month else mo1
            end_day = int(range_day)
        elif list_suffix:
            last_mo, last_day = mo1, d1
            for lm in _LISTDATE_RE.finditer(list_suffix):
                if lm.group(1) and lm.group(2):
                    last_mo, last_day = int(lm.group(1)), int(lm.group(2))
                elif lm.group(3):
                    last_day = int(lm.group(3))
            end_month, end_day = last_mo, last_day

        try:
            periods.append({
                "start": _mk_date_cst(y1, mo1, d1, h1, mi1),
                "end": _mk_date_cst(end_year, end_month, end_day, h2, mi2),
                "raw": text[dp_start:full_end],
            })
            used_spans.append((dp_start, full_end))
        except ValueError:
            continue

    return periods


def summarize_validity(text):
    """Returns {start,end,periods} (start=earliest, end=latest) or None if nothing found."""
    periods = extract_validity_periods(text)
    if not periods:
        return None
    return {
        "start": min(p["start"] for p in periods),
        "end": max(p["end"] for p in periods),
        "periods": periods,
    }


def parse_coordinate_groups(raw_text: str):
    """Returns a list of groups; each group is a list of {lat,lon,raw,fmt} points.
    Multiple groups typically mean the source text describes multiple distinct
    areas/lines (e.g. '禁航区一' and '禁航区二' in the same warning)."""
    text = normalize_symbols(raw_text)
    text = convert_chinese_labels(text)
    tokens = extract_tokens(text)
    markers = find_markers(text)
    boundaries = find_group_boundaries(text)

    points = []
    pending_lat = None
    pending_lon = None
    for tok in tokens:
        if tok["axis"] == "point":
            lat, lon = tok["value"]
            points.append({"lat": lat, "lon": lon, "raw": tok["raw"], "fmt": tok["fmt"], "start": tok["start"], "end": tok["end"]})
            continue
        if tok["axis"] == "lat":
            pending_lat = tok
        elif tok["axis"] == "lon":
            pending_lon = tok
        if pending_lat and pending_lon:
            lat, lon = pending_lat["value"], pending_lon["value"]
            if _valid_lat(lat) and _valid_lon(lon):
                points.append({
                    "lat": lat, "lon": lon, "raw": pending_lat["raw"] + " " + pending_lon["raw"], "fmt": pending_lat["fmt"],
                    "start": min(pending_lat["start"], pending_lon["start"]), "end": max(pending_lat["end"], pending_lon["end"]),
                })
            pending_lat = None
            pending_lon = None
    return group_points(points, markers, boundaries)


def parse_coordinate_text(raw_text: str):
    """Flat point list (back-compat / simple callers)."""
    groups = parse_coordinate_groups(raw_text)
    return [p for g in groups for p in g]



# ------------------------------------------------------------------
# HTTP / HTML 解析
# ------------------------------------------------------------------
def _is_cert_error(exc: BaseException) -> bool:
    """True if an exception (or any of its __cause__ chain) looks like a TLS/SSL
    certificate verification failure, as opposed to a network/timeout/DNS/HTTP error.
    Used to give a specific, actionable diagnostic instead of a generic 'fetch failed'
    when every bureau fails in the same run (see run_once)."""
    seen = set()
    e = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, requests.exceptions.SSLError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            return True
        e = e.__cause__ or e.__context__
    return False


HOMEPAGE_URL = "https://www.msa.gov.cn/"
SAFETY_INDEX_URL = "https://www.msa.gov.cn/html/cnmsa/hxaq/aqxx/index.html"


def warm_up_session(session, insecure=False):
    """A real browser reaches a bureau's warning list by clicking through the site
    (homepage -> 安全信息 menu -> bureau link), which means it arrives carrying session
    cookies and a Referer header. This script instead jumps straight to each bureau's
    URL from a cold start every run, which some WAFs flag as suspicious direct/bot
    traffic and 403 -- even though the exact same URL is fine when fetched with a normal
    browser (confirmed: hitting a bureau's index.jhtml directly still returns 200 from
    other networks/clients, so the URLs themselves are correct and the site isn't down).
    This does one best-effort GET to the homepage to pick up whatever cookies the site
    sets, before any bureau pages are requested. Best-effort only: failure here is not
    fatal, and does not itself guarantee bureau fetches will succeed (this is a
    reasonable mitigation for likely bot-detection, not a confirmed fix for every WAF)."""
    try:
        verify_arg = _CERT_PATH if not insecure else False
        session.get(HOMEPAGE_URL, headers=HEADERS, timeout=15, verify=verify_arg)
    except Exception:  # noqa: BLE001
        pass  # non-fatal: worst case we proceed exactly as before this change


def fetch(session, url, retries=3, delay=1.5, timeout=20, verify=True, expect_url_contains=None, referer=None):
    last_err = None
    verify_arg = _CERT_PATH if verify else False
    req_headers = HEADERS if not referer else {**HEADERS, "Referer": referer}
    for attempt in range(retries):
        try:
            res = session.get(url, headers=req_headers, timeout=timeout, verify=verify_arg)
            res.encoding = res.apparent_encoding or "utf-8"
            if res.status_code != 200:
                raise RuntimeError(f"HTTP {res.status_code}")
            if len(res.text) < 200:
                raise RuntimeError("空の応答")
            if expect_url_contains and expect_url_contains not in res.url:
                # The site's WAF appends a short-lived access token (?hav=...) to article
                # links; once it expires, requesting the article can redirect elsewhere
                # instead of returning 404 -- still HTTP 200, still plenty of bytes, so
                # without this check it looks like a normal successful fetch. Critically,
                # a stale-token redirect isn't guaranteed to land on the site's homepage
                # -- it can land on a DIFFERENT, unrelated article that still matches the
                # general "/hxaq/article/" path shape (confirmed in practice: 鲁航警628/26
                # was once cached with a real-but-wrong-article's coordinates, off the
                # coast of Cyprus, because an earlier version of this check only verified
                # the path pattern, not that it was the SAME article). Callers now pass
                # the canonical (token-stripped) URL of the article they actually asked
                # for, so this verifies we landed on that exact article, not just
                # something shaped like one.
                raise RuntimeError(f"想定外のURLへリダイレクトされました（アクセストークン失効の可能性）: {res.url}")
            return res.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(delay * (attempt + 1))
    raise last_err


def parse_list_html(html, base_url):
    items = []
    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if "/hxaq/article/" not in a["href"]:
                continue
            title = a.get_text(" ", strip=True)
            if not title:
                continue
            href = urljoin(base_url, a["href"])
            # look for a nearby date (same line / parent text)
            parent_text = a.parent.get_text(" ", strip=True) if a.parent else title
            m = re.search(r"(\d{4}-\d{2}-\d{2})", parent_text) or re.search(r"(\d{4}-\d{2}-\d{2})", title)
            date = m.group(1) if m else ""
            title = re.sub(r"\d{4}-\d{2}-\d{2}\s*$", "", title).strip()
            items.append({"title": title, "url": href, "date": date})
    else:
        # regex fallback if bs4 isn't installed
        for m in re.finditer(r'<a[^>]+href="([^"]*\/hxaq\/article\/[^"]*)"[^>]*>([\s\S]*?)</a>', html):
            href = urljoin(base_url, m.group(1).replace("&amp;", "&"))
            text = re.sub(r"<[^>]+>", " ", m.group(2))
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            tail = html[m.end():m.end() + 120]
            dm = re.search(r"(\d{4}-\d{2}-\d{2})", tail) or re.search(r"(\d{4}-\d{2}-\d{2})", text)
            date = dm.group(1) if dm else ""
            title = re.sub(r"\d{4}-\d{2}-\d{2}\s*$", "", text).strip()
            if title:
                items.append({"title": title, "url": href, "date": date})

    seen = set()
    dedup = []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        dedup.append(it)
    return dedup


def build_list_page_url(base_url, page_no):
    """Guesses the URL for page N of a bureau's warning list. This CMS family (jhtml,
    used across all www.msa.gov.cn channel pages) conventionally serves page 1 at
    .../index.jhtml and subsequent pages at .../index_2.jhtml, .../index_3.jhtml, etc.
    This is a best-effort guess -- it has NOT been confirmed against the live site by a
    human. fetch_bureau_items() is defensive about it: if a "page 2" fetch fails or
    comes back with the exact same items as page 1, it stops paging for that bureau
    immediately rather than looping forever or double-counting, and prints what
    happened so it's obvious from the log whether the guessed pattern needs fixing."""
    if page_no <= 1:
        return base_url
    if base_url.endswith("index.jhtml"):
        return base_url[: -len("index.jhtml")] + f"index_{page_no}.jhtml"
    # Unexpected base URL shape (shouldn't happen given BUREAUS above) -- don't guess.
    return None


def fetch_bureau_items(session, bureau, page_count, args):
    """Fetches page 1 (always) and, if page_count > 1, as many further pages as
    build_list_page_url() can construct -- stopping early on a fetch error or on a
    page that yields nothing new (see build_list_page_url's docstring)."""
    all_items = []
    seen_urls = set()
    prev_page_url = SAFETY_INDEX_URL  # referer a real browser would have on page 1
    for page_no in range(1, page_count + 1):
        page_url = build_list_page_url(bureau["url"], page_no)
        if page_url is None:
            if page_no > 1:
                print(f"    {page_no}ページ目: URLを構築できず打ち切り")
            break
        try:
            html = fetch(session, page_url, delay=args.delay, verify=not args.insecure, referer=prev_page_url)
        except Exception as e:  # noqa: BLE001
            if page_no == 1:
                raise  # page 1 failing is a real per-bureau failure, same as before
            print(f"    {page_no}ページ目: 取得失敗のため打ち切り — {e}")
            break
        prev_page_url = page_url
        items = parse_list_html(html, page_url)
        new_urls = {it["url"] for it in items} - seen_urls
        if page_no > 1 and not new_urls:
            print(f"    {page_no}ページ目: 前のページと同じ内容のため打ち切り"
                  f"（ページネーションURLのパターンが実際と違う可能性があります）")
            break
        for it in items:
            if it["url"] not in seen_urls:
                all_items.append(it)
                seen_urls.add(it["url"])
        if page_count > 1:
            print(f"    {page_no}ページ目: {len(items)}件（累計{len(all_items)}件）")
        if page_no < page_count:
            time.sleep(args.delay)
    return all_items


ARTICLE_BODY_SELECTORS = ["#zoom", ".TRS_Editor", ".trs_editor", ".article-content", ".Article_content", "#content", ".detail-content", ".art-con", ".artCon", ".view", ".article"]
FOOTER_MARKERS = ["收藏", "打印本页", "关闭窗口", "下载PDF", "上一篇", "下一篇", "分享到", "责任编辑", "网站声明", "主办单位", "版权所有", "网站地图", "联系我们"]
# Page-chrome that sits INSIDE the same content container as the real body on some
# bureaus' pages (confirmed in practice on 琼航警143/26): a "HH:MM [字体：大 中 小] 分享到："
# toolbar row directly before the actual text. Because it's inside the matched container,
# the ARTICLE_BODY_SELECTORS path used to return it verbatim -- only the structural
# fallback path trimmed anything. Also strips the warning's own reference number (e.g.
# "琼航警143/26，") when it's repeated as a literal prefix of the body -- that number is
# already carried separately in the title, so keeping it here just duplicates it.
_HEADER_CHROME_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*(?:\[字体[：:][^\]]*\]\s*)?(?:分享到[：:]\s*)?")
_WARNING_ID_PREFIX_RE = re.compile(r"^[\u4e00-\u9fff]{1,3}航警\d+/\d+[，,]\s*")


def _strip_leading_chrome(text):
    """Removes just the small, fixed-position leading toolbar row and duplicated
    warning-ID prefix -- never the footer trim, so this is safe to use even as a
    last-resort fallback where a footer-marker trim already collapsed the text too far."""
    text = _HEADER_CHROME_RE.sub("", text)
    text = _WARNING_ID_PREFIX_RE.sub("", text)
    return text


def _strip_body_chrome(text):
    """Removes the leading toolbar row and duplicated warning-ID prefix, then trims at
    the first footer marker. Applied to whichever extraction path produced the text
    (selector match or structural heuristic) so both get the same cleanup instead of
    only the structural-heuristic path."""
    text = _strip_leading_chrome(text)
    end_idx = len(text)
    for marker in FOOTER_MARKERS:
        idx = text.find(marker)
        if idx != -1 and idx < end_idx:
            end_idx = idx
    cleaned = text[:end_idx].strip()
    return cleaned if len(cleaned) > 20 else text.strip()


def extract_body_text(html):
    """Legacy whole-page text extraction (kept for callers that genuinely want it)."""
    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)
    return re.sub(r"<[^>]+>", " ", html)


def extract_article_body(html):
    """Narrows down to just the actual warning text, the same way the browser tool does:
    MSA article pages (and most Chinese gov-site CMS templates) wrap the real body in a
    specific content container, surrounded by navigation/sidebar/footer boilerplate --
    bureau contact-number lists, related-article links, copyright notices, etc. Blindly
    using the whole page's text lets that boilerplate pollute coordinate and list-marker
    detection (e.g. a numbered "1、海口海事局电话：...  2、三亚海事局电话：..." contact list)."""
    if not HAVE_BS4:
        return extract_body_text(html)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # 1) try a known content-container selector -- most precise when it matches
    for sel in ARTICLE_BODY_SELECTORS:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if len(t) > 20:
                return _strip_body_chrome(t)

    # 2) fall back to a structural heuristic: body sits between the "发布时间：YYYY-MM-DD"
    #    metadata line and the page-action footer (收藏/打印本页/关闭窗口/...)
    text = soup.get_text(" ", strip=True)
    pub = re.search(r"发布时间[：:]\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}[^\s]{0,20}", text)
    if pub:
        text = text[pub.end():]
    body = _strip_body_chrome(text)

    # 3) if the heuristic collapsed to nothing usable, fall back to the raw full-page text
    return body if len(body) > 20 else _strip_leading_chrome(text.strip())


def match_military(title, keywords):
    low = title.lower()
    return any(k in title or k.lower() in low for k in keywords)


# Matches a bare warning reference number like "粤航警0461/26" or "琼航警143/26" --
# same shape as _WARNING_ID_PREFIX_RE above, but unanchored so it can be found anywhere
# in a string, not just stripped off the front.
_WARNING_ID_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{1,3}航警\d+/\d+")
_CANCEL_KEYWORD_RE = re.compile(r"撤销|撤消")


def is_cancellation_notice(title):
    """A notice title like "撤销粤航警461/26—粤航警523/26" revokes an earlier warning
    ahead of its stated validity window ending. These need their body fetched even when
    they don't themselves match any military keyword, purely so extract_cancelled_ids()
    can check the body for the target ID when (per the site's own inconsistency) it's
    only mentioned there and not in the title."""
    return bool(_CANCEL_KEYWORD_RE.search(title or ""))


def extract_own_id(title):
    """Pulls this item's own warning reference number out of its title, e.g.
    "南海军事训练警告—粤航警0461/26" -> "粤航警0461/26". MSA titles put the reference
    number after the LAST em/en-dash when there is one (shortLabel() in geoplot-mil.html
    relies on the same convention) -- important for cancellation titles specifically,
    which look like "撤销{target}—{this notice's own id}": naively taking the first ID
    anywhere in the title would grab the target instead of this notice's own number."""
    parts = re.split(r"[—–]", title or "")
    tail = parts[-1] if len(parts) > 1 else (title or "")
    m = _WARNING_ID_TOKEN_RE.search(tail)
    return m.group(0) if m else None


def extract_cancelled_ids(title, body, own_id=None):
    """Finds warning ID(s) that a "撤销"/"撤消" notice revokes, by scanning the short
    clause right after each occurrence of that keyword (up to the next 。／；／newline)
    in the title and/or body -- not the whole text, so an unrelated ID mentioned
    elsewhere in a long body doesn't get swept in. own_id is excluded so a title's own
    trailing "—{own id}" (see extract_own_id) or a body that opens with its own
    "{own id}，...，撤销{target}。" doesn't self-match as also being cancelled by itself.
    Handles more than one target (e.g. "撤销511/26、512/26") since it collects every ID
    token found in the clause, not just the first.
    """
    found = set()
    for text in (title, body):
        if not text:
            continue
        for m in _CANCEL_KEYWORD_RE.finditer(text):
            clause = text[m.end(): m.end() + 200]
            stop = re.search(r"[。；\n]", clause)
            if stop:
                clause = clause[: stop.start()]
            for wid in _WARNING_ID_TOKEN_RE.findall(clause):
                if wid != own_id:
                    found.add(wid)
    return found


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_chinese_title(title):
    """MSA warnings are always posted as two separate list entries -- one Chinese, one
    English -- for the same underlying event with identical coordinates. We only need
    one; keep the Chinese entry and drop the English-only duplicate."""
    return bool(_CJK_RE.search(title))


# ------------------------------------------------------------------
# 状態管理（累積ストア）
# ------------------------------------------------------------------
def load_state(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(path, state):
    # Defensive: run_once() already creates the output directory at startup, but a run
    # can take several minutes, and in practice the directory has occasionally vanished
    # by the time we get here (antivirus quarantine, a cloud-sync client -- OneDrive
    # etc. -- transiently renaming/recreating a synced folder, and so on). Re-creating it
    # here is cheap and turns that into a non-event instead of a crash.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        # sort_keys=True matters here beyond just readability: without it, dict
        # insertion order means every newly-discovered article gets appended at the
        # very end of the file. Two runs that each add a *different* new article then
        # both edit the same trailing lines (right before the closing brace), which
        # git sees as a real conflict even though the additions never actually
        # overlapped -- this is what caused the "CONFLICT (content): Merge conflict in
        # msa_out/state.json" failures in the GitHub Actions workflow. Sorting by key
        # (article URL) spreads new entries out across the file by where their key
        # falls alphabetically, so two runs adding different articles usually touch
        # different lines and rebase/merge cleanly.
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _order_ring_points(points):
    """Reorders >=3 points into a simple, non-self-intersecting polygon ring by
    sorting them by angle around their centroid.

    MSA notices usually list a zone's corners already going around the perimeter
    (typically clockwise: NW->NE->SE->SW), in which case this is a no-op (up to
    starting point/direction, which doesn't change the resulting shape). But some
    notices list corners in reading order instead -- e.g. 辽航警345/26 gives
    NW, NE, SW, SE (top row then bottom row) -- and connecting THAT literally
    produces a self-intersecting "bowtie" instead of the intended rectangle, since
    the NE->SW segment cuts across the SE->NW segment.

    Angle-sort-around-centroid always reconstructs the correct simple polygon for
    any convex point set, which covers essentially every real-world MSA exclusion
    zone (rectangles/quadrilaterals). The tradeoff is it would draw a genuinely
    concave (non-convex) shape wrong -- accepted deliberately, since those appear
    to be extremely rare in this data source and a bowtie is a far more visible/
    confusing failure mode than a slightly-off concave corner would be.
    """
    lat0 = sum(p["lat"] for p in points) / len(points)
    lon0 = sum(p["lon"] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p["lat"] - lat0, p["lon"] - lon0))


def build_geojson(state):
    features = []
    # Sorted (by URL key), not raw dict-insertion order, for the same reason
    # save_state() now uses sort_keys=True: a stable, content-independent ordering
    # means two runs adding different new articles touch different parts of the file
    # instead of both appending at the very end, which avoids spurious git merge
    # conflicts on military.geojson (see save_state()'s comment for the full story).
    for key, item in sorted(state.items()):
        if not item.get("military"):
            continue
        if item.get("own_id") in MANUALLY_EXCLUDED_OWN_IDS:
            continue
        groups = item.get("groups") or []
        validity = item.get("validity")
        # item["url"] (the freshest access-token'd link) is what actually opens in a
        # browser -- `key` is only the token-independent identity used internally, see
        # _canonical_article_key. Emitting `key` here would produce a link that 404s/
        # redirects since it's missing the required ?hav=... token.
        base_props = {
            "title": item["title"], "url": item.get("url", key), "date": item.get("date", ""),
            "bureau": item.get("bureau", ""), "group_count": len(groups),
        }
        if item.get("detail_gave_up"):
            base_props["detail_gave_up"] = True
        if item.get("raw_text"):
            base_props["raw_text"] = item["raw_text"]
        if validity:
            base_props["valid_start"] = validity["start"]
            base_props["valid_end"] = validity["end"]
            if validity.get("raw"):
                base_props["valid_raw"] = validity["raw"]
        if item.get("revoked"):
            # A later "撤销/撤消" notice named this warning's own_id as a target -- it's
            # no longer in effect regardless of what valid_end above says. See
            # extract_cancelled_ids() / the revoked_by cross-reference pass in run_once().
            base_props["revoked"] = True
            if item.get("revoked_by"):
                base_props["revoked_by"] = item["revoked_by"]

        if not groups:
            # No coordinates could be extracted (parse gap, or the warning genuinely has
            # none) -- still emit the item so its title/validity/link show up in the
            # results list instead of silently vanishing from the output entirely.
            features.append({
                "type": "Feature",
                "properties": {**base_props, "kind": "no_coords"},
                "geometry": None,
            })
            continue

        for gi, grp in enumerate(groups):
            props = {**base_props, "group_index": gi + 1}
            if len(grp) == 1:
                p = grp[0]
                features.append({
                    "type": "Feature",
                    "properties": {**props, "kind": "point"},
                    "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                })
            elif len(grp) == 2:
                features.append({
                    "type": "Feature",
                    "properties": {**props, "kind": "line"},
                    "geometry": {"type": "LineString", "coordinates": [[p["lon"], p["lat"]] for p in grp]},
                })
            else:
                ring = [[p["lon"], p["lat"]] for p in _order_ring_points(grp)]
                ring.append(ring[0])  # close the ring
                features.append({
                    "type": "Feature",
                    "properties": {**props, "kind": "area"},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                })
    return {"type": "FeatureCollection", "features": features}


# ------------------------------------------------------------------
# メインの巡回ロジック
# ------------------------------------------------------------------
def _canonical_article_key(url):
    """Article identity, ignoring the site's rotating WAF access token (?hav=...). The
    token changes every time an article is re-listed, so using the raw URL as the
    state.json key (as earlier versions of this script did) makes the same underlying
    warning look like a brand-new article on every run once its token rotates -- and
    once its token expires, that stale key can never be successfully re-fetched again.
    Keying by the token-stripped path instead gives each warning one stable, permanent
    identity that we can keep refreshing with whatever the newest valid token is."""
    return url.split("?", 1)[0]


def migrate_state_keys(state):
    """One-time migration for state.json files written before state was keyed by
    _canonical_article_key(): merges any duplicate entries that only differ by their
    (now-stripped) access-token query string into a single entry, keeping whichever
    duplicate has the most useful cached data (coordinates > validity > most recent)."""
    groups = {}
    for key, item in state.items():
        canon = _canonical_article_key(item.get("url", key))
        groups.setdefault(canon, []).append((key, item))
    if all(len(v) == 1 and k == v[0][0] for k, v in groups.items()):
        return state, 0  # already canonical; nothing to migrate

    def score(kv):
        _, it = kv
        return (bool(it.get("groups")), bool(it.get("validity")), it.get("first_seen", ""))

    merged = {}
    dupes_removed = 0
    for canon, entries in groups.items():
        if len(entries) > 1:
            dupes_removed += len(entries) - 1
        _, best_item = max(entries, key=score)
        merged[canon] = best_item
    return merged, dupes_removed


def run_once(args, keywords):
    os.makedirs(args.out_dir, exist_ok=True)
    state_path = os.path.join(args.out_dir, "state.json")
    state = load_state(state_path)

    state, migrated_dupes = migrate_state_keys(state)
    if migrated_dupes:
        print(f"  クリーンアップ: アクセストークン違いによる重複{migrated_dupes}件を統合しました")

    # one-time cleanup: earlier versions of this script kept both the Chinese and
    # English list entries for the same warning; purge any English-only leftovers.
    english_leftovers = [u for u, it in state.items() if not is_chinese_title(it.get("title", ""))]
    if english_leftovers:
        for u in english_leftovers:
            del state[u]
        print(f"  クリーンアップ: 過去に保存された英語版の重複{len(english_leftovers)}件を削除しました")

    # Backfill own_id/cancels for entries saved before this feature existed -- both are
    # derived from the title alone (no network needed), so this is cheap enough to just
    # redo every run rather than track yet another version stamp for it.
    for it in state.values():
        it["own_id"] = extract_own_id(it.get("title", ""))
        title_cancels = extract_cancelled_ids(it.get("title", ""), None, it.get("own_id"))
        # Keep anything already found from a body fetch (see the detail-fetch loop
        # below) -- this backfill only ever ADDS title-derived IDs, never removes body-
        # derived ones a previous run already found.
        it["cancels"] = sorted(set(it.get("cancels", [])) | title_cancels)

    wanted_ids = set(args.bureaus.split(",")) if args.bureaus else None
    bureaus = [b for b in BUREAUS if not wanted_ids or b["id"] in wanted_ids]

    is_first_run = not os.path.exists(state_path)
    if args.pages is not None:
        # User explicitly passed --pages (e.g. a one-off deep re-scan) -- this always
        # wins, first run or not. Previously this branch didn't exist and a first run
        # (no state.json yet) would silently ignore an explicit --pages in favor of
        # --first-run-pages, which was surprising (see HANDOFF.md).
        page_count = args.pages
    elif is_first_run:
        page_count = args.first_run_pages
    else:
        page_count = 1
    page_count = max(1, page_count)

    session = requests.Session()
    warm_up_session(session, insecure=args.insecure)
    new_count = 0
    new_military = 0
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    if page_count > 1:
        reason = "初回実行のため" if is_first_run else "--pages指定のため"
        page_note = f"（{reason}各局{page_count}ページ目まで遡って取得）"
    else:
        page_note = ""
    print(f"[{ts}] 巡回開始: {len(bureaus)}局{page_note}")
    bureau_fail_count = 0
    cert_fail_count = 0
    forbidden_fail_count = 0
    for b in bureaus:
        stuck_here = [k for k, it in state.items()
                      if it.get("military") and not it.get("detail_fetched")
                      and it.get("bureau") == b["name"]
                      and STALE_DETAIL_RETRY_THRESHOLD <= it.get("detail_fail_count", 0) < DETAIL_GIVE_UP_THRESHOLD]
        bureau_page_count = max(page_count, STALE_RESCAN_PAGES) if stuck_here else page_count
        if stuck_here and bureau_page_count > page_count:
            print(f"  {b['name']}: 本文取得が{STALE_DETAIL_RETRY_THRESHOLD}回以上失敗している項目が"
                  f"{len(stuck_here)}件あるため、今回は{bureau_page_count}ページ目まで遡って"
                  f"再取得を試みます（アクセストークン更新のため）")
        try:
            items = fetch_bureau_items(session, b, bureau_page_count, args)
            added = 0
            skipped_english = 0
            for it in items:
                key = _canonical_article_key(it["url"])
                if key in state:
                    # Same article, freshly re-listed: its WAF access token may have
                    # rotated since we last saw it, so keep the URL up to date. This is
                    # what lets a previously-failed detail fetch (stale/expired token)
                    # succeed on a later run once the article is re-listed again.
                    state[key]["url"] = it["url"]
                    continue
                if not is_chinese_title(it["title"]):
                    skipped_english += 1
                    continue
                military = match_military(it["title"], keywords)
                state[key] = {
                    **it, "bureau": b["name"], "military": military,
                    "points": [], "groups": [], "detail_fetched": False, "first_seen": ts,
                }
                added += 1
                new_count += 1
                if military:
                    new_military += 1
            skip_note = f"（英語版{skipped_english}件は除外）" if skipped_english else ""
            print(f"  {b['name']}: {len(items)}件中 新規{added}件{skip_note}")
        except Exception as e:  # noqa: BLE001
            bureau_fail_count += 1
            if _is_cert_error(e):
                cert_fail_count += 1
            if "HTTP 403" in str(e):
                forbidden_fail_count += 1
            print(f"  {b['name']}: 取得失敗 — {e}")
        time.sleep(args.delay)

    if bureaus and bureau_fail_count == len(bureaus):
        # Every bureau's list page failed to fetch this run. build_geojson() below will
        # still succeed by falling back to whatever was already cached in state.json, so
        # without this warning it's easy to mistake "the map still shows pins" for "the
        # scrape worked" when in fact zero fresh data was retrieved this run.
        print(f"  ⚠ 今回は{len(bureaus)}局すべての一覧取得に失敗しました。"
              f"このあと出力される military.geojson は新規取得データではなく、"
              f"前回までの state.json のキャッシュをそのまま書き出したものです。")
        if cert_fail_count == bureau_fail_count:
            print("  ⚠ 原因はSSL証明書検証エラー（CERTIFICATE_VERIFY_FAILED）のようです。"
                  "多くの場合、法人PCのHTTPS検査（SSLインスペクション）機能や、"
                  "一部のセキュリティソフト（ウイルス対策ソフトのWeb保護機能等）が原因です。"
                  "次のいずれかを試してください：")
            print("    1) pip install pip-system-certs を実行してから、もう一度このスクリプトを実行する")
            print("       （OSの証明書ストアをPythonのrequestsに反映させます）")
            print("    2) セキュリティソフトのHTTPS/SSLスキャン機能を一時的に無効化して切り分ける")
            print("    3) 診断目的に限り --insecure オプションを付けて一度実行してみる"
                  "（証明書検証の問題かどうかの確認用。常用は非推奨）")
        elif forbidden_fail_count == bureau_fail_count:
            print("  ⚠ 原因はHTTP 403（アクセス拒否）のようです。サイト自体は稼働しており、"
                  "同じURLがブラウザでは開けるなら、サイト障害やURLミスではなく、"
                  "このPC/ネットワークからの自動アクセスがWAF（不正アクセス対策）に"
                  "弾かれている可能性が高いです。本スクリプトはトップページへの事前アクセスで"
                  "Cookieを取得し、Refererヘッダーも付与するようにしていますが、"
                  "それでも解消しない場合は次を試してください：")
            print("    1) しばらく時間を空けてから再実行する（一時的なレート制限の可能性）")
            print("    2) 別のネットワーク（自宅回線・モバイル回線等）から実行してみて、"
                  "同じPCでもネットワークを変えると成功するか確認する")
            print("    3) --delay を増やして実行間隔を空ける（例: --delay 3）")

    def _cancellation_worth_fetching(it):
        """Most "撤销/撤消" notices don't concern a military warning at all, and even
        the ones that do usually name their target right in the title (the run_once
        backfill loop above already extracts that -- no fetch needed for it). The ONLY
        reason to spend a request on a cancellation notice's body is the case
        is_cancellation_notice()'s docstring warns about: the title names NO target id
        at all, so the body is the only place left to look. That's expected to be rare,
        which is the whole point -- this intentionally does NOT fetch a notice just
        because its title-named target happens to be a warning we're tracking, since
        the title alone already gave us that target for free."""
        title = it.get("title", "")
        if not is_cancellation_notice(title):
            return False
        return not extract_cancelled_ids(title, None, it.get("own_id"))

    # fetch article detail for military candidates missing coordinates, or whose cached
    # result predates the current extraction logic (see DETAIL_EXTRACTION_VERSION above).
    # Also fetch cancellation notices per _cancellation_worth_fetching() above, even when
    # they're not themselves a military match.
    all_to_fetch = [u for u, it in state.items() if
                     (it.get("military") or _cancellation_worth_fetching(it)) and
                     (not it.get("detail_fetched") or it.get("extraction_version") != DETAIL_EXTRACTION_VERSION)
                     and it.get("detail_fail_count", 0) < DETAIL_GIVE_UP_THRESHOLD]
    to_fetch = all_to_fetch[: args.max_detail_per_run]
    if len(all_to_fetch) > len(to_fetch):
        print(f"  注意: 本文取得の対象が{len(all_to_fetch)}件あり、上限--max-detail-per-run={args.max_detail_per_run}"
              f"により今回は{len(to_fetch)}件のみ処理します。残り{len(all_to_fetch)-len(to_fetch)}件は次回の実行に持ち越されます"
              f"（bureau一覧の後ろの方の局ほど後回しになりがちなので、気になる場合は--max-detail-per-runを増やすか、"
              f"--bureausで対象を絞ってください）。")
    fetched_ok = 0
    for key in to_fetch:
        fetch_url = state[key]["url"]
        try:
            # Pass the exact canonical (token-stripped) URL we're requesting -- not just
            # a generic "/hxaq/article/" path shape -- so fetch() can tell a stale-token
            # redirect to a DIFFERENT real article apart from actually landing on this one.
            html = fetch(session, fetch_url, delay=args.delay, verify=not args.insecure,
                          expect_url_contains=_canonical_article_key(fetch_url))
            text = extract_article_body(html)
            groups = parse_coordinate_groups(text)
            points = [p for g in groups for p in g]
            validity = summarize_validity(text)
            state[key]["groups"] = groups
            state[key]["points"] = points
            # Full extracted body text, kept verbatim for the "リスト表示" mode in
            # geoplot-mil.html (shows title + full original text per item, not just the
            # parsed coordinates/validity). Not used by any coordinate/date parsing.
            state[key]["raw_text"] = text
            state[key]["validity"] = (
                {"start": validity["start"].isoformat(), "end": validity["end"].isoformat(),
                 "period_count": len(validity["periods"]),
                 "raw": [p["raw"] for p in validity["periods"]]}
                if validity else None
            )
            state[key]["detail_fetched"] = True
            state[key]["detail_fail_count"] = 0
            state[key]["detail_gave_up"] = False
            state[key]["extraction_version"] = DETAIL_EXTRACTION_VERSION
            # Union with (not overwrite) whatever title-only pass already found -- see
            # the backfill loop above -- since the title and body can each mention IDs
            # the other doesn't.
            body_cancels = extract_cancelled_ids(state[key]["title"], text, state[key].get("own_id"))
            state[key]["cancels"] = sorted(set(state[key].get("cancels", [])) | body_cancels)
            fetched_ok += 1
            validity_note = f"、有効期限 {validity['end'].isoformat()}Z" if validity else "、有効期間は検出できず"
            cancel_note = f"、{'／'.join(state[key]['cancels'])}を撤回" if state[key]["cancels"] else ""
            print(f"  本文取得OK: {state[key]['title'][:40]} → 座標{len(points)}点（{len(groups)}エリア/経路）{validity_note}{cancel_note}")
        except Exception as e:  # noqa: BLE001
            # Left as detail_fetched=False (or a stale extraction_version) on purpose --
            # this article will be retried on a later run, hopefully once it's been
            # re-listed with a fresh access token (see _canonical_article_key above).
            state[key]["detail_fail_count"] = state[key].get("detail_fail_count", 0) + 1
            if state[key]["detail_fail_count"] >= DETAIL_GIVE_UP_THRESHOLD:
                state[key]["detail_gave_up"] = True
                print(f"  本文取得失敗: {state[key]['title'][:40]} — {e}"
                      f"（{DETAIL_GIVE_UP_THRESHOLD}回失敗のため以後リトライを停止します。"
                      f"サイト側でリンクが失効した可能性があります）")
            else:
                note = ""
                if state[key]["detail_fail_count"] == STALE_DETAIL_RETRY_THRESHOLD:
                    note = f"（{STALE_DETAIL_RETRY_THRESHOLD}回連続失敗のため、次回このbureauをページ深くまで再取得します）"
                print(f"  本文取得失敗: {state[key]['title'][:40]} — {e}{note}")
        time.sleep(args.delay)

    # Cross-reference every item's "cancels" list against every other item's "own_id" --
    # done over the WHOLE state (not just what changed this run) since a revocation
    # notice and the warning it revokes can be scraped in either order, and a warning
    # already sitting in state needs to pick up a revocation posted after it was first
    # fetched. Whichever item(s) list a given own_id get recorded so the UI can show
    # what revoked it, not just that it was revoked.
    revoked_by = {}
    for it in state.values():
        revoker = it.get("own_id") or it.get("title", "")
        for target_id in it.get("cancels", []):
            revoked_by.setdefault(target_id, []).append(revoker)
    for it in state.values():
        oid = it.get("own_id")
        if oid and oid in revoked_by:
            it["revoked"] = True
            it["revoked_by"] = revoked_by[oid]
        else:
            it["revoked"] = False
            it.pop("revoked_by", None)

    save_state(state_path, state)

    geojson = build_geojson(state)
    with open(os.path.join(args.out_dir, "military.geojson"), "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False, indent=2)

    latest_military = [it for it in state.values() if it.get("military") and it.get("first_seen") == ts]
    with open(os.path.join(args.out_dir, "military_latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest_military, f, ensure_ascii=False, indent=2)

    stale_note = ""
    if bureaus and bureau_fail_count == len(bureaus):
        stale_note = "（※新規取得0局のため、上記の地点数は前回までのキャッシュです。上の⚠を参照）"
    print(f"[{ts}] 完了: 新規{new_count}件（軍事候補{new_military}件） / 本文取得{fetched_ok}件 / "
          f"軍事関連の座標プロット可能地点 {len(geojson['features'])}件{stale_note}")
    print(f"  → {os.path.join(args.out_dir, 'military.geojson')} をGeoPlot MILにインポートできます")


def main():
    ap = argparse.ArgumentParser(description="中国海事局 航行警告 軍事関連抽出スクレイパー")
    ap.add_argument("--once", action="store_true", help="1回だけ実行（既定）")
    ap.add_argument("--loop", action="store_true", help="繰り返し実行する")
    ap.add_argument("--interval", type=float, default=15, help="ループ時の実行間隔（分）既定15分")
    ap.add_argument("--out-dir", default="./msa_out", help="出力先ディレクトリ")
    ap.add_argument("--bureaus", default="", help="対象海事局IDのカンマ区切り（省略で全局）例: gd,fj,zj")
    ap.add_argument("--keywords-file", default="", help="キーワード一覧テキストファイル（1行1語、省略で既定値を使用）")
    ap.add_argument("--delay", type=float, default=1.5, help="リクエスト間隔（秒）既定1.5秒。短くしすぎないこと")
    ap.add_argument("--max-detail-per-run", type=int, default=200, help="1回の実行で本文取得する上限件数（大きめが安全。小さくすると後ろの局が後回しになりがち）")
    ap.add_argument("--pages", type=int, default=None,
                     help="各海事局の一覧を何ページ読むか（既定は未指定=初回実行なら--first-run-pages、"
                          "2回目以降は1ページ。明示的に指定すると初回/2回目以降を問わずそちらを優先）")
    ap.add_argument("--first-run-pages", type=int, default=3, help="state.jsonがまだ無い初回実行時に各海事局の一覧を何ページ読むか（既定3ページ。多めに遡って取りこぼしを減らす）")
    ap.add_argument("--insecure", action="store_true",
                     help="SSL証明書検証を無効化する（診断用）。ウイルス対策ソフトや企業プロキシによる"
                          "証明書エラーの切り分けにのみ使用し、常用しないこと。")
    args = ap.parse_args()

    if args.insecure:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        print("!! --insecure が指定されています。SSL証明書の検証をスキップして通信します。")
        print("!! これは通信の安全性を下げる設定です。原因切り分け後は外して運用してください。")

    keywords = DEFAULT_KEYWORDS
    if args.keywords_file:
        with open(args.keywords_file, "r", encoding="utf-8") as f:
            keywords = [line.strip() for line in f if line.strip()]

    if args.loop:
        print(f"ループモード: {args.interval}分間隔。Ctrl+Cで停止します。")
        try:
            while True:
                try:
                    run_once(args, keywords)
                except Exception as e:  # noqa: BLE001
                    # Anything unexpected here (a transient filesystem hiccup, a network
                    # library raising something fetch()'s own retry loop didn't catch,
                    # etc.) must not kill the whole unattended loop -- that would silently
                    # stop all future scraping until someone happens to notice the window
                    # closed. Log it clearly and just try again next cycle.
                    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                    print(f"[{ts}] !! 今回の巡回中に予期しないエラーが発生しました。次回の巡回は"
                          f"予定通り続行します（ループ自体は停止していません）: {type(e).__name__}: {e}")
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            print("停止しました。")
    else:
        run_once(args, keywords)


if __name__ == "__main__":
    main()
