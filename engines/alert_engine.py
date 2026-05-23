"""Alert engine.

Responsible for:
- Rendering Signal objects into rich Telegram messages
- Deduplication (idempotent per `dedup_key`)
- Daily quotas (max BUY / SELL per index per setup)
- Rate-limited delivery to all configured chats
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from config.settings import AppConfig
from database.sqlite import Database
from models.signal import AlertPayload, Signal, SignalDirection
from utils.logger import log
from utils.time_utils import now_ist


# Telegram sender signature: send(chat_id, AlertPayload) -> awaitable
TelegramSender = Callable[[int, AlertPayload], Awaitable[bool]]


class AlertEngine:
    """Renders and dispatches signals as Telegram alerts."""

    def __init__(
        self,
        db: Database,
        config: AppConfig,
        sender: TelegramSender,
        chat_ids: list[int],
    ) -> None:
        self.db = db
        self.config = config
        self.sender = sender
        self.chat_ids = list(chat_ids)
        self._lock = asyncio.Lock()
        self._minute_bucket = 0
        self._sent_this_minute = 0

    def reload_config(self, config: AppConfig, chat_ids: Optional[list[int]] = None) -> None:
        self.config = config
        if chat_ids is not None:
            self.chat_ids = list(chat_ids)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    @staticmethod
    def render(signal: Signal) -> AlertPayload:
        """Render a Signal into the Markdown alert format."""
        setup = signal.setup
        index = signal.index
        time_str = signal.time_str

        if setup == "inside_candle":
            if signal.direction == SignalDirection.BUY:
                title = "🟢 *INSIDE CANDLE BREAKOUT BUY*"
                price_label = "Breakout Price"
            else:
                title = "🔴 *INSIDE CANDLE BREAKDOWN SELL*"
                price_label = "Breakdown Price"

            text = (
                f"{title}\n\n"
                f"*Index:* {index}\n"
                f"*Time:* {time_str}\n"
                f"*{price_label}:* {signal.price:.2f}\n"
                f"*Reference High:* {signal.reference_high:.2f}\n"
                f"*Reference Low:* {signal.reference_low:.2f}\n"
            )
        else:
            arrow = "🟢" if signal.direction == SignalDirection.BUY else "🔴"
            text = (
                f"{arrow} *{setup.upper()} {signal.direction.value}*\n\n"
                f"*Index:* {index}\n"
                f"*Time:* {time_str}\n"
                f"*Price:* {signal.price:.2f}\n"
            )
            if signal.reference_high is not None:
                text += f"*Reference High:* {signal.reference_high:.2f}\n"
            if signal.reference_low is not None:
                text += f"*Reference Low:* {signal.reference_low:.2f}\n"

        return AlertPayload(
            text=text,
            parse_mode=signal.metadata.get("parse_mode", "Markdown"),
            signal=signal,
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    async def dispatch(self, signal: Signal) -> bool:
        """Persist (if new) and broadcast a signal. Returns True if sent."""
        async with self._lock:
            # Per-day quota
            setup_cfg = self.config.setups.get(signal.setup)
            if setup_cfg is not None:
                cap = (
                    setup_cfg.max_buy_alerts_per_day
                    if signal.direction == SignalDirection.BUY
                    else setup_cfg.max_sell_alerts_per_day
                )
                count = await self.db.count_alerts_today(
                    signal.setup, signal.index, signal.direction.value, now_ist().date()
                )
                if count >= cap:
                    log.info(
                        f"Skipping alert (quota): {signal.dedup_key()} count={count} cap={cap}"
                    )
                    return False

            # Idempotency
            inserted = await self.db.insert_alert(
                dedup_key=signal.dedup_key(),
                setup=signal.setup,
                index_name=signal.index,
                direction=signal.direction.value,
                price=signal.price,
                payload=signal.model_dump(mode="json"),
            )
            if not inserted:
                log.info(f"Duplicate alert suppressed: {signal.dedup_key()}")
                return False

            payload = self.render(signal)
            await self._rate_limit_wait()
            await self._broadcast(payload)
            log.success(f"ALERT sent: {signal.dedup_key()} @ {signal.price}")
            return True

    # ------------------------------------------------------------------
    # Informational notifications (progress / armed / invalidated)
    # ------------------------------------------------------------------
    async def notify(
        self,
        text: str,
        dedup_key: Optional[str] = None,
        parse_mode: str = "Markdown",
    ) -> bool:
        """Broadcast an informational message.

        - If `dedup_key` is provided, ensures the message is sent at most
          once across restarts (uses the `alerts` table with
          setup="__notification__").
        - Otherwise, fires unconditionally.
        """
        async with self._lock:
            if dedup_key is not None:
                inserted = await self.db.insert_alert(
                    dedup_key=dedup_key,
                    setup="__notification__",
                    index_name="",
                    direction="INFO",
                    price=0.0,
                    payload={"text": text},
                )
                if not inserted:
                    log.info(f"Duplicate notification suppressed: {dedup_key}")
                    return False

            payload = AlertPayload(text=text, parse_mode=parse_mode)
            await self._rate_limit_wait()
            await self._broadcast(payload)
            log.success(f"NOTIFY sent: {dedup_key or '(no-key)'}")
            return True

    async def _rate_limit_wait(self) -> None:
        """Cooperative rate-limit (per-minute global)."""
        from time import time

        bucket = int(time() // 60)
        if bucket != self._minute_bucket:
            self._minute_bucket = bucket
            self._sent_this_minute = 0
        cap = max(1, self.config.telegram.rate_limit_per_minute)
        if self._sent_this_minute >= cap:
            await asyncio.sleep(60 - (time() % 60))
            self._minute_bucket = int(time() // 60)
            self._sent_this_minute = 0
        self._sent_this_minute += 1

    async def _broadcast(self, payload: AlertPayload) -> None:
        if not self.chat_ids:
            log.warning("No chat IDs configured; skipping broadcast.")
            return
        results = await asyncio.gather(
            *[self.sender(chat_id, payload) for chat_id in self.chat_ids],
            return_exceptions=True,
        )
        for chat, res in zip(self.chat_ids, results):
            if isinstance(res, Exception):
                log.error(f"Telegram send to {chat} failed: {res!r}")
            elif res is False:
                log.warning(f"Telegram send to {chat} returned False")


__all__ = ["AlertEngine", "TelegramSender"]
