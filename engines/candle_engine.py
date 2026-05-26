"""Candle aggregation engine.

Aggregates raw ticks into closed candles per (symbol, timeframe) and emits
two kinds of events to observers:
- `on_tick(symbol, timeframe, candle)`     — in-progress candle updated
- `on_close(symbol, timeframe, candle)`    — candle just closed
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timedelta

from models.candle import Candle, CandleSeries
from utils.logger import log
from utils.time_utils import IST, floor_to_timeframe, timeframe_to_seconds

TickObserver = Callable[[str, str, Candle], Awaitable[None]]
CloseObserver = Callable[[str, str, Candle], Awaitable[None]]


class CandleEngine:
    """Aggregates ticks into candles for any (symbol, timeframe) combination."""

    def __init__(self) -> None:
        self._series: dict[tuple[str, str], CandleSeries] = {}
        self._tick_observers: list[TickObserver] = []
        self._close_observers: list[CloseObserver] = []
        self._tracked: set[tuple[str, str]] = set()
        self._lock = asyncio.Lock()
        self._closer_task: asyncio.Task[None] | None = None
        self._running = False

    # ------------------------------ wiring ------------------------------
    def track(self, symbol: str, timeframe: str) -> None:
        """Register a (symbol, timeframe) pair to aggregate."""
        key = (symbol, timeframe)
        self._tracked.add(key)
        self._series.setdefault(key, CandleSeries(symbol=symbol, timeframe=timeframe))

    def on_tick(self, observer: TickObserver) -> None:
        self._tick_observers.append(observer)

    def on_close(self, observer: CloseObserver) -> None:
        self._close_observers.append(observer)

    def series_for(self, symbol: str, timeframe: str) -> CandleSeries:
        key = (symbol, timeframe)
        if key not in self._series:
            self._series[key] = CandleSeries(symbol=symbol, timeframe=timeframe)
        return self._series[key]

    # ------------------------------ history ----------------------------
    def seed_history(self, symbol: str, timeframe: str, candles: list[Candle]) -> None:
        series = self.series_for(symbol, timeframe)
        for c in candles:
            c.is_closed = True
            series.append(c)
        log.info(f"Seeded {len(candles)} historical candles for {symbol}/{timeframe}")

    # ------------------------------ ingest -----------------------------
    async def on_tick_received(
        self,
        symbol: str,
        price: float,
        ts: datetime,
        volume: float = 0.0,
    ) -> None:
        """Update every tracked timeframe for `symbol` with this tick."""
        ts = ts.astimezone(IST) if ts.tzinfo else IST.localize(ts)
        async with self._lock:
            for sym, tf in list(self._tracked):
                if sym != symbol:
                    continue
                series = self.series_for(sym, tf)
                bucket = floor_to_timeframe(ts, tf)

                last = series.last()
                if last is None or last.start != bucket:
                    # Close prior candle if it exists
                    if last is not None and not last.is_closed:
                        last.is_closed = True
                        await self._fire_close(sym, tf, last)
                    new = Candle(
                        symbol=sym,
                        timeframe=tf,
                        start=bucket,
                        open=price,
                        high=price,
                        low=price,
                        close=price,
                        volume=volume,
                        is_closed=False,
                    )
                    series.append(new)
                    await self._fire_tick(sym, tf, new)
                else:
                    last.update(price, volume)
                    await self._fire_tick(sym, tf, last)

    async def force_close_due(self, now: datetime) -> None:
        """Force-close any in-progress candle whose window ended before `now`.

        Useful when ticks pause briefly across a candle boundary.
        """
        now = now.astimezone(IST) if now.tzinfo else IST.localize(now)
        async with self._lock:
            for (sym, tf), series in self._series.items():
                last = series.last()
                if last is None or last.is_closed:
                    continue
                tf_secs = timeframe_to_seconds(tf)
                close_at = last.start + timedelta(seconds=tf_secs)
                if now >= close_at:
                    last.is_closed = True
                    await self._fire_close(sym, tf, last)

    # ----------------------------- observers ---------------------------
    async def _fire_tick(self, symbol: str, tf: str, candle: Candle) -> None:
        for obs in self._tick_observers:
            try:
                await obs(symbol, tf, candle)
            except Exception as exc:  # pragma: no cover
                log.exception(f"tick observer failed: {exc!r}")

    async def _fire_close(self, symbol: str, tf: str, candle: Candle) -> None:
        log.debug(
            f"CLOSE {symbol} {tf} {candle.hhmm} O={candle.open} H={candle.high} L={candle.low} C={candle.close}"
        )
        for obs in self._close_observers:
            try:
                await obs(symbol, tf, candle)
            except Exception as exc:  # pragma: no cover
                log.exception(f"close observer failed: {exc!r}")

    # ----------------------------- closer task -------------------------
    async def start_closer(self, interval_sec: float = 1.0) -> None:
        """Background task to close candles even when ticks pause."""
        self._running = True

        async def _loop() -> None:
            from utils.time_utils import now_ist

            while self._running:
                try:
                    await self.force_close_due(now_ist())
                except Exception as exc:  # pragma: no cover
                    log.exception(f"closer loop error: {exc!r}")
                await asyncio.sleep(interval_sec)

        self._closer_task = asyncio.create_task(_loop(), name="candle-closer")

    async def stop_closer(self) -> None:
        self._running = False
        if self._closer_task is not None:
            self._closer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._closer_task


__all__ = ["CandleEngine", "TickObserver", "CloseObserver"]
