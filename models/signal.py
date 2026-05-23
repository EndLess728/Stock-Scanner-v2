"""Signal / alert payload models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from utils.time_utils import IST, now_ist


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Signal(BaseModel):
    """A trade-idea signal emitted by a Setup or Pattern."""

    setup: str
    index: str
    direction: SignalDirection
    price: float
    reference_high: Optional[float] = None
    reference_low: Optional[float] = None
    timeframe: str = "5min"
    timestamp: datetime = Field(default_factory=now_ist)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def time_str(self) -> str:
        return self.timestamp.astimezone(IST).strftime("%H:%M")

    @property
    def date_str(self) -> str:
        return self.timestamp.astimezone(IST).strftime("%Y-%m-%d")

    def dedup_key(self) -> str:
        """Idempotency key — at most one matching alert per day."""
        return f"{self.date_str}|{self.setup}|{self.index}|{self.direction.value}"


class AlertPayload(BaseModel):
    """Telegram-rendered alert envelope."""

    text: str
    parse_mode: str = "Markdown"
    silent: bool = False
    signal: Optional[Signal] = None


__all__ = ["Signal", "SignalDirection", "AlertPayload"]
