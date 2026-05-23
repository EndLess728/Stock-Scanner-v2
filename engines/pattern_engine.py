"""Pattern recognition engine — pluggable, future-ready.

Holds a registry of pattern detectors. Each detector receives the latest
closed candle (or full series) and returns a list of `Pattern` objects
in any lifecycle state (FORMING / CONFIRMED / INVALIDATED / BREAKOUT / RETEST).

Confirmed/breakout/retest patterns are converted to Signals and pushed
through the AlertEngine.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable, Dict, List, Optional

from config.settings import AppConfig
from engines.alert_engine import AlertEngine
from models.candle import Candle, CandleSeries
from models.pattern import Pattern, PatternStatus
from models.signal import Signal, SignalDirection
from patterns.swing_detector import SwingDetector
from utils.logger import log
from utils.time_utils import now_ist


# Pattern detector contract:
#   detect(series: CandleSeries, config: dict) -> List[Pattern]
PatternDetector = Callable[[CandleSeries, Dict[str, Any]], List[Pattern]]

PATTERN_DETECTORS: Dict[str, PatternDetector] = {}


def register_detector(name: str) -> Callable[[PatternDetector], PatternDetector]:
    """Decorator used by pattern modules to register themselves."""

    def _wrap(fn: PatternDetector) -> PatternDetector:
        PATTERN_DETECTORS[name] = fn
        return fn

    return _wrap


class PatternEngine:
    """Runs registered pattern detectors against rolling candle series."""

    def __init__(self, config: AppConfig, alerts: AlertEngine) -> None:
        self.config = config
        self.alerts = alerts
        self._series_cache: Dict[tuple[str, str], CandleSeries] = {}
        self.swing_detector = SwingDetector(
            lookback=config.patterns.model_dump().get("swing", {}).get("lookback", 20),
            sensitivity=config.patterns.model_dump().get("swing", {}).get("sensitivity", 0.0015),
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def discover_patterns() -> Dict[str, PatternDetector]:
        import patterns as _pkg

        for _, modname, _ in pkgutil.iter_modules(_pkg.__path__):
            full = f"{_pkg.__name__}.{modname}"
            try:
                importlib.import_module(full)
            except Exception as exc:  # pragma: no cover
                log.exception(f"Failed importing pattern module {full}: {exc!r}")
        return dict(PATTERN_DETECTORS)

    def build(self) -> None:
        self.discover_patterns()
        if not self.config.patterns.enabled:
            log.info("Pattern engine master switch is OFF")
            return
        log.info(f"Pattern detectors loaded: {list(PATTERN_DETECTORS.keys())}")

    def reload_config(self, config: AppConfig) -> None:
        self.config = config
        cfg = config.patterns.model_dump()
        self.swing_detector = SwingDetector(
            lookback=cfg.get("swing", {}).get("lookback", 20),
            sensitivity=cfg.get("swing", {}).get("sensitivity", 0.0015),
        )

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    def update_series(self, symbol: str, timeframe: str, candle: Candle) -> CandleSeries:
        key = (symbol, timeframe)
        series = self._series_cache.setdefault(
            key, CandleSeries(symbol=symbol, timeframe=timeframe, max_size=500)
        )
        series.append(candle)
        return series

    async def on_candle_close(self, symbol: str, timeframe: str, candle: Candle) -> None:
        series = self.update_series(symbol, timeframe, candle)
        if not self.config.patterns.enabled:
            return

        cfg = self.config.patterns.model_dump()
        for name, detector in PATTERN_DETECTORS.items():
            pat_cfg = cfg.get(name, {})
            if not isinstance(pat_cfg, dict) or not pat_cfg.get("enabled", False):
                continue
            try:
                results = detector(series, pat_cfg)
            except Exception as exc:  # pragma: no cover
                log.exception(f"Pattern '{name}' failed: {exc!r}")
                continue

            for pattern in results:
                if pattern.status in {
                    PatternStatus.CONFIRMED,
                    PatternStatus.BREAKOUT,
                    PatternStatus.RETEST,
                }:
                    signal = self._pattern_to_signal(pattern)
                    if signal is not None:
                        await self.alerts.dispatch(signal)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _pattern_to_signal(pattern: Pattern) -> Optional[Signal]:
        direction_meta = pattern.metadata.get("direction", "BUY").upper()
        try:
            direction = SignalDirection(direction_meta)
        except ValueError:
            direction = SignalDirection.BUY
        price = pattern.confirmation_price or (pattern.swings[-1].price if pattern.swings else 0.0)
        if price <= 0:
            return None
        return Signal(
            setup=f"pattern_{pattern.kind.value.lower()}",
            index=pattern.symbol,
            direction=direction,
            price=price,
            reference_high=pattern.metadata.get("reference_high"),
            reference_low=pattern.metadata.get("reference_low"),
            timeframe=pattern.timeframe,
            timestamp=now_ist(),
            metadata={"pattern": pattern.model_dump(mode="json")},
        )


__all__ = ["PatternEngine", "register_detector", "PATTERN_DETECTORS"]
