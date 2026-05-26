"""Pattern recognition models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PatternKind(StrEnum):
    HEAD_SHOULDERS = "HEAD_AND_SHOULDERS"
    INVERSE_HEAD_SHOULDERS = "INVERSE_HEAD_AND_SHOULDERS"
    M_PATTERN = "M_PATTERN"
    W_PATTERN = "W_PATTERN"
    PARALLEL_CHANNEL = "PARALLEL_CHANNEL"
    FIBONACCI = "FIBONACCI"


class PatternStatus(StrEnum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class SwingPoint(BaseModel):
    """A pivot high/low detected on a candle series."""

    timestamp: datetime
    price: float
    kind: SwingKind
    index_in_series: int


class Pattern(BaseModel):
    """A pattern instance tracked by the PatternEngine."""

    kind: PatternKind
    symbol: str
    timeframe: str
    status: PatternStatus = PatternStatus.FORMING
    swings: list[SwingPoint] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    confirmation_price: float | None = None
    invalidation_price: float | None = None


__all__ = [
    "PatternKind",
    "PatternStatus",
    "SwingKind",
    "SwingPoint",
    "Pattern",
]
