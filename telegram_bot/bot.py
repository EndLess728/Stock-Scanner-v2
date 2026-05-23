"""Telegram bot wrapper (python-telegram-bot v20+, asyncio).

Provides:
- send_message(chat_id, payload)         — used by AlertEngine
- start / stop lifecycle hooks
- Command handlers (see handlers.py)
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from models.signal import AlertPayload
from utils.logger import log


class TelegramBot:
    """Thin async wrapper around `python-telegram-bot.Application`."""

    def __init__(self, token: str, default_chat_ids: list[int]) -> None:
        self.token = token
        self.default_chat_ids = list(default_chat_ids)
        self.app: Application = ApplicationBuilder().token(token).build()
        self._started = False
        # Injected context (set by main.py) - exposed to handlers via bot_data
        self.context: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def attach_context(self, **context: Any) -> None:
        """Make orchestrator components accessible to command handlers."""
        self.context.update(context)
        self.app.bot_data.update(context)

    def register_command(
        self,
        name: str,
        handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Any],
    ) -> None:
        self.app.add_handler(CommandHandler(name, handler))

    async def start(self) -> None:
        if self._started:
            return
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        self._started = True
        log.success("Telegram bot polling started")

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        finally:
            self._started = False
            log.info("Telegram bot stopped")

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------
    async def send_message(self, chat_id: int, payload: AlertPayload) -> bool:
        try:
            parse_mode = ParseMode.MARKDOWN if payload.parse_mode.lower().startswith("markdown") else None
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=payload.text,
                parse_mode=parse_mode,
                disable_notification=payload.silent,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as exc:
            log.error(f"Telegram send failed chat={chat_id}: {exc!r}")
            return False
        except Exception as exc:  # pragma: no cover
            log.exception(f"Unexpected Telegram error chat={chat_id}: {exc!r}")
            return False

    async def broadcast_text(self, text: str, parse_mode: str = "Markdown") -> None:
        payload = AlertPayload(text=text, parse_mode=parse_mode)
        for cid in self.default_chat_ids:
            await self.send_message(cid, payload)


__all__ = ["TelegramBot"]
