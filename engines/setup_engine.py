"""Setup engine.

Loads all subclasses of `BaseSetup`, applies config, and routes closed candles
to each setup. New setups added under `setups/` are auto-discovered.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from config.settings import AppConfig
from engines.alert_engine import AlertEngine
from engines.state_engine import StateEngine
from models.candle import Candle
from models.signal import Signal
from setups.base_setup import BaseSetup, SETUP_REGISTRY
from utils.logger import log


class SetupEngine:
    """Owns all setup instances and dispatches closed candles."""

    def __init__(
        self,
        config: AppConfig,
        state: StateEngine,
        alerts: AlertEngine,
    ) -> None:
        self.config = config
        self.state = state
        self.alerts = alerts
        self._setups: Dict[str, BaseSetup] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def discover_setups() -> Dict[str, Type[BaseSetup]]:
        """Import every module under `setups/` so they register themselves."""
        import setups as _setups_pkg

        for _, modname, _ in pkgutil.iter_modules(_setups_pkg.__path__):
            full = f"{_setups_pkg.__name__}.{modname}"
            try:
                importlib.import_module(full)
            except Exception as exc:  # pragma: no cover
                log.exception(f"Failed importing setup module {full}: {exc!r}")
        return dict(SETUP_REGISTRY)

    def build(self) -> None:
        """Instantiate every enabled setup with its config."""
        discovered = self.discover_setups()
        self._setups.clear()

        for name, cls in discovered.items():
            cfg = self.config.setups.get(name)
            if cfg is None or not cfg.enabled:
                log.info(f"Setup '{name}' disabled — skipped")
                continue

            instance = cls(
                name=name,
                config=cfg.model_dump(),
                state=self.state,
                alerts=self.alerts,
            )
            self._setups[name] = instance
            log.info(f"Setup '{name}' registered for indices={cfg.indices}")

    def enabled_names(self) -> List[str]:
        return list(self._setups.keys())

    def enable(self, name: str) -> bool:
        if name not in self.config.setups:
            return False
        self.config.setups[name].enabled = True
        self.build()
        return True

    def disable(self, name: str) -> bool:
        if name not in self.config.setups:
            return False
        self.config.setups[name].enabled = False
        self._setups.pop(name, None)
        return True

    def reload_config(self, config: AppConfig) -> None:
        self.config = config
        self.build()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    async def on_candle_close(self, symbol: str, timeframe: str, candle: Candle) -> None:
        """Forward closed candle to every applicable setup."""
        for name, setup in self._setups.items():
            cfg = self.config.setups.get(name)
            if cfg is None:
                continue
            if cfg.timeframe != timeframe:
                continue
            if symbol not in cfg.indices:
                continue
            try:
                signal: Optional[Signal] = await setup.on_candle(symbol, candle)
                if signal is not None:
                    await self.alerts.dispatch(signal)
            except Exception as exc:  # pragma: no cover
                log.exception(f"Setup '{name}' failed on candle: {exc!r}")

    async def on_candle_tick(self, symbol: str, timeframe: str, candle: Candle) -> None:
        """Optional in-progress hook; default no-op (most setups use close)."""
        for name, setup in self._setups.items():
            cfg = self.config.setups.get(name)
            if cfg is None:
                continue
            if cfg.timeframe != timeframe:
                continue
            if symbol not in cfg.indices:
                continue
            if not getattr(setup, "uses_ticks", False):
                continue
            try:
                signal = await setup.on_tick(symbol, candle)
                if signal is not None:
                    await self.alerts.dispatch(signal)
            except Exception as exc:  # pragma: no cover
                log.exception(f"Setup '{name}' tick handler failed: {exc!r}")


__all__ = ["SetupEngine"]
