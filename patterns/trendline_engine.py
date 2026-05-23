"""Trendline regression / projection utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

from models.pattern import SwingPoint


@dataclass
class Trendline:
    """A simple y = m*x + c line, defined in candle-index space."""

    slope: float
    intercept: float
    points: List[Tuple[int, float]]
    kind: str  # "resistance" | "support"

    def value_at(self, x: int) -> float:
        return self.slope * x + self.intercept

    def is_ascending(self) -> bool:
        return self.slope > 0

    def is_descending(self) -> bool:
        return self.slope < 0

    def is_horizontal(self, tol: float = 1e-4) -> bool:
        return abs(self.slope) < tol


def fit_line(points: Sequence[Tuple[int, float]]) -> Tuple[float, float]:
    """Linear regression on (x, y) points. Returns (slope, intercept)."""
    if len(points) < 2:
        return 0.0, points[0][1] if points else 0.0
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    slope, intercept = np.polyfit(xs, ys, 1)
    return float(slope), float(intercept)


def trendline_through_highs(swings: Sequence[SwingPoint]) -> Trendline | None:
    pts = [(s.index_in_series, s.price) for s in swings if s.kind.value == "HIGH"]
    if len(pts) < 2:
        return None
    slope, intercept = fit_line(pts)
    return Trendline(slope=slope, intercept=intercept, points=pts, kind="resistance")


def trendline_through_lows(swings: Sequence[SwingPoint]) -> Trendline | None:
    pts = [(s.index_in_series, s.price) for s in swings if s.kind.value == "LOW"]
    if len(pts) < 2:
        return None
    slope, intercept = fit_line(pts)
    return Trendline(slope=slope, intercept=intercept, points=pts, kind="support")


def count_touches(
    line: Trendline,
    candles_highs_lows: Sequence[Tuple[int, float, float]],  # (idx, high, low)
    tolerance_pct: float = 0.002,
) -> int:
    """Count how many candles touch the trendline within `tolerance_pct`."""
    touches = 0
    for idx, high, low in candles_highs_lows:
        y = line.value_at(idx)
        tol = abs(y) * tolerance_pct
        if line.kind == "resistance" and abs(high - y) <= tol:
            touches += 1
        elif line.kind == "support" and abs(low - y) <= tol:
            touches += 1
    return touches


__all__ = [
    "Trendline",
    "fit_line",
    "trendline_through_highs",
    "trendline_through_lows",
    "count_touches",
]
