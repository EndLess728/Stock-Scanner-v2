"""Swing high / low detector — fractal-based with sensitivity threshold."""
from __future__ import annotations

from typing import List

from models.candle import CandleSeries
from models.pattern import SwingKind, SwingPoint


class SwingDetector:
    """Detects pivot highs/lows on a candle series.

    A bar at index `i` is a swing-high if its `high` is strictly greater than
    the highs of `lookback//2` bars on either side, and the move from prior
    swing exceeds `sensitivity` (fractional, e.g. 0.0015 = 0.15%).
    """

    def __init__(self, lookback: int = 10, sensitivity: float = 0.0015) -> None:
        self.lookback = max(2, lookback)
        self.sensitivity = max(0.0, sensitivity)

    def _radius(self) -> int:
        return max(1, self.lookback // 2)

    def detect(self, series: CandleSeries) -> List[SwingPoint]:
        candles = series.candles
        n = len(candles)
        r = self._radius()
        if n < 2 * r + 1:
            return []

        swings: List[SwingPoint] = []
        prev_price = candles[0].close

        for i in range(r, n - r):
            window = candles[i - r : i + r + 1]
            mid = candles[i]
            is_high = all(mid.high >= c.high for c in window) and any(
                mid.high > c.high for c in window if c is not mid
            )
            is_low = all(mid.low <= c.low for c in window) and any(
                mid.low < c.low for c in window if c is not mid
            )

            if is_high:
                if abs(mid.high - prev_price) / max(prev_price, 1e-9) >= self.sensitivity:
                    swings.append(
                        SwingPoint(
                            timestamp=mid.start,
                            price=mid.high,
                            kind=SwingKind.HIGH,
                            index_in_series=i,
                        )
                    )
                    prev_price = mid.high
            elif is_low:
                if abs(mid.low - prev_price) / max(prev_price, 1e-9) >= self.sensitivity:
                    swings.append(
                        SwingPoint(
                            timestamp=mid.start,
                            price=mid.low,
                            kind=SwingKind.LOW,
                            index_in_series=i,
                        )
                    )
                    prev_price = mid.low

        return swings

    def last_swings(self, series: CandleSeries, n: int = 5) -> List[SwingPoint]:
        return self.detect(series)[-n:]


__all__ = ["SwingDetector"]
