"""Custom W Pattern (double-bottom variant) detector."""

from __future__ import annotations

from typing import Any, Dict, List

from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus, SwingKind
from patterns.swing_detector import SwingDetector
from utils.time_utils import now_ist


@register_detector("w_pattern")
def detect_w_pattern(series: CandleSeries, config: Dict[str, Any]) -> List[Pattern]:
    if not config.get("enabled", False):
        return []
    if len(series) < int(config.get("min_pattern_bars", 10)):
        return []

    tol = float(config.get("trough_tolerance", 0.005))
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

    window = swings[-3:]
    if [s.kind for s in window] != [SwingKind.LOW, SwingKind.HIGH, SwingKind.LOW]:
        return []
    l1, peak, l2 = window
    if abs(l1.price - l2.price) / max(l1.price, 1e-9) > tol:
        return []

    status = PatternStatus.FORMING
    if last.close > peak.price:
        status = PatternStatus.BREAKOUT

    return [
        Pattern(
            kind=PatternKind.W_PATTERN,
            symbol=series.symbol,
            timeframe=series.timeframe,
            status=status,
            swings=list(window),
            confirmation_price=last.close if status == PatternStatus.BREAKOUT else None,
            invalidation_price=min(l1.price, l2.price),
            metadata={
                "direction": "BUY",
                "neckline_price": peak.price,
                "reference_high": peak.price,
                "reference_low": min(l1.price, l2.price),
            },
            created_at=l1.timestamp,
            updated_at=now_ist(),
        )
    ]
