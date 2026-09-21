"""三阶互调 (2fa - fb) 分析核心逻辑，纯函数实现，不依赖 Web 框架。"""

from __future__ import annotations

from typing import Iterable, Optional


def distance_to_band(freq_hz: int, start_hz: int, end_hz: int) -> int:
    """频点到保护频段的最短距离：频段内为 0，否则为到最近边界的距离。"""
    if start_hz <= freq_hz <= end_hz:
        return 0
    return min(abs(freq_hz - start_hz), abs(freq_hz - end_hz))


def enumerate_products(
    carriers_hz: Iterable[int],
    scan_start_hz: int,
    scan_end_hz: int,
) -> dict[int, list[tuple[int, int]]]:
    """枚举不同载频的 2fa-fb 三阶产物（fa != fb，有序对）。

    仅保留落在扫描范围 [scan_start_hz, scan_end_hz] 内的产物；
    同频产物聚合全部来源组合。返回 {频率: [(fa, fb), ...]}，按频率升序。
    """
    carriers = sorted(set(carriers_hz))
    products: dict[int, list[tuple[int, int]]] = {}
    for fa in carriers:
        for fb in carriers:
            if fa == fb:
                continue
            freq = 2 * fa - fb
            if scan_start_hz <= freq <= scan_end_hz:
                products.setdefault(freq, []).append((fa, fb))
    for combos in products.values():
        combos.sort()
    return dict(sorted(products.items()))


def analyze(
    carriers_hz: Iterable[int],
    guard_bands: Iterable[tuple[int, int]],
    scan_start_hz: int,
    scan_end_hz: int,
    guard_interval_hz: int,
) -> list[dict]:
    """对站点频率方案执行互调分析，返回按频率升序的产物列表。

    每个产物包含：频率、是否命中（最短距离 < 保护间隔）、命中对象
    （最近的载频或保护频段）、到最近载频/保护频段的距离、来源组合。
    """
    carriers = sorted(set(carriers_hz))
    bands = sorted((int(s), int(e)) for s, e in guard_bands)

    results: list[dict] = []
    for freq, combos in enumerate_products(carriers, scan_start_hz, scan_end_hz).items():
        nearest_carrier = min(carriers, key=lambda c: (abs(freq - c), c))
        d_carrier = abs(freq - nearest_carrier)

        d_band: Optional[int] = None
        nearest_band: Optional[tuple[int, int]] = None
        if bands:
            d_band, nearest_band = min(
                ((distance_to_band(freq, s, e), (s, e)) for s, e in bands),
                key=lambda item: (item[0], item[1][0], item[1][1]),
            )

        # 距离相等时优先载频，保证结果确定
        if d_band is not None and nearest_band is not None and d_band < d_carrier:
            nearest = {
                "kind": "guard_band",
                "distance_hz": d_band,
                "carrier_hz": None,
                "band": {"start_hz": nearest_band[0], "end_hz": nearest_band[1]},
            }
            min_distance = d_band
        else:
            nearest = {
                "kind": "carrier",
                "distance_hz": d_carrier,
                "carrier_hz": nearest_carrier,
                "band": None,
            }
            min_distance = d_carrier

        results.append(
            {
                "frequency_hz": freq,
                "is_hit": min_distance < guard_interval_hz,
                "nearest": nearest,
                "distance_to_nearest_carrier_hz": d_carrier,
                "distance_to_nearest_guard_band_hz": d_band,
                "sources": [{"fa_hz": fa, "fb_hz": fb} for fa, fb in combos],
            }
        )
    return results
