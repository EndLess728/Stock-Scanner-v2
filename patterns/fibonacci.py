"""Fibonacci retracement / extension detector.

Identifies an anchor swing (last opposing swings) and reports:
- *Rejection* at a fib level
- *Breakout* through a level after retracement
- *Retest* of a level
"""

from __future__ import annotations

from typing import Any, Dict, List

from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus, SwingKind
from patterns.swing_detector import SwingDetector
from utils.time_utils import now_ist


@register_detector("fibonacci")
def detect_fibonacci(series: CandleSeries, config: Dict[str, Any]) -> List[Pattern]:
    if not config.get("enabled", False):
        return []

    levels: List[float] = list(config.get("levels", [0.236, 0.382, 0.5, 0.618, 0.786]))
    extensions: List[float] = list(config.get("extensions", [1.272, 1.618, 2.0]))
    tol = float(config.get("rejection_tolerance", 0.001))

    detector = SwingDetector(
        lookback=int(config.get("swing_lookback", 8)),
        sensitivity=float(config.get("swing_sensitivity", 0.0015)),
    )
    swings = detector.detect(series)
    if len(swings) < 2:
        return []

    last = series.last()
    if last is None or not last.is_closed:
        return []

    # Use the most recent opposing swing pair as the anchor
    high_swing = next((s for s in reversed(swings) if s.kind == SwingKind.HIGH), None)
    low_swing = next((s for s in reversed(swings) if s.kind == SwingKind.LOW), None)
    if high_swing is None or low_swing is None:
        return []

    if high_swing.timestamp > low_swing.timestamp:
        # Down-move expected -> retrace from high to low
        top, bottom = high_swing.price, low_swing.price
        direction = "BUY"  # rejection at retracement = bullish bounce
    else:
        top, bottom = high_swing.price, low_swing.price
        direction = "SELL"

    rng = top - bottom
    if rng <= 0:
        return []

    fib_levels = {f"{lvl:.3f}": bottom + lvl * rng for lvl in levels}
    fib_levels.update({f"ext_{ext:.3f}": bottom + ext * rng for ext in extensions})

    status = PatternStatus.FORMING
    hit_level: str | None = None
    tol_abs = top * tol

    for name, lvl_price in fib_levels.items():
        if abs(last.close - lvl_price) <= tol_abs:
            status = PatternStatus.RETEST
            hit_level = name
            break

    if status == PatternStatus.FORMING:
        # Detect rejection (wick touched but close moved away)
        for name, lvl_price in fib_levels.items():
            if last.high >= lvl_price >= last.close + tol_abs:
                status = PatternStatus.CONFIRMED
                hit_level = name
                break
            if last.low <= lvl_price <= last.close - tol_abs:
                status = PatternStatus.CONFIRMED
                hit_level = name
                break

    if status == PatternStatus.FORMING:
        return []

    return [
        Pattern(
            kind=PatternKind.FIBONACCI,
            symbol=series.symbol,
            timeframe=series.timeframe,
            status=status,
            swings=[high_swing, low_swing],
            confirmation_price=last.close,
            metadata={
                "direction": direction,
                "fib_level": hit_level,
                "levels": fib_levels,
                "reference_high": top,
                "reference_low": bottom,
            },
            created_at=min(high_swing.timestamp, low_swing.timestamp),
            updated_at=now_ist(),
        )
    ]
