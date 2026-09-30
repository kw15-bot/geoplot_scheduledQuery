#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
notam_cn_collect.py
===================
FAA NMS-API から「中国・香港・マカオ・台湾」のNOTAMだけを10分おき程度で収集し、
GeoJSON化する。失効・取消したNOTAMは削除せず月別のArchiveへ蓄積する。

出力（--out-dir、既定 ./notam_out）:
  state_active.json            有効/未来のNOTAM（と取消通知）。真実の源（id -> レコード）
  notam_cn.geojson             ビューア用。state_active から毎回作り直す
  archive/notam_YYYY-MM.geojson  失効・取消したNOTAM（即時移行）。失効月ごとに追記のみ（idで重複排除）
  meta.json                    最終成功時刻など。データ変化時と数時間おきにだけ更新（無駄コミット防止）

対象の判定: icaoLocation か affectedFir の「4文字トークン」(A)項が複数地点なら空白区切りで分割)の先頭2文字が下記のもの。
  CN: ZB ZG ZH ZJ ZL ZP ZS ZU ZW ZY (+ZX=ZXXX複数FIR仮コード) / HK: VH / MO: VM / TW: RC
  （米国ARTCCの3文字コード ZBW/ZHU/ZJX/ZLA/ZSE/ZUA 等との衝突を避けるため4文字に限る）

手元での試し方（gitもFAAも不要）:
  # 1) プローブの出力 or 自作JSONをそのまま食わせる
  python notam_cn_collect.py --fixture probe_out/cn_sample.json --out-dir ./notam_out_local
  # 2) 時刻を進めて失効・Archive移動を確認する
  python notam_cn_collect.py --fixture probe_out/cn_sample.json --out-dir ./notam_out_local \
         --now 2026-10-01T00:00:00Z
  # 3) 実APIに接続して差分取得（NMS_CLIENT_ID / NMS_CLIENT_SECRET / NMS_ENV が必要）
  python notam_cn_collect.py --out-dir ./notam_out_local --dry-run     # 何も書かず結果だけ表示
  # 4) 初回の全件投入（差分APIは24時間分しか取れないため）
  #    il = GET /v1/notams?classification=<分類> を単独指定→GeoJSON一括ファイル(圧縮)へ。
  #    ※専用の /v1/notams/il は仕様上 AIXM(SOAP封筒)専用なので使わない。
  python notam_cn_collect.py --bootstrap il --out-dir ./notam_out_local
  python notam_cn_collect.py --bootstrap locations --out-dir ./notam_out_local   # ilが使えない場合の代替

終了コード: 0=成功 / 2=API取得失敗（Actionsを赤くして気づけるようにする。last_successは進めない）

