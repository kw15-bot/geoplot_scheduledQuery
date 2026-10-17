# -*- coding: utf-8 -*-
"""描画チェック(msa_check.py・notam_check.py)で共通に使う、経緯度の多角形の簡単な計算。標準ライブラリだけ。"""

import math


def ring_area_km2(ring):
    """経緯度の環の面積(km²、等長方形近似)。"""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 111.32
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        a += (x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky)
    return abs(a) / 2


def _seg_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def ring_self_intersects(ring):
    n = len(ring) - 1                       # 閉じた環(最後 = 最初)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue                    # 隣り合う(最初と最後の辺)
            if _seg_cross(ring[i], ring[i + 1], ring[j], ring[j + 1]):
                return True
    return False
