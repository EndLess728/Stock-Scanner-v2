"""Market data service.

Wires:
  AngelOne REST + WS  ->  CandleEngine  ->  SetupEngine + PatternEngine

Responsibilities:
- Seed today's candles from REST history (so a mid-day restart works)
- Subscribe to live ticks via WebSocket
- Push ticks into CandleEngine
- Manage health monitoring (stale-tick reconnect)
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any

from broker.angelone_client import AngelOneClient
from broker.websocket_client import AngelOneWebSocket
from config.settings import AppConfig
from engines.candle_engine import CandleEngine
from engines.pattern_engine import PatternEngine
from engines.setup_engine import SetupEngine
from models.candle import Candle
from utils.logger import log
from utils.time_utils import IST, is_market_open, now_ist, parse_hhmm


class MarketDataService:
    """Bridges the broker with the engines."""

    def __init__(
        self,
        config: AppConfig,
        broker: AngelOneClient,
        candle_engine: CandleEngine,
        setup_engine: SetupEngine,
        pattern_engine: PatternEngine,
    ) -> None:
        self.config = config
        self.broker = broker
        self.candle_engine = candle_engine
        self.setup_engine = setup_engine
        self.pattern_engine = pattern_engine
        self.ws: AngelOneWebSocket | None = None

        # token -> index name (e.g. "99926000" -> "NIFTY50")
        self._token_to_index: dict[str, str] = {}
        self._healthcheck_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def reload_config(self, config: AppConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _build_subscriptions(self) -> list[dict[str, Any]]:
        by_exchange: dict[int, list[str]] = {}
        for name, idx in self.config.indices.items():
            if not idx.enabled:
                continue
            self._token_to_index[idx.token] = name
            by_exchange.setdefault(idx.exchange_type, []).append(idx.token)
        return [
            {"exchangeType": ex_type, "tokens": tokens} for ex_type, tokens in by_exchange.items()
        ]

    async def _seed_history(self) -> None:
        """Backfill today's candles from REST so engines have context on restart."""
        tf = self.config.timeframes.default
        interval = AngelOneClient.interval_for_timeframe(tf)

        now = now_ist()
        open_t = parse_hhmm(self.config.market.open_time)
        market_open = IST.localize(datetime.combine(now.date(), open_t))
        from_dt = market_open - timedelta(minutes=5)
        to_dt = now

        if to_dt <= from_dt:
            log.info("Skipping history seed (pre-market)")
            return

        for name, idx in self.config.indices.items():
            if not idx.enabled:
                continue
            self.candle_engine.track(name, tf)
            try:
                raw = await self.broker.get_candles(
                    exchange=idx.exchange,
                    symbol_token=idx.token,
                    interval=interval,
                    from_dt=from_dt,
                    to_dt=to_dt,
                )
            except Exception as exc:
                log.warning(f"History seed failed for {name}: {exc!r}")
                continue

            candles: list[Candle] = []
            for row in raw:
                # [timestamp, open, high, low, close, volume]
                ts_raw = row[0]
                if isinstance(ts_raw, str):
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        ts = datetime.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S")
                else:
                    ts = ts_raw
                if ts.tzinfo is None:
                    ts = IST.localize(ts)
                candles.append(
                    Candle(
                        symbol=name,
                        timeframe=tf,
                        start=ts.astimezone(IST),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]) if len(row) > 5 else 0.0,
                        is_closed=True,
                    )
                )
            self.candle_engine.seed_history(name, tf, candles)

            # Replay through setup engine so setup state catches up after a restart
            for c in candles:
                await self.setup_engine.on_candle_close(name, tf, c)
                await self.pattern_engine.on_candle_close(name, tf, c)

    # ------------------------------------------------------------------
    # Tick callback
    # ------------------------------------------------------------------
    async def _on_tick(self, token: str, ltp: float, ts_ms: int, raw: dict[str, Any]) -> None:
        index = self._token_to_index.get(token)
        if index is None:
            return
        ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=IST)

        if not is_market_open(
            ts,
            self.config.market.open_time,
            self.config.market.close_time,
            self.config.market.trading_days,
            self.config.market.holidays,
        ):
            return

        await self.candle_engine.on_tick_received(index, ltp, ts)

    # ------------------------------------------------------------------
    # Health monitor
    # ------------------------------------------------------------------
    async def _healthcheck(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.sleep(15.0)
                if self.ws is None:
                    continue
                stale = self.ws.seconds_since_last_tick()
                connected = self.ws.is_connected()
                if (
                    connected
                    and stale > 60
                    and is_market_open(
                        now_ist(),
                        self.config.market.open_time,
                        self.config.market.close_time,
                        self.config.market.trading_days,
                        self.config.market.holidays,
                    )
                ):
                    log.warning(f"Stale ticks ({stale:.0f}s) — kicking WebSocket")
                    await self.ws.stop()
                    await asyncio.sleep(1.0)
                    await self.ws.start()
                    self.ws.set_subscriptions(self._build_subscriptions())
            except Exception as exc:  # pragma: no cover
                log.exception(f"Healthcheck error: {exc!r}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        log.info("MarketDataService starting…")
        # 1) Track all enabled indices on default timeframe
        tf = self.config.timeframes.default
        for name, idx in self.config.indices.items():
            if idx.enabled:
                self.candle_engine.track(name, tf)

        # 2) Wire engines to candle events
        async def _on_close(symbol: str, timeframe: str, candle: Candle) -> None:
            await self.setup_engine.on_candle_close(symbol, timeframe, candle)
            await self.pattern_engine.on_candle_close(symbol, timeframe, candle)

        self.candle_engine.on_close(_on_close)
        self.candle_engine.on_tick(self.setup_engine.on_candle_tick)
        await self.candle_engine.start_closer(interval_sec=1.0)

        # 3) Login + history seed
        await self.broker.login()
        await self._seed_history()

        # 4) WebSocket
        subscriptions = self._build_subscriptions()
        self.ws = AngelOneWebSocket(
            broker=self.broker,
            tick_callback=self._on_tick,
            subscriptions=subscriptions,
        )
        await self.ws.start()

        # 5) Healthcheck
        self._healthcheck_task = asyncio.create_task(self._healthcheck(), name="ws-healthcheck")

        log.success("MarketDataService running")

    async def stop(self) -> None:
        log.info("MarketDataService stopping…")
        self._stop.set()
        if self._healthcheck_task is not None:
            self._healthcheck_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._healthcheck_task
        if self.ws is not None:
            await self.ws.stop()
        await self.candle_engine.stop_closer()
        await self.broker.logout()


__all__ = ["MarketDataService"]
