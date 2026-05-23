"""Inverse Head and Shoulders pattern detector.

Mirror image of H&S. Sequence L-H-L-H-L where the middle low is lower
than both shoulders. Neckline drawn through the two intervening highs.
"""
from __future__ import annotations

from typing import Any, Dict, List

from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus, SwingKind
from patterns.swing_detector import SwingDetector
from patterns.trendline_engine import fit_line
from utils.time_utils import now_ist


@register_detector("inverse_head_and_shoulders")
def detect_inverse_head_and_shoulders(
    series: CandleSeries, config: Dict[str, Any]
) -> List[Pattern]:
    if not config.get("enabled", False):
        return []
    min_bars = int(config.get("min_pattern_bars", 15))
    tol = float(config.get("symmetry_tolerance", 0.10))

    if len(series) < min_bars:
        return []

    detector = SwingDetector(
        lookback=int(config.get("swing_lookback", 8)),
        sensitivity=float(config.get("swing_sensitivity", 0.0015)),
    )
    swings = detector.detect(series)
    if len(swings) < 5:
        return []

    last = series.last()
    if last is None or not last.is_closed:
        return []

    out: List[Pattern] = []
    for i in range(len(swings) - 5, len(swings) - 4):
        window = swings[i : i + 5]
        if [s.kind for s in window] != [
            SwingKind.LOW,
            SwingKind.HIGH,
            SwingKind.LOW,
            SwingKind.HIGH,
            SwingKind.LOW,
        ]:
            continue
        ls, hi1, head, hi2, rs = window
        if head.price >= ls.price or head.price >= rs.price:
            continue
        symmetry = abs(ls.price - rs.price) / max(abs(head.price), 1e-9)
        if symmetry > tol:
            continue

        slope, intercept = fit_line(
            [(hi1.index_in_series, hi1.price), (hi2.index_in_series, hi2.price)]
        )
        neckline_at_last = slope * (len(series) - 1) + intercept

        status = PatternStatus.FORMING
        if last.close > neckline_at_last:
            status = PatternStatus.BREAKOUT

        out.append(
            Pattern(
                kind=PatternKind.INVERSE_HEAD_SHOULDERS,
                symbol=series.symbol,
                timeframe=series.timeframe,
                status=status,
                swings=list(window),
                confirmation_price=last.close if status == PatternStatus.BREAKOUT else None,
                invalidation_price=head.price,
                metadata={
                    "direction": "BUY",
                    "symmetry": symmetry,
                    "neckline_slope": slope,
                    "neckline_intercept": intercept,
                    "reference_high": max(hi1.price, hi2.price),
                    "reference_low": head.price,
                },
                created_at=ls.timestamp,
                updated_at=now_ist(),
            )
        )
    return out
