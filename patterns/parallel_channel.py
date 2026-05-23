"""Parallel channel detector (ascending / descending / horizontal)."""

from __future__ import annotations

from typing import Any, Dict, List

from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus
from patterns.swing_detector import SwingDetector
from patterns.trendline_engine import (
    trendline_through_highs,
    trendline_through_lows,
    count_touches,
)
from utils.time_utils import now_ist


@register_detector("parallel_channel")
def detect_parallel_channel(series: CandleSeries, config: Dict[str, Any]) -> List[Pattern]:
    if not config.get("enabled", False):
        return []
    if len(series) < int(config.get("min_pattern_bars", 20)):
        return []

    min_touches = int(config.get("min_touches", 3))
    parallel_tol = float(config.get("parallel_tolerance", 0.10))
    breakout_tol = float(config.get("breakout_tolerance", 0.001))

    detector = SwingDetector(
        lookback=int(config.get("swing_lookback", 8)),
        sensitivity=float(config.get("swing_sensitivity", 0.0015)),
    )
    swings = detector.detect(series)
    upper = trendline_through_highs(swings)
    lower = trendline_through_lows(swings)
    if upper is None or lower is None:
        return []

    # Parallelism check
    denom = max(abs(upper.slope), abs(lower.slope), 1e-9)
    parallel = abs(upper.slope - lower.slope) / denom <= parallel_tol

    candles_idx = [(i, c.high, c.low) for i, c in enumerate(series.candles)]
    upper_touches = count_touches(upper, candles_idx)
    lower_touches = count_touches(lower, candles_idx)
    if not parallel or upper_touches < min_touches or lower_touches < min_touches:
        return []

    last = series.last()
    if last is None or not last.is_closed:
        return []

    idx_last = len(series.candles) - 1
    upper_y = upper.value_at(idx_last)
    lower_y = lower.value_at(idx_last)
    tol_abs = upper_y * breakout_tol

    direction = None
    status = PatternStatus.FORMING
    if last.close > upper_y + tol_abs:
        status = PatternStatus.BREAKOUT
        direction = "BUY"
    elif last.close < lower_y - tol_abs:
        status = PatternStatus.BREAKOUT
        direction = "SELL"

    if upper.is_ascending() and lower.is_ascending():
        channel_kind = "ascending"
    elif upper.is_descending() and lower.is_descending():
        channel_kind = "descending"
    else:
        channel_kind = "horizontal"

    return [
        Pattern(
            kind=PatternKind.PARALLEL_CHANNEL,
            symbol=series.symbol,
            timeframe=series.timeframe,
            status=status,
            swings=swings[-6:],
            confirmation_price=last.close if status == PatternStatus.BREAKOUT else None,
            metadata={
                "direction": direction or "NONE",
                "channel_kind": channel_kind,
                "upper_slope": upper.slope,
                "lower_slope": lower.slope,
                "upper_touches": upper_touches,
                "lower_touches": lower_touches,
                "reference_high": upper_y,
                "reference_low": lower_y,
            },
            created_at=swings[0].timestamp if swings else now_ist(),
            updated_at=now_ist(),
        )
    ]
