"""Per-setup state engine — persists and restores daily state."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from database.sqlite import Database
from utils.logger import log
from utils.time_utils import now_ist


class StateEngine:
    """Persists setup state per (trade_date, setup, index)."""

    def __init__(self, db: Database) -> None:
        self.db = db
        # In-memory cache: {(date, setup, index): state}
        self._cache: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    async def get(self, setup: str, index_name: str, day: Optional[date] = None) -> Dict[str, Any]:
        day = day or now_ist().date()
        key = (day.isoformat(), setup, index_name)
        if key in self._cache:
            return self._cache[key]
        loaded = await self.db.load_setup_state(day, setup, index_name) or {}
        self._cache[key] = loaded
        return loaded

    async def set(
        self,
        setup: str,
        index_name: str,
        state: Dict[str, Any],
        day: Optional[date] = None,
    ) -> None:
        day = day or now_ist().date()
        key = (day.isoformat(), setup, index_name)
        self._cache[key] = state
        await self.db.save_setup_state(day, setup, index_name, state)

    async def update(
        self,
        setup: str,
        index_name: str,
        patch: Dict[str, Any],
        day: Optional[date] = None,
    ) -> Dict[str, Any]:
        current = await self.get(setup, index_name, day)
        current.update(patch)
        await self.set(setup, index_name, current, day)
        return current

    async def reset_daily(self, day: Optional[date] = None) -> None:
        day = day or now_ist().date()
        deleted = await self.db.reset_setup_state(day)
        # Drop in-memory cache for older days
        self._cache = {k: v for k, v in self._cache.items() if k[0] == day.isoformat()}
        log.info(f"State reset: pruned {deleted} stale rows older than {day}")


__all__ = ["StateEngine"]
