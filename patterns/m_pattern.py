"""Custom M Pattern (double-top variant) detector.

Sequence: H-L-H where the two highs are roughly equal (`peak_tolerance`)
and a confirmed close below the trough fires the SELL signal.
"""

from __future__ import annotations

from typing import Any, Dict, List

from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus, SwingKind
from patterns.swing_detector import SwingDetector
from utils.time_utils import now_ist


@register_detector("m_pattern")
def detect_m_pattern(series: CandleSeries, config: Dict[str, Any]) -> List[Pattern]:
    if not config.get("enabled", False):
        return []
    if len(series) < int(config.get("min_pattern_bars", 10)):
        return []

    tol = float(config.get("peak_tolerance", 0.005))
    detector = SwingDetector(
        lookback=int(config.get("swing_lookback", 6)),
        sensitivity=float(config.get("swing_sensitivity", 0.0015)),
    )
    swings = detector.detect(series)
    if len(swings) < 3:
        return []

    last = series.last()
    if last is None or not last.is_closed:
        return []

    out: List[Pattern] = []
    window = swings[-3:]
    if [s.kind for s in window] != [SwingKind.HIGH, SwingKind.LOW, SwingKind.HIGH]:
        return []
    h1, trough, h2 = window
    if abs(h1.price - h2.price) / max(h1.price, 1e-9) > tol:
        return []

    status = PatternStatus.FORMING
    if last.close < trough.price:
        status = PatternStatus.BREAKOUT

    out.append(
        Pattern(
            kind=PatternKind.M_PATTERN,
            symbol=series.symbol,
            timeframe=series.timeframe,
            status=status,
            swings=list(window),
            confirmation_price=last.close if status == PatternStatus.BREAKOUT else None,
            invalidation_price=max(h1.price, h2.price),
            metadata={
                "direction": "SELL",
                "neckline_price": trough.price,
                "reference_high": max(h1.price, h2.price),
                "reference_low": trough.price,
            },
            created_at=h1.timestamp,
            updated_at=now_ist(),
        )
    )
    return out
