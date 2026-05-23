"""Pattern recognition models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PatternKind(str, Enum):
    HEAD_SHOULDERS = "HEAD_AND_SHOULDERS"
    INVERSE_HEAD_SHOULDERS = "INVERSE_HEAD_AND_SHOULDERS"
    M_PATTERN = "M_PATTERN"
    W_PATTERN = "W_PATTERN"
    PARALLEL_CHANNEL = "PARALLEL_CHANNEL"
    FIBONACCI = "FIBONACCI"


class PatternStatus(str, Enum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"


class SwingKind(str, Enum):
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
    swings: List[SwingPoint] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    confirmation_price: Optional[float] = None
    invalidation_price: Optional[float] = None


__all__ = [
    "PatternKind",
    "PatternStatus",
    "SwingKind",
    "SwingPoint",
    "Pattern",
]
