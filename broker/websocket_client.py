"""Angel One SmartWebSocketV2 wrapper.

Streams live ticks for subscribed symbols and dispatches them to an async
queue consumed by the candle engine.

Subscription mode 1 (LTP) is sufficient for index spot ticks.
The SDK is synchronous and threadsafe — we drive it from a background
thread and bridge to asyncio via a queue.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore
except ImportError:  # pragma: no cover
    SmartWebSocketV2 = None  # type: ignore

from broker.angelone_client import AngelOneClient
from utils.logger import log


# Tick callback signature: async fn(symbol_token: str, ltp: float, ts_ms: int, raw: dict)
TickCallback = Callable[[str, float, int, Dict[str, Any]], Awaitable[None]]


class AngelOneWebSocket:
    """Live tick streamer with automatic reconnect and heartbeat handling."""

    MODE_LTP = 1
    MODE_QUOTE = 2
    MODE_SNAPQUOTE = 3

    def __init__(
        self,
        broker: AngelOneClient,
        tick_callback: TickCallback,
        subscriptions: Optional[List[Dict[str, Any]]] = None,
        mode: int = MODE_LTP,
        correlation_id: str = "stock_scanner_v2",
    ) -> None:
        if SmartWebSocketV2 is None:
            raise RuntimeError("smartapi-python is not installed.")

        self.broker = broker
        self.tick_callback = tick_callback
        self.subscriptions: List[Dict[str, Any]] = subscriptions or []
        self.mode = mode
        self.correlation_id = correlation_id

        self._ws: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_tick_ts = 0.0
        self._reconnect_backoff = 2.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Spawn WS client in a background thread; non-blocking."""
        await self.broker.ensure_session()
        self._loop = asyncio.get_running_loop()
        self._running = True
        self._thread = threading.Thread(target=self._run_forever, name="angel-ws", daemon=True)
        self._thread.start()
        log.info("Angel WebSocket thread launched")

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close_connection()
            except Exception:  # pragma: no cover
                pass
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        log.info("Angel WebSocket stopped")

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def set_subscriptions(self, subscriptions: List[Dict[str, Any]]) -> None:
        self.subscriptions = subscriptions
        if self._ws is not None and self._connected.is_set():
            try:
                self._ws.subscribe(self.correlation_id, self.mode, subscriptions)
                log.info(f"Resubscribed: {subscriptions}")
            except Exception as exc:  # pragma: no cover
                log.exception(f"Resubscribe failed: {exc!r}")

    # ------------------------------------------------------------------
    # Thread loop
    # ------------------------------------------------------------------
    def _run_forever(self) -> None:
        while self._running:
            try:
                self._connect_once()
            except Exception as exc:
                log.exception(f"WebSocket loop error: {exc!r}")
            if not self._running:
                break
            log.warning(f"WebSocket disconnected; reconnect in {self._reconnect_backoff:.1f}s")
            time.sleep(self._reconnect_backoff)
            self._reconnect_backoff = min(self._reconnect_backoff * 1.5, 30.0)

    def _connect_once(self) -> None:
        self._connected.clear()
        self._ws = SmartWebSocketV2(
            self.broker.auth_token,
            self.broker.api_key,
            self.broker.client_id,
            self.broker.feed_token,
        )

        ws = self._ws

        def on_open(_wsapp: Any) -> None:
            log.success("WebSocket connected")
            self._connected.set()
            self._reconnect_backoff = 2.0
            if self.subscriptions:
                try:
                    ws.subscribe(self.correlation_id, self.mode, self.subscriptions)
                    log.info(f"Subscribed: {self.subscriptions}")
                except Exception as exc:  # pragma: no cover
                    log.exception(f"Subscribe failed: {exc!r}")

        def on_data(_wsapp: Any, message: Any) -> None:
            self._handle_message(message)

        def on_error(_wsapp: Any, error: Any) -> None:
            log.error(f"WebSocket error: {error!r}")

        def on_close(_wsapp: Any) -> None:
            log.warning("WebSocket on_close fired")
            self._connected.clear()

        ws.on_open = on_open
        ws.on_data = on_data
        ws.on_error = on_error
        ws.on_close = on_close

        # Blocks the worker thread until disconnect
        ws.connect()

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------
    def _handle_message(self, message: Any) -> None:
        try:
            if isinstance(message, (bytes, bytearray)):
                # Heartbeat / binary frame
                return
            if isinstance(message, str):
                try:
                    message = json.loads(message)
                except json.JSONDecodeError:
                    return
            if not isinstance(message, dict):
                return

            token = str(message.get("token") or message.get("tk") or "")
            ltp_raw = message.get("last_traded_price") or message.get("ltp")
            if ltp_raw is None:
                return
            # Angel sends LTP in paise (× 100) for some feeds
            ltp = float(ltp_raw) / 100.0 if float(ltp_raw) > 100000 else float(ltp_raw)
            ts_ms = int(message.get("exchange_timestamp") or message.get("ft") or int(time.time() * 1000))
            self._last_tick_ts = time.time()

            if self._loop is None:
                return
            asyncio.run_coroutine_threadsafe(
                self.tick_callback(token, ltp, ts_ms, message), self._loop
            )
        except Exception as exc:  # pragma: no cover
            log.exception(f"Tick handler failed: {exc!r}")

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def seconds_since_last_tick(self) -> float:
        if self._last_tick_ts == 0:
            return float("inf")
        return time.time() - self._last_tick_ts


__all__ = ["AngelOneWebSocket", "TickCallback"]