依存: Python 3.9+ の標準ライブラリのみ。
"""
import argparse
import base64
import collections
import datetime as dt
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import notam_route_segments
import aip_chart_areas  # AIPの図の点で区域の境界を線にする(HANDOFF_NOTAM.md §24)

SCHEMA_VERSION = 1
HOSTS = {
    "fit": "https://api-fit.cgifederal-aim.com",
    "staging": "https://api-staging.cgifederal-aim.com",
    "prod": "https://api-nms.aim.faa.gov",
}
# ICAO地名指標の先頭2文字 -> グループ（中国本土/香港/マカオ/台湾は国・地域全体を緩く拾う）
AREA_BY_PREFIX = {
    **{p: "CN" for p in ("ZB", "ZG", "ZH", "ZJ", "ZL", "ZP", "ZS", "ZU", "ZW", "ZY")},
    "ZX": "CN",                       # ZXXX = 複数FIRにまたがるNOTAMのQ項FIR欄（実データで確認）
    "VH": "HK", "VM": "MO", "RC": "TW",
}
# 周辺国の特定FIRのみを参考表示対象に含める（2026-09-27、ユーザー合意）。
# これらはプレフィックスで拾うと国全体（例: K=米国全土, RJ=日本全国のFIR）まで
# 混入してしまうため、4文字の完全一致でのみマッチさせる（AREA_BY_PREFIXとは別枠）。
#   RPHI: マニラFIR(フィリピン)  KZAK: オークランド洋上管制(米国)
# 2026-09-28: RJJJ(福岡)・RKRR(仁川)・ZKKP(平壌)は中国と関係の薄いNOTAMが大半のため対象から外した
# (ユーザー判断。収集済みの分も state・Archive から削除。drop_untargeted()参照)。
AREA_BY_EXACT = {"RPHI": "REF", "KZAK": "REF"}
AREA_LABEL = {"CN": "中国本土", "HK": "香港", "MO": "マカオ", "TW": "台湾", "REF": "周辺国FIR(参考)"}

MAX_DELTA_WINDOW = dt.timedelta(hours=23, minutes=30)   # 仕様上の上限は24時間
OVERLAP = dt.timedelta(minutes=10)                      # 取りこぼし防止の重ね取り
COLD_START_WINDOW = dt.timedelta(hours=6)
CIRCLE_MAX_NM = 250       # これを超える半径(FIR全体を示す999など)は円にせず点にする
CIRCLE_POINTS = 64
HTTP_TIMEOUT = 60


class ApiError(Exception):
    pass


# ----------------------------------------------------------------------------- 時刻・座標
def parse_dt(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00") if t else None


_Q_COORD = re.compile(r"^(\d{2})(\d{2})([NS])(\d{3})(\d{2})([EW])$")


def parse_qline_coord(s):
    """'3900N11600E' -> (lat, lon)。度分のみ。失敗は None。"""
    m = _Q_COORD.match((s or "").strip().upper())
    if not m:
        return None
    lat = int(m[1]) + int(m[2]) / 60.0
    lon = int(m[4]) + int(m[5]) / 60.0
    if m[3] == "S":
        lat = -lat
    if m[6] == "W":
        lon = -lon
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon


def circle_ring(lat, lon, r_nm, n=CIRCLE_POINTS):
    """等長方形近似の円（既存MSA側の円展開と同じ考え方）。GeoJSON順 [lon, lat]、閉じたリング。"""
    r_km = r_nm * 1.852
    dlat = r_km / 111.32
    dlon = r_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    ring = [[round(lon + dlon * math.sin(2 * math.pi * i / n), 6),
             round(lat + dlat * math.cos(2 * math.pi * i / n), 6)] for i in range(n)]
    ring.append(ring[0])
    return ring


# ----------------------------------------------------------------------------- 分類
def target_area(n):
    """icaoLocation / affectedFir を空白等で分割し、4文字のトークンのどれかが対象なら該当。
    実データでは A) が複数地点（例 'ZGZU ZSHA'）になりうるため。
    先に完全一致(AREA_BY_EXACT、周辺国の特定FIRのみ)を確認し、無ければ従来の
    2文字プレフィックス(AREA_BY_PREFIX、中国本土/香港/マカオ/台湾を国単位で緩く拾う)を見る。"""
    for key in ("icaoLocation", "affectedFir"):
        for tok in re.split(r"[\s,/;]+", (n.get(key) or "").upper()):
            if len(tok) != 4:
                continue
            if tok in AREA_BY_EXACT:
                return AREA_BY_EXACT[tok]
            if tok[:2] in AREA_BY_PREFIX:
                return AREA_BY_PREFIX[tok[:2]]
    return None


def record_target_area(rec):
    """保存済みレコード(またはArchiveのproperties)が、今の収集対象に入るか。target_area()と同じ判定。"""
    return target_area({"icaoLocation": rec.get("icao_location"), "affectedFir": rec.get("fir")})


def drop_untargeted(active):
    """収集対象から外したFIRのレコードを state から消す(対象を狭めたときの後始末)。消した件数を返す。"""
    gone = [k for k, r in active.items() if not record_target_area(r)]
    for k in gone:
        del active[k]
    return len(gone)


def q_category(q):
    """Qコード(例 QRDCA)の2文字目で大分類。R=空域制限、W=警告。他は 'other'。
    ICAO Doc 8126 の主題区分に基づく想定（要確認）。"""
    q = (q or "").upper()
    if len(q) >= 2 and q[0] == "Q":
        if q[1] == "R":
            return "restriction"
        if q[1] == "W":
            return "warning"
    return "other"


# Qコード2〜3文字目(主題)による絞り込み用タグ。ICAO Doc 8126 / FAA 7930.2 Appendix B の
# 正式な Second/Third Letter テーブルに基づく分類（2026-09-24 実データで裏取り済み）。
# あくまで「見る優先度」のラベルであり、軍事かどうかの自動判定はしない（人間が本文を読んで判断する）。
#   restriction: Rグループ(RA/RD/RM/RO/RP/RR/RT)。空域制限・危険区域の設定そのもの。
#   flag       : 軍事訓練・射撃・空中給油・危険物等、内容を優先的に確認したいもの。
#                ただし QWMLW は実データで花火が多数混入していたため、コード名を鵜呑みにしない。
#   watch      : 無人機・気球など、大半は民間だが個別確認が要るもの。
#   leisure    : 航空ショー・曲技飛行・グライダー・模型飛行等、軍事とは考えにくいレジャー・民間活動。
#   plaintext  : QXXXX 等、コード上は未分類で本文(E項)を読むしかないもの。
#   admin      : R/W グループ以外(空港施設・航法援助施設・運航方式等)。基本的に対象外。
_Q_SUBJECT_FLAG = {"WM", "WE", "WF", "WR", "WD", "WH"}
_Q_SUBJECT_WATCH = {"WU", "WL", "WC"}
_Q_SUBJECT_LEISURE = {"WA", "WB", "WG", "WJ", "WP", "WV", "WY", "WZ"}


def q_focus_tag(q):
    """フィルタ用の詳細タグ。q_category を補い、Wグループの中身をさらに粗く仕分ける。"""
    q = (q or "").upper()
    if len(q) != 5 or q[0] != "Q":
        return "admin"
    subject = q[1:3]
    if subject == "XX":
        return "plaintext"
    if subject[0] == "R":
        return "restriction"
    if subject[0] == "W":
        if subject in _Q_SUBJECT_FLAG:
            return "flag"
        if subject in _Q_SUBJECT_WATCH:
            return "watch"
        if subject in _Q_SUBJECT_LEISURE:
            return "leisure"
        return "watch"  # 未知のWサブタイプは安全側(要確認)に倒す
    return "admin"


# ----------------------------------------------------------------------------- 語句照合(keyword_hit)
# focus_tag/q_focus_tag はQコードの主題(何についてのNOTAMか)による粗い絞り込み。
# こちらは本文(raw_text)そのものに対する語句一致で、focus_tag/area_groupの絞り込みを
# 置き換えるものではなく、その上に重ねる追加条件として使う(ビューア側もAND条件)。
# 語句リストは別ルートで入手したもの(ユーザー提供、2026-09-24)。大文字小文字を区別しない
# 部分一致(ユーザー合意済み)。
# 注意: CLSD/CLOSED は誘導路・滑走路・駐機場閉鎖など空港運用の事務連絡(focus_tag=admin)にも
# 非常に高い頻度でヒットする(実データでの内訳は§?参照)。keyword_hitだけを唯一の絞り込みに
# せず、必ずarea_group/focus_tagの絞り込みと併用すること。
# 2026-09-27: SPECIALを追加(B3231/26 "SPECIAL OPS (AEROSPACE FLT ACT) ..." 等。HANDOFF_NOTAM.md §16)。
# ビューアでの語句フィルタはまだ使っていない(TEMPORARY・SPECIALを採用する予定)。
KEYWORD_LIST = ["DANGER", "TEMPORARY", "CLSD", "FORBIDDEN", "PROHIBITED", "DNG", "CLOSED", "SPECIAL"]


def keyword_hits(text):
    """raw_text に対して KEYWORD_LIST を大文字小文字を区別せず部分一致で照合し、
    ヒットした語句を(KEYWORD_LISTの順で)返す。1つもヒットしなければ空リスト。"""
    t = (text or "").upper()
    return [kw for kw in KEYWORD_LIST if kw in t]


_REF = re.compile(r"(\S+/\d+)\s+NOTAM([NRC])(?:\s+(\S+/\d+))?")


# E項の座標: 2つの書式に対応する。
#   (a) 記号が先: N230636E1162812（度分秒 DDMMSS/DDDMMSS）や N2306E11628（度分）
#   (b) 数字が先: 344747N1352608E（無人機エリア通知等で確認）、331300N1222400W（"TO"区切りの
#       航空路上NOTAMで確認、区切り自体は finditer が無視するので影響しない）
# 2026-09-27追加: 実データで(b)を取りこぼし、より粗い円・点にフォールバックしていたのを修正。
# 2026-09-27追加: (a)の緯度の数字の途中に空白が1つ入った表記(N39 5808E1012905、N39345 0E1052608。
#   FAA NOTAM SearchのPDFで確認)も読む。空白は緯度の桁の間だけ・1文字ずつ許し、読み取り後に取り除く。
_TEXT_COORD = re.compile(
    r"(?:(?P<ns1>[NS])(?P<lat1>\d(?:[ \t]?\d){3}(?:(?:[ \t]?\d){2}(?:\.\d+)?)?)\s*(?P<ew1>[EW])(?P<lon1>\d{5}(?:\d{2}(?:\.\d+)?)?))"
    r"|(?:(?P<lat2>\d{4}(?:\d{2}(?:\.\d+)?)?)(?P<ns2>[NS])\s*(?P<lon2>\d{5}(?:\d{2}(?:\.\d+)?)?)(?P<ew2>[EW]))"
)


def _dms(digits, deg_len):
    # 秒に小数が付く表記(364322.287N 1273032.318E。RKRRのA1391/26で確認)にも対応する
    digits, _, frac = digits.partition(".")
    d = int(digits[:deg_len])
    m = int(digits[deg_len:deg_len + 2])
    sec = int(digits[deg_len + 2:deg_len + 4]) if len(digits) > deg_len + 2 else 0
    if frac and len(digits) > deg_len + 2:
        sec += float("0." + frac)
    if m >= 60 or sec >= 60:
        return None
    return d + m / 60.0 + sec / 3600.0


def e_section(icao_text, text):
    """ICAO全文の E) 〜 F)(または末尾) を返す。無ければAPIの text。Q項の座標を拾わないため。"""
    if icao_text:
        m = re.search(r"E\)(.*?)(?:\bF\)|\Z)", icao_text, re.S)
        if m:
            return m[1]
    return text or ""


def _is_named_waypoint_coord(e_text, start, end):
    """'CG1(N253957E1095707)' のような、迂回路上の臨時ウェイポイント定義に出てくる座標かどうか。
    括弧の中に単独で座標が収まっている(前後が空白のみを挟んで'('/')')場合はそう判断し、
    境界ポリゴンの頂点としては数えない。2026-09-27追加: 航空路変更NOTAM(A4957/26等)で、
    複数の臨時ウェイポイント座標が実在しないエリア境界として誤認されていたのを修正。"""
    i = start - 1
    while i >= 0 and e_text[i] in " \t\r\n":
        i -= 1
    if i < 0 or e_text[i] != "(":
        return False
    j = end
    while j < len(e_text) and e_text[j] in " \t\r\n":
        j += 1
    return j < len(e_text) and e_text[j] == ")"


# 座標と座標の間がこれ(空白・ハイフン・カンマ・TO)だけなら、同じ多角形の続きとみなす。
# それ以外(ピリオドや "AREA 2:" のような見出し)が挟まっていたら、そこで多角形を区切る。
_RING_CONTINUES = re.compile(r"(?:[\s,\-]|\bTO\b)*", re.I)
# 座標の数字の途中で改行された表記(-36082⏎0N1293040E、N128⏎5338E。RKRRで確認)をつなぐ
_SPLIT_DIGITS = re.compile(r"(?<=\d)[ \t]*\r?\n[ \t]*(?=\d)")
# E項の円: "A CIRCLE RADIUS 7NM CENTERED ON <座標>" / "200NM RADIUS OF <座標>" / "0.5NM RAD OF <座標>"
# (いずれも直後に座標が来る形。単位は NM / KM / M)
_R = r"(?P<r>\d+(?:\.\d+)?)\s*(?P<u>NM|KM|M)\b"
_CIRCLE_BEFORE = re.compile(
    r"(?:CIRCLE\s+(?:WITH\s+)?(?:A\s+)?RADIUS\s+(?:OF\s+)?" + _R + r"\s+CENT(?:ER|RE)(?:ED|D)?\s+(?:ON|AT)"
    r"|" + _R.replace("?P<r>", "?P<r2>").replace("?P<u>", "?P<u2>") + r"\s+(?:RADIUS|RAD)\s+(?:OF|CENT(?:ER|RE)(?:ED|D)?\s+(?:ON|AT)|AROUND))\s*$",
    re.I)
# 中心の座標が先で半径が後の円: "A CIRCLE CENTERED AT <座標> WITH RADIUS OF 45KM" (ZSHA A5020/26、2026-09-30追加)
_CENTER_BEFORE = re.compile(r"CENT(?:ER|RE)(?:ED|D)?\s+(?:AT|ON)\s*$", re.I)
_RADIUS_AFTER = re.compile(r"\s*,?\s*(?:WITH\s+)?(?:A\s+)?RAD(?:IUS)?\s+(?:OF\s+)?" + _R, re.I)
# E項の帯状エリア: "1NM EITHER SIDE OF LINE <座標>-<座標>-..." (線の両側に指定幅)
_CORRIDOR = re.compile(r"(?P<w>\d+(?:\.\d+)?)\s*(?P<u>NM|KM)\s+EITHER\s+SIDE\s+OF\s+(?:THE\s+)?(?:CENT(?:ER|RE)\s*)?LINE", re.I)
TEXT_CIRCLE_MAX_NM = 500


def _to_nm(value, unit):
    unit = unit.upper()
    return value / 1.852 if unit == "KM" else value / 1852.0 if unit == "M" else value


def _corridor_ring(pts, width_nm):
    """線(GeoJSON順 [lon,lat] の点列)の両側 width_nm の帯を、閉じたリングにする(端は平ら)。
    幅が小さいので、線の中央緯度での等長方形近似で十分。"""
    lat0 = sum(p[1] for p in pts) / len(pts)
    kx = 60.0 * max(math.cos(math.radians(lat0)), 0.01)      # 経度1度あたりのNM
    xy = [(p[0] * kx, p[1] * 60.0) for p in pts]
    normals = []
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        d = math.hypot(x2 - x1, y2 - y1) or 1.0
        normals.append((-(y2 - y1) / d, (x2 - x1) / d))
    left, right = [], []
    for i, (x, y) in enumerate(xy):
        ns = [normals[k] for k in (i - 1, i) if 0 <= k < len(normals)]
        nx, ny = sum(n[0] for n in ns), sum(n[1] for n in ns)
        ln = math.hypot(nx, ny) or 1.0
        nx, ny = nx / ln, ny / ln
        cos_half = max(nx * ns[0][0] + ny * ns[0][1], 0.5)   # 曲がり角での幅の補正(上限2倍)
        off = width_nm / cos_half
        left.append([round((x + nx * off) / kx, 6), round((y + ny * off) / 60.0, 6)])
        right.append([round((x - nx * off) / kx, 6), round((y - ny * off) / 60.0, 6)])
    ring = left + right[::-1]
    return ring + [ring[0]]


def parse_text_polygons(e_text):
    """E項の座標列を多角形リング(GeoJSON順 [lon,lat])のリストにする。
    先頭点に戻ったところでリングを閉じ、複数エリアの列挙にも対応。3点未満は捨てる。
    座標の間に区切り記号以外(ピリオド・"AREA 2:"等)が挟まったところでも多角形を区切る
    (2026-09-27追加: L1539/26のように各エリアが先頭点に戻らず列挙される書式への対応)。
    括弧内に単独で書かれた座標(臨時ウェイポイント定義)は頂点として数えない。
    2026-09-28追加: 本文に書かれた円("A CIRCLE RADIUS 7NM CENTERED ON <座標>" 等)は円のリングに、
    "1NM EITHER SIDE OF LINE <座標>-<座標>" は線の両側の帯のリングにする。座標の数字の途中の改行もつなぐ。"""
    t = _SPLIT_DIGITS.sub("", e_text or "")
    rings, cur = [], []
    corridor = None          # 帯状エリアの点列を集めている間は幅(NM)
    prev_end = None

    def flush():
        if corridor is not None:
            if len(cur) >= 2:
                rings.append(_corridor_ring(cur, corridor))
        elif len(cur) >= 3:
            rings.append(cur + [cur[0]])

    for m in _TEXT_COORD.finditer(t):
        if _is_named_waypoint_coord(t, m.start(), m.end()):
            continue
        if m["ns1"] is not None:
            ns, lat_digits, ew, lon_digits = m["ns1"], re.sub(r"[ \t]", "", m["lat1"]), m["ew1"], m["lon1"]
        else:
            ns, lat_digits, ew, lon_digits = m["ns2"], m["lat2"], m["ew2"], m["lon2"]
        lat, lon = _dms(lat_digits, 2), _dms(lon_digits, 3)
        if lat is None or lon is None or lat > 90 or lon > 180:
            continue
        pt = [round(lon if ew == "E" else -lon, 6), round(lat if ns == "N" else -lat, 6)]
        gap = t[prev_end:m.start()] if prev_end is not None else t[:m.start()]

        c = _CIRCLE_BEFORE.search(t[max(0, m.start() - 120):m.start()])
        if not c and _CENTER_BEFORE.search(t[max(0, m.start() - 40):m.start()]):
            c = _RADIUS_AFTER.match(t, m.end())            # 中心が先・半径が後
        if c:
            flush()
            cur, corridor = [], None
            r_nm = _to_nm(float(c.groupdict().get("r") or c.groupdict().get("r2")), c.groupdict().get("u") or c.groupdict().get("u2"))
            if 0 < r_nm <= TEXT_CIRCLE_MAX_NM:
                rings.append(circle_ring(pt[1], pt[0], r_nm))
            prev_end = m.end()
            continue

        hdr = None
        for h in _CORRIDOR.finditer(gap):
            hdr = h
        if cur and (hdr or not _RING_CONTINUES.fullmatch(gap)):
            flush()
            cur, corridor = [], None
        if hdr:
            corridor = _to_nm(float(hdr["w"]), hdr["u"])
        prev_end = m.end()
        if corridor is None and cur and pt == cur[0] and len(cur) >= 3:
            rings.append(cur + [pt])
            cur = []
        else:
            cur.append(pt)
    flush()
    return rings


def text_rings(e_text):
    """E項から読める面(リング)。座標で書かれた多角形・円・帯(parse_text_polygons)が無ければ、
    地点名を中心にした円(例: "CIRCLE CENTERED AT SHIQUANHE VOR 'SQH' WITH RADIUS OF 30KM"。
    中心はAIP索引から引く。notam_route_segments.named_center_circles)を見る。2026-09-29追加。"""
    rings = parse_text_polygons(e_text)
    if rings:
        return rings
    return [circle_ring(lat, lon, r) for lat, lon, r in notam_route_segments.named_center_circles(e_text)
            if 0 < r <= TEXT_CIRCLE_MAX_NM]


def ring_centroid(ring):
    pts = ring[:-1]
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)      # (lat, lon)


def nm_between(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(h))


def _valid_polygon(coords):
    try:
        for ring in coords:
            if len(ring) < 4 or ring[0] != ring[-1]:
                return False
            for x, y in ring:
                if not (math.isfinite(x) and math.isfinite(y)):
                    return False
        return True
    except (TypeError, ValueError):
        return False


def build_geometry(feature, n, e_text=""):
    """(geometry|None, source)。優先順: APIの面 > E項テキストの多角形 > 航空路閉鎖の線 > Q項の円 > Q項の点 > APIの点。
    Q項の中心座標は誤記がありうる（実データで、E項の多角形から138NMずれた例を確認）ため、
    E項に多角形があればそちらを正とする。"""
    g = feature.get("geometry") or {}
    parts = g.get("geometries", []) if g.get("type") == "GeometryCollection" else [g]
    polys, points = [], []
    for p in parts:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "Polygon" and _valid_polygon(p.get("coordinates")):
            polys.append(p["coordinates"])
        elif p.get("type") == "MultiPolygon":
            polys.extend(c for c in p.get("coordinates", []) if _valid_polygon(c))
        elif p.get("type") == "Point" and len(p.get("coordinates") or []) >= 2:
            pc = p["coordinates"][:2]
            # (0,0)は座標未設定のプレースホルダー("null island")と判断し、実座標として使わない。
            # 複数FIRにまたがる案内NOTAM(A)欄が複数地点)等でAPIがこの値を返す実例を確認。
            if abs(pc[0]) > 1e-6 or abs(pc[1]) > 1e-6:
                points.append(pc)
    if polys:
        geom = {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 \
            else {"type": "MultiPolygon", "coordinates": polys}
        return geom, "api"
    rings = text_rings(e_text)
    if rings:
        geom = {"type": "Polygon", "coordinates": [rings[0]]} if len(rings) == 1 \
            else {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}
        return geom, "text-polygon"
    # 航空路の区間閉鎖(SEGMENT A-B OF ATS RTE R CLSD 等)を、AIPデータセットの索引で線にする。
    # 索引(環境変数 AIPDS_INDEX)が無ければ何もしない。HANDOFF_NOTAM.md §19
    line = notam_route_segments.route_closure_geometry(e_text)
    if line:
        return line, "route-segment"
    # AIPの図を参照する区域(REF AIP CHINA ZWWW-3P-5 ... WEST AND NORTH OF G-H-J-K)。図の点を結んだ境界線
    line = aip_chart_areas.chart_boundary_geometry(e_text)
    if line:
        return line, "aip-chart-line"
    c = parse_qline_coord(n.get("coordinates"))
    try:
        r = float(n.get("radius")) if n.get("radius") not in (None, "") else None
    except (TypeError, ValueError):
        r = None
    if c and r is not None and 0 < r <= CIRCLE_MAX_NM:
        return {"type": "Polygon", "coordinates": [circle_ring(c[0], c[1], r)]}, "qline-circle"
    if c:
        return {"type": "Point", "coordinates": [round(c[1], 6), round(c[0], 6)]}, "qline-point"
    if points:
        return {"type": "Point", "coordinates": points[0]}, "api-point"
    return None, None


# ----------------------------------------------------------------------------- レコード
def make_record(feature, now):
    core = ((feature.get("properties") or {}).get("coreNOTAMData")) or {}
    n = core.get("notam") or {}
    nid = str(n.get("id") or "").replace("NMS_ID_", "").strip()
    area = target_area(n)
    if not nid or not area:
        return None
    icao_text = local_text = None
    for t in core.get("notamTranslation") or []:
        if not isinstance(t, dict):
            continue
        # 1.0.18: formattedText/simpleText、1.0.17: icao_message/domestic_message
        if t.get("type") == "ICAO":
            icao_text = t.get("formattedText") or t.get("icao_message")
        elif t.get("type") == "LOCAL_FORMAT":
            local_text = t.get("simpleText") or t.get("domestic_message")
    ntype = (n.get("type") or "").upper()
    ref = None
    m = _REF.search(icao_text or "")
    if m:
        ntype = m[2]                       # 本文の N/R/C を優先
        ref = m[3] if ntype in ("R", "C") else None
    e_text = e_section(icao_text, n.get("text"))
    geom, gsrc = build_geometry(feature, n, e_text)
    try:
        radius = float(n["radius"]) if n.get("radius") not in (None, "") else None
    except (TypeError, ValueError):
        radius = None
    offset = None                                   # Q項の中心と、実際の図形の重心とのずれ(NM)。品質チェック用
    qc = parse_qline_coord(n.get("coordinates"))
    if qc and geom and geom["type"] in ("Polygon", "MultiPolygon") and gsrc in ("api", "text-polygon"):
        ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        offset = round(nm_between(ring_centroid(ring), qc), 1)
    return {
        "id": nid, "number": n.get("number"), "series": n.get("series"), "year": n.get("year"),
        "type": ntype or None, "ref_number": ref, "issued": n.get("issued"),
        "icao_location": n.get("icaoLocation"), "location": n.get("location"), "fir": n.get("affectedFir"),
        "area_group": area, "classification": n.get("classification"), "account_id": n.get("accountId"),
        "q_code": n.get("selectionCode"), "traffic": n.get("traffic"), "purpose": n.get("purpose"),
        "scope": n.get("scope"), "min_fl": n.get("minimumFl"), "max_fl": n.get("maximumFl"),
        "lower": n.get("lowerLimit"), "upper": n.get("upperLimit"),
        "effective_start": n.get("effectiveStart"), "effective_end": n.get("effectiveEnd"),
        "estimated": str(n.get("estimated")).lower() == "true", "schedule": n.get("schedule"),
        "cancelation_date": n.get("cancelationDate"),
        "coordinates": n.get("coordinates"), "radius_nm": radius,
        "text": n.get("text"), "icao_text": icao_text, "local_text": local_text,
        "last_updated": n.get("lastUpdated"),
        "geometry": geom, "geometry_source": gsrc, "qline_offset_nm": offset,
        "first_seen": iso(now), "ended_by": None, "end_override": None,
    }


def effective_end(rec):
    """予定終了・取消日・後続NOTAM(R/C)による打ち切りのうち最も早いもの。PERMなどで無ければ None。"""
    ends = [parse_dt(rec.get(k)) for k in ("effective_end", "cancelation_date", "end_override")]
    ends = [e for e in ends if e]
    return min(ends) if ends else None


def status_of(rec, now):
    if rec.get("type") == "C":
        return "cancel-notice"
    start, end = parse_dt(rec.get("effective_start")), effective_end(rec)
    # 開始前に取消・置換されたもの(取消NOTAMの発行が元NOTAMの開始より前)は、開始を待たずに
    # 「取消」にする。以前は開始時刻まで「予定(upcoming)」のまま残っていた(KZAK A4680/26等)。
    if rec.get("end_override") and end and end <= now and (not start or end <= start):
        return "cancelled"
    if start and start > now:
        return "upcoming"
    if end and end <= now:
        sched = parse_dt(rec.get("effective_end"))
        return "cancelled" if (rec.get("end_override") and (not sched or end < sched)) else "expired"
    return "active"


def apply_links(active):
    """R/C(置換・取消)NOTAMが参照する元NOTAMの有効期間を、発行時刻で打ち切る。追加のみで消さない。"""
    by_number = collections.defaultdict(list)
    for r in active.values():
        by_number[r["number"]].append(r)
    for r in active.values():
        if r.get("type") not in ("R", "C") or not r.get("ref_number"):
            continue
        cands = [x for x in by_number.get(r["ref_number"], [])
                 if x["id"] != r["id"] and x["area_group"] == r["area_group"]]
        same_loc = [x for x in cands if x["icao_location"] == r["icao_location"]]
        for x in (same_loc or cands):
            issued = parse_dt(r.get("issued"))
            cur = parse_dt(x.get("end_override"))
            if issued and (not cur or issued < cur):
                x["end_override"] = iso(issued)
            x["ended_by"] = r["number"]
            x["ended_by_type"] = r["type"]          # "C"=取消(撤回) / "R"=置換。ビューアの「〜により撤回」表示用


def ingest(active, features, now, stats):
    for f in features:
        if not isinstance(f, dict):
            continue
        rec = make_record(f, now)
        if rec is None:
            stats["skipped_not_target"] += 1
            continue
        old = active.get(rec["id"])
        if old:
            same = old.get("last_updated") == rec["last_updated"] and old.get("text") == rec["text"]
            if same:
                stats["unchanged"] += 1
                continue
            rec["first_seen"] = old.get("first_seen", rec["first_seen"])
            rec["ended_by"], rec["end_override"] = old.get("ended_by"), old.get("end_override")
            stats["updated"] += 1
        else:
            stats["new"] += 1
        if rec["geometry"] is None:
            stats["no_geometry"] += 1
        active[rec["id"]] = rec


# ----------------------------------------------------------------------------- GeoJSON
def to_feature(rec, now):
    when = parse_dt(rec.get("issued"))
    place = ((rec.get("icao_location") or rec.get("fir") or "").split() or [""])[0]
    end = effective_end(rec)
    raw_text = rec.get("icao_text") or rec.get("text")
    hits = keyword_hits(raw_text)
    return {
        "type": "Feature",
        "geometry": rec.get("geometry"),
        "properties": {
            "title": f"{AREA_LABEL.get(rec['area_group'], rec['area_group'])} {place} {rec.get('number')}",
            "date": when.strftime("%Y-%m-%d") if when else None,
            "issuer": rec.get("account_id"),
            "raw_text": raw_text,
            "valid_start": iso(parse_dt(rec.get("effective_start"))),
            "valid_end": iso(end),
            "valid_raw": [rec["schedule"]] if rec.get("schedule") else [],
            "kind": "point" if (rec.get("geometry") or {}).get("type") == "Point" else "area",
            "source": "nms",
            "nms_id": rec["id"], "number": rec.get("number"), "series": rec.get("series"),
            "notam_type": rec.get("type"), "area_group": rec["area_group"],
            "icao_location": rec.get("icao_location"), "fir": rec.get("fir"),
            # INTL(ICAO書式) / DOM(米国国内書式) / MIL。ビューアはDOMを表示しない(同じ告示のICAO書式版が別にある)
            "classification": rec.get("classification"),
            "q_code": rec.get("q_code"), "category": q_category(rec.get("q_code")),
            "focus_tag": q_focus_tag(rec.get("q_code")),
            "keyword_hit": bool(hits), "matched_keywords": hits,
            "lower": rec.get("lower"), "upper": rec.get("upper"),
            "radius_nm": rec.get("radius_nm"), "geometry_source": rec.get("geometry_source"),
            "qline_offset_nm": rec.get("qline_offset_nm"),
            "estimated_end": rec.get("estimated"), "status": status_of(rec, now),
            "ended_by": rec.get("ended_by"), "ended_by_type": rec.get("ended_by_type"),
            "first_seen": rec.get("first_seen"),
            "last_updated": rec.get("last_updated"),
        },
    }


def sort_key(f):
    p = f["properties"]
    return (p["area_group"], p.get("icao_location") or "", p.get("number") or "", p["nms_id"])


# ----------------------------------------------------------------------------- ファイルI/O
def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def write_if_changed(path, obj, indent=None, dry=False):
    path = Path(path)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=indent,
                      separators=(",", ": ") if indent else (",", ":")) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)      # 途中で落ちても壊れたファイルを残さない
    return True


def archive_ended(active, now, keep_days, notice_days, out_dir, dry, stats):
    """失効・取消したNOTAMを月別Archiveへ移し、activeから外す。Archiveは追記のみで削除しない。
    既定は即時(keep_days=0)。取消通知(C)自体は、対象NOTAMより後に届く順序入れ替わりに備えて
    notice_days だけactiveに残す（その間に apply_links が対象を打ち切れる）。"""
    moving = collections.defaultdict(list)
    for rid, rec in list(active.items()):
        st = status_of(rec, now)
        if st in ("expired", "cancelled"):
            ref_t, cutoff = effective_end(rec), now - dt.timedelta(days=keep_days)
        elif st == "cancel-notice":
            ref_t, cutoff = parse_dt(rec.get("issued")), now - dt.timedelta(days=notice_days)
        else:
            continue
        if ref_t and ref_t <= cutoff:
            moving[ref_t.strftime("%Y-%m")].append(rid)
    for month, ids in moving.items():
        path = Path(out_dir) / "archive" / f"notam_{month}.geojson"
        fc = read_json(path, {"type": "FeatureCollection", "features": []})
        byid = {f["properties"]["nms_id"]: f for f in fc["features"]}
        for rid in ids:
            feat = to_feature(active[rid], now)          # 移動時点の最終状態で保存
            prev = byid.get(rid)
            if prev:                                     # 差分に再登場した古いNOTAMで first_seen が変わらないように
                feat["properties"]["first_seen"] = prev["properties"].get("first_seen", feat["properties"]["first_seen"])
            byid[rid] = feat
        fc["features"] = sorted(byid.values(), key=sort_key)
        if write_if_changed(path, fc, dry=dry):
            stats["archived"] += len(ids)
        else:
            stats["archived_unchanged"] += len(ids)      # 既にArchive済みのものが再登場しただけ
        for rid in ids:
            del active[rid]


# ----------------------------------------------------------------------------- API
def http(method, url, headers=None, data=None, timeout=HTTP_TIMEOUT, raw=False):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    except Exception as e:
        raise ApiError(f"{method} {urllib.parse.urlsplit(url).path}: {type(e).__name__}: {e}")
    if raw:
        return status, body
    try:
        return status, json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return status, body[:300].decode("utf-8", "replace")


class Nms:
    def __init__(self):
        env = (os.environ.get("NMS_ENV") or "prod").strip().lower()
        self.base = (os.environ.get("NMS_HOST") or HOSTS.get(env) or "").rstrip("/")
        if not self.base:
            raise ApiError(f"NMS_ENV は {sorted(HOSTS)} のいずれか")
        cid, sec = (os.environ.get("NMS_CLIENT_ID") or "").strip(), (os.environ.get("NMS_CLIENT_SECRET") or "").strip()
        if not cid or not sec:
            raise ApiError("NMS_CLIENT_ID / NMS_CLIENT_SECRET が未設定")
        basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
        st, body = http("POST", f"{self.base}/v1/auth/token",
                        headers={"Authorization": f"Basic {basic}",
                                 "Content-Type": "application/x-www-form-urlencoded"},
                        data=b"grant_type=client_credentials")
        if st != 200 or not isinstance(body, dict) or "access_token" not in body:
            raise ApiError(f"トークン取得失敗 HTTP {st}（認証情報の環境と NMS_ENV を確認）")
        self._auth = {"Authorization": f"Bearer {body['access_token']}"}

    def get(self, path, params=None, fmt="GEOJSON", raw=False, timeout=HTTP_TIMEOUT):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        h = dict(self._auth, Accept="application/json")
        if fmt:
            h["nmsResponseFormat"] = fmt
        return http("GET", f"{self.base}/nmsapi{path}{q}", headers=h, raw=raw, timeout=timeout)

    def notams(self, params):
        for attempt in (1, 2):
            st, body = self.get("/v1/notams", params)
            if st == 200 and isinstance(body, dict):
                return features_of(body)
            if st in (408, 500, 502, 503, 504) and attempt == 1:
                time.sleep(5)
                continue
            raise ApiError(f"/v1/notams {params} -> HTTP {st}")

    def initial_load(self, classification):
        st, body = self.get("/v1/notams", {"classification": classification, "allowRedirect": "false"})
        url = (body.get("data") or {}).get("url") if isinstance(body, dict) else None
        if st not in (200, 307) or not url:
            raise ApiError(f"initial load 取得失敗 HTTP {st}")
        path = url if url.startswith("/nmsapi") else "/nmsapi" + url      # FAQ: /nmsapi/v1/content/{token}
        st, raw = http("GET", self.base + path, headers=dict(self._auth), raw=True, timeout=600)
        if st != 200:
            raise ApiError(f"initial load 本体の取得失敗 HTTP {st}")
        return parse_bulk(raw)


def features_of(body):
    data = (body or {}).get("data") or {}
    return [f for f in (data.get("geojson") or []) if isinstance(f, dict)]


def parse_bulk(raw):
    """初期ロードのファイル(gz/非gz、JSON/JSONL)から Feature 配列を取り出す。形式は実データ未確認のため寛容に。"""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        try:
            obj = [json.loads(line) for line in text.splitlines() if line.strip()]
        except ValueError:
            raise ApiError("初期ロードがJSON/JSONLではない（AIXM形式の可能性）。--bootstrap locations を使うか、"
                           "形式を共有してほしい")
    if isinstance(obj, dict):
        obj = ((obj.get("data") or {}).get("geojson")) or obj.get("features") or []
    feats = []
    for x in obj:
        if isinstance(x, dict) and x.get("type") == "FeatureCollection":
            feats.extend(x.get("features") or [])
        elif isinstance(x, dict):
            feats.append(x)
    if not feats:
        raise ApiError("初期ロードから Feature を取り出せなかった（形式が想定と違う。AIXM形式の可能性）")
    return feats


def load_fixture(path):
    obj = read_json(path, None)
    if obj is None:
        raise ApiError(f"fixture が見つからない: {path}")
    return parse_bulk(json.dumps(obj).encode())


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="中国・香港・マカオ・台湾のNOTAM収集", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./notam_out")
    ap.add_argument("--fixture", help="API を呼ばず、このJSON(Feature配列/NMS応答/FeatureCollection)を取り込む")
    ap.add_argument("--bootstrap", choices=["il", "locations"],
                    help="初回の全件投入方式。il=分類単独指定の一括GeoJSON(/v1/notams?classification=)、locations=地点ごとに問い合わせ")
    ap.add_argument("--il-class", default="INTERNATIONAL", help="initial load の分類（既定 INTERNATIONAL）")
    ap.add_argument("--extra-locations", default="", help="locations方式で追加問い合わせするICAOコード(カンマ区切り)")
    ap.add_argument("--keep-ended-days", type=int, default=0, help="失効・取消後にactiveへ残す日数。既定0=即時Archive")
    ap.add_argument("--keep-notice-days", type=int, default=2, help="取消通知(C)をactiveに残す日数（順序入れ替わり対策）")
    ap.add_argument("--heartbeat-hours", type=float, default=3.0, help="データ変化が無くてもmeta.jsonを更新する間隔")
    ap.add_argument("--now", help="現在時刻の上書き（テスト用）ISO8601")
    ap.add_argument("--dry-run", action="store_true", help="何も書かず結果だけ表示")
    a = ap.parse_args(argv)

    now = parse_dt(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    out = Path(a.out_dir)
    stats = collections.Counter()
    active = read_json(out / "state_active.json", {})
    dropped = drop_untargeted(active)
    if dropped:
        stats["dropped_untargeted"] = dropped
    meta = read_json(out / "meta.json", {})
    last_ok = parse_dt(meta.get("last_success"))

    try:
        if a.fixture:
            feats = load_fixture(a.fixture)
        else:
            api = Nms()
            if a.bootstrap == "il":
                feats = api.initial_load(a.il_class)
            elif a.bootstrap == "locations":
                st, body = api.get("/v1/locationseries", fmt=None, timeout=300)
                if st != 200 or not isinstance(body, dict):
                    raise ApiError(f"locationseries 取得失敗 HTTP {st}")
                rows = (body.get("data") or {}).get("locationSeries") or []
                locs = {r["icaoId"] for r in rows if isinstance(r, dict)
                        and len(r.get("icaoId") or "") == 4
                        and (r["icaoId"][:2].upper() in AREA_BY_PREFIX or r["icaoId"].upper() in AREA_BY_EXACT)}
                locs |= {x.strip().upper() for x in a.extra_locations.split(",") if x.strip()}
                print(f"locations方式: {len(locs)} 地点を個別に問い合わせ", file=sys.stderr)
                feats = []
                for loc in sorted(locs):
                    feats.extend(api.notams({"location": loc}))
                    time.sleep(0.2)
            else:
                if last_ok is None:
                    since = now - COLD_START_WINDOW
                    print("WARN: meta.json が無い(初回)。直近6時間だけ取得。先に --bootstrap il を推奨", file=sys.stderr)
                else:
                    since = last_ok - OVERLAP
                    if now - since > MAX_DELTA_WINDOW:
                        print(f"WARN: 最終成功から {now - last_ok} 経過。差分APIの上限を超えるため取りこぼしの可能性。"
                              "--bootstrap を実行してください", file=sys.stderr)
                        since = now - MAX_DELTA_WINDOW
                feats = api.notams({"lastUpdatedDate": since.strftime("%Y-%m-%dT%H:%M:%SZ")})
    except ApiError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    stats["fetched"] = len(feats)
    ingest(active, feats, now, stats)
    apply_links(active)
    archive_ended(active, now, a.keep_ended_days, a.keep_notice_days, out, a.dry_run, stats)

    features = sorted((to_feature(r, now) for r in active.values() if r.get("type") != "C" and r.get("geometry")),
                      key=sort_key)
    counts = collections.Counter(f["properties"]["status"] for f in features)
    changed = [
        write_if_changed(out / "state_active.json", active, indent=1, dry=a.dry_run),
        write_if_changed(out / "notam_cn.geojson", {"type": "FeatureCollection", "features": features}, dry=a.dry_run),
    ]
    if any(changed) or stats["archived"]:
        meta["last_change"] = iso(now)
    beat = last_ok is None or (now - last_ok) >= dt.timedelta(hours=a.heartbeat_hours)
    if not a.fixture and (any(changed) or beat):
        meta.update({"schema": SCHEMA_VERSION, "last_success": iso(now),
                     "active_records": len(active), "features": len(features), "by_status": dict(counts)})
    elif a.fixture and any(changed):
        meta.update({"schema": SCHEMA_VERSION, "active_records": len(active), "features": len(features),
                     "by_status": dict(counts)})
    write_if_changed(out / "meta.json", meta, indent=1, dry=a.dry_run)

    summary = {"changed": any(changed), "dry_run": a.dry_run, **dict(stats),
               "active_records": len(active), "features": len(features), "by_status": dict(counts)}
    print(json.dumps(summary, ensure_ascii=False))
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write("### NOTAM collect\n```json\n" + json.dumps(summary, ensure_ascii=False, indent=1) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
