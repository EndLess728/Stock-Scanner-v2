"""Base class + registry for trading setups (Strategy Pattern).

Every concrete setup MUST:
- subclass `BaseSetup`
- set a class-level `name` attribute (string, snake_case)
- implement `on_candle(symbol, candle) -> Optional[Signal]`

Subclassing auto-registers the setup in `SETUP_REGISTRY` so the SetupEngine
can discover and instantiate it without any core engine changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from models.candle import Candle
from models.signal import Signal
from utils.logger import log

if TYPE_CHECKING:
    from engines.alert_engine import AlertEngine
    from engines.state_engine import StateEngine


SETUP_REGISTRY: dict[str, type[BaseSetup]] = {}


class BaseSetup(ABC):
    """Abstract base class for every alert setup."""

    # Subclass MUST set this to the YAML key (e.g. "inside_candle")
    name: str = ""

    # Set to True if the setup wants intra-candle (tick-level) updates
    uses_ticks: bool = False

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise TypeError(f"{cls.__name__} must define class attribute `name`")
        SETUP_REGISTRY[cls.name] = cls
        log.debug(f"Registered setup: {cls.name} -> {cls.__name__}")

    def __init__(
        self,
        name: str,
        config: dict[str, Any],
        state: StateEngine,
        alerts: AlertEngine,
    ) -> None:
        self.name = name
        self.config = config
        self.state = state
        self.alerts = alerts

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    @abstractmethod
    async def on_candle(self, symbol: str, candle: Candle) -> Signal | None:
        """Process a newly-closed candle. Return a Signal to fire an alert."""

    async def on_tick(self, symbol: str, candle: Candle) -> Signal | None:
        """Process an in-progress candle (only invoked if `uses_ticks=True`)."""
        return None


__all__ = ["BaseSetup", "SETUP_REGISTRY"]
