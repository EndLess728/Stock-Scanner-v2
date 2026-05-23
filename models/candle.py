"""Candle / OHLCV models."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Iterator, List, Optional

from pydantic import BaseModel, Field

from utils.time_utils import IST


class Candle(BaseModel):
    """Single OHLCV candle. `start` is the candle's open timestamp (IST)."""

    symbol: str
    timeframe: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = False

    @property
    def hhmm(self) -> str:
        return self.start.astimezone(IST).strftime("%H:%M")

    def update(self, price: float, volume: float = 0.0) -> None:
        """Mutate the candle with a new tick price/volume."""
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += volume

    def inside(self, other: "Candle") -> bool:
        """True if this candle's range is strictly inside `other`'s range."""
        return self.high < other.high and self.low > other.low

    def closes_above(self, level: float) -> bool:
        return self.is_closed and self.close > level

    def closes_below(self, level: float) -> bool:
        return self.is_closed and self.close < level

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return (
            f"Candle({self.symbol} {self.timeframe} {self.hhmm} "
            f"O={self.open} H={self.high} L={self.low} C={self.close} "
            f"closed={self.is_closed})"
        )


class CandleSeries(BaseModel):
    """A bounded, ordered series of candles for one symbol+timeframe."""

    symbol: str
    timeframe: str
    max_size: int = 500
    candles: List[Candle] = Field(default_factory=list)

    def append(self, candle: Candle) -> None:
        if self.candles and candle.start <= self.candles[-1].start:
            # Same-bar update -> replace last
            self.candles[-1] = candle
            return
        self.candles.append(candle)
        if len(self.candles) > self.max_size:
            self.candles = self.candles[-self.max_size:]

    def last(self) -> Optional[Candle]:
        return self.candles[-1] if self.candles else None

    def closed_candles(self) -> List[Candle]:
        return [c for c in self.candles if c.is_closed]

    def find_by_hhmm(self, hhmm: str) -> Optional[Candle]:
        """Return the candle that opened at `HH:MM` *today* (IST)."""
        from utils.time_utils import now_ist

        today = now_ist().date()
        for c in self.candles:
            ts = c.start.astimezone(IST)
            if ts.date() == today and ts.strftime("%H:%M") == hhmm:
                return c
        return None

    def __iter__(self) -> Iterator[Candle]:  # type: ignore[override]
        return iter(self.candles)

    def __len__(self) -> int:
        return len(self.candles)


__all__ = ["Candle", "CandleSeries"]
