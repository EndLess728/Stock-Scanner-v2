"""Async SQLite repository.

Stores:
- sent alerts (idempotency / dedup)
- setup_state (per index + setup per day)
- user_preferences (per chat)
- session metadata
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from utils.logger import log

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT UNIQUE NOT NULL,
    setup TEXT NOT NULL,
    index_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    price REAL NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_date_setup_index
    ON alerts(setup, index_name, created_at);

CREATE TABLE IF NOT EXISTS setup_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    setup TEXT NOT NULL,
    index_name TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(trade_date, setup, index_name)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    chat_id INTEGER PRIMARY KEY,
    preferences TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    """Thin async wrapper around aiosqlite."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        log.info(f"SQLite ready at {self.path}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call await db.connect() first.")
        return self._conn

    # ------------------------------ alerts ------------------------------
    async def has_alert(self, dedup_key: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM alerts WHERE dedup_key = ? LIMIT 1", (dedup_key,)
        ) as cur:
            return await cur.fetchone() is not None

    async def insert_alert(
        self,
        dedup_key: str,
        setup: str,
        index_name: str,
        direction: str,
        price: float,
        payload: Dict[str, Any],
    ) -> bool:
        """Insert if not exists. Returns True if inserted."""
        try:
            await self.conn.execute(
                """
                INSERT INTO alerts (dedup_key, setup, index_name, direction, price, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedup_key,
                    setup,
                    index_name,
                    direction,
                    price,
                    json.dumps(payload, default=str),
                    datetime.utcnow().isoformat(),
                ),
            )
            await self.conn.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def count_alerts_today(
        self, setup: str, index_name: str, direction: str, day: date
    ) -> int:
        async with self.conn.execute(
            """
            SELECT COUNT(*) FROM alerts
            WHERE setup = ? AND index_name = ? AND direction = ?
              AND substr(created_at, 1, 10) = ?
            """,
            (setup, index_name, direction, day.isoformat()),
        ) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    # --------------------------- setup_state ---------------------------
    async def save_setup_state(
        self,
        trade_date: date,
        setup: str,
        index_name: str,
        state: Dict[str, Any],
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO setup_state (trade_date, setup, index_name, state, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, setup, index_name)
            DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at
            """,
            (
                trade_date.isoformat(),
                setup,
                index_name,
                json.dumps(state, default=str),
                datetime.utcnow().isoformat(),
            ),
        )
        await self.conn.commit()

    async def load_setup_state(
        self, trade_date: date, setup: str, index_name: str
    ) -> Optional[Dict[str, Any]]:
        async with self.conn.execute(
            "SELECT state FROM setup_state WHERE trade_date=? AND setup=? AND index_name=?",
            (trade_date.isoformat(), setup, index_name),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    async def reset_setup_state(self, trade_date: date) -> int:
        """Clear all states older than `trade_date` (keep today, delete past)."""
        cur = await self.conn.execute(
            "DELETE FROM setup_state WHERE trade_date < ?", (trade_date.isoformat(),)
        )
        await self.conn.commit()
        return cur.rowcount

    # ------------------------- user preferences -------------------------
    async def upsert_user_pref(self, chat_id: int, prefs: Dict[str, Any]) -> None:
        await self.conn.execute(
            """
            INSERT INTO user_preferences (chat_id, preferences, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                preferences=excluded.preferences, updated_at=excluded.updated_at
            """,
            (chat_id, json.dumps(prefs, default=str), datetime.utcnow().isoformat()),
        )
        await self.conn.commit()

    async def get_user_pref(self, chat_id: int) -> Dict[str, Any]:
        async with self.conn.execute(
            "SELECT preferences FROM user_preferences WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
            return json.loads(row[0]) if row else {}

    async def list_user_chats(self) -> List[int]:
        async with self.conn.execute("SELECT chat_id FROM user_preferences") as cur:
            return [int(r[0]) async for r in cur]

    # --------------------------- session_meta ---------------------------
    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO session_meta (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, datetime.utcnow().isoformat()),
        )
        await self.conn.commit()

    async def get_meta(self, key: str) -> Optional[str]:
        async with self.conn.execute("SELECT value FROM session_meta WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


# Module-level singleton
_db: Optional[Database] = None


def get_database(path: Optional[str] = None) -> Database:
    """Return process-wide Database singleton."""
    global _db
    if _db is None:
        from config import settings

        _db = Database(path or settings.database_path)
    return _db


__all__ = ["Database", "get_database"]
