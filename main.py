"""Main orchestrator.

Wires:
  Settings -> Config -> Database
  -> Broker (Angel One) -> CandleEngine -> StateEngine -> AlertEngine
  -> SetupEngine + PatternEngine -> MarketDataService
  -> TelegramBot
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

from broker.angelone_client import AngelOneClient
from config import load_yaml_config, settings
from database.sqlite import get_database
from engines.alert_engine import AlertEngine
from engines.candle_engine import CandleEngine
from engines.pattern_engine import PatternEngine
from engines.setup_engine import SetupEngine
from engines.state_engine import StateEngine
from services.market_data_service import MarketDataService
from telegram_bot.bot import TelegramBot
from telegram_bot.handlers import register_all
from utils.logger import log, setup_logger


class Application:
    """Top-level container that owns every component's lifecycle."""

    def __init__(self) -> None:
        self.config = load_yaml_config()
        setup_logger(
            level=self.config.logging.level,
            log_file=self.config.logging.file,
            rotation=self.config.logging.rotation,
            retention=self.config.logging.retention,
            backtrace=self.config.logging.backtrace,
            diagnose=self.config.logging.diagnose,
        )
        log.info("Bootstrapping Stock Scanner v2")

        self.db = get_database()
        self.broker = AngelOneClient()
        self.candle_engine = CandleEngine()
        self.state = StateEngine(self.db)

        chat_ids = settings.chat_ids
        self.bot = TelegramBot(token=settings.telegram_bot_token, default_chat_ids=chat_ids)

        async def _sender(chat_id: int, payload: Any) -> bool:
            return await self.bot.send_message(chat_id, payload)

        self.alerts = AlertEngine(
            db=self.db,
            config=self.config,
            sender=_sender,
            chat_ids=chat_ids,
        )
        self.setup_engine = SetupEngine(self.config, self.state, self.alerts)
        self.pattern_engine = PatternEngine(self.config, self.alerts)
        self.market_service = MarketDataService(
            config=self.config,
            broker=self.broker,
            candle_engine=self.candle_engine,
            setup_engine=self.setup_engine,
            pattern_engine=self.pattern_engine,
        )

        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Hot reload
    # ------------------------------------------------------------------
    async def reload_config(self) -> None:
        log.info("Reloading config.yaml …")
        new_cfg = load_yaml_config()
        self.config = new_cfg
        self.alerts.reload_config(new_cfg)
        self.setup_engine.reload_config(new_cfg)
        self.pattern_engine.reload_config(new_cfg)
        self.market_service.reload_config(new_cfg)
        if self.market_service.ws is not None:
            self.market_service.ws.set_subscriptions(self.market_service._build_subscriptions())
        # Rewire bot bot_data so handlers see fresh config
        self.bot.attach_context(
            config=self.config,
            setup_engine=self.setup_engine,
            market_service=self.market_service,
            reload_fn=self.reload_config,
        )
        log.success("Config reloaded")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        await self.db.connect()
        await self.state.reset_daily()

        # Build engines
        self.setup_engine.build()
        self.pattern_engine.build()

        # Telegram bot
        register_all(self.bot)
        self.bot.attach_context(
            config=self.config,
            setup_engine=self.setup_engine,
            market_service=self.market_service,
            reload_fn=self.reload_config,
        )
        await self.bot.start()
        if self.config.telegram.send_startup_message:
            await self.bot.broadcast_text(
                "🟢 *Stock Scanner v2 is online.*\nMonitoring: "
                + ", ".join(f"`{i}`" for i in self.config.enabled_indices())
            )

        # Market data
        await self.market_service.start()

        log.success("Application started")

    async def stop(self) -> None:
        log.info("Application shutting down …")
        try:
            if self.config.telegram.send_shutdown_message:
                await self.bot.broadcast_text("🔻 *Stock Scanner v2 going offline.*")
        except Exception:  # pragma: no cover
            pass
        await self.market_service.stop()
        await self.bot.stop()
        await self.db.close()
        log.success("Application stopped")

    async def run_forever(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                # Windows / non-main-thread
                pass

        await self._stop.wait()
        await self.stop()


async def amain() -> None:
    app = Application()
    try:
        await app.run_forever()
    except Exception as exc:
        log.exception(f"Fatal error: {exc!r}")
        raise


if __name__ == "__main__":
    asyncio.run(amain())
