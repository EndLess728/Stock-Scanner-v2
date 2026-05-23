"""Angel One SmartAPI REST client.

Wraps the official `smartapi-python` SDK with:
- TOTP-driven login
- Auto session refresh
- Historical candle fetch
- Defensive error handling
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pyotp

try:
    # Official package (>=1.4): `pip install smartapi-python`
    from SmartApi import SmartConnect  # type: ignore
except ImportError:  # pragma: no cover - dev environments may stub this
    SmartConnect = None  # type: ignore

from config import settings
from utils.helpers import async_retry
from utils.logger import log
from utils.time_utils import IST


class AngelOneClient:
    """Async-friendly wrapper around the synchronous SmartConnect SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or settings.angel_api_key
        self.client_id = client_id or settings.angel_client_id
        self.password = password or settings.angel_password
        self.totp_secret = totp_secret or settings.angel_totp_secret

        if SmartConnect is None:
            raise RuntimeError(
                "smartapi-python is not installed. Run `pip install smartapi-python`."
            )

        self._smart: Any = SmartConnect(api_key=self.api_key)
        self._auth_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._session_started_at: Optional[float] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def _generate_totp(self) -> str:
        return pyotp.TOTP(self.totp_secret).now()

    @async_retry(attempts=3, base_delay=2.0, max_delay=15.0)
    async def login(self) -> Dict[str, str]:
        """Authenticate and generate a session.

        Returns dict with auth_token, refresh_token, feed_token.
        """
        async with self._lock:
            totp = self._generate_totp()
            log.info(f"AngelOne login: client_id={self.client_id} totp=******")
            loop = asyncio.get_running_loop()
            data: Dict[str, Any] = await loop.run_in_executor(
                None,
                lambda: self._smart.generateSession(self.client_id, self.password, totp),
            )
            if not data or data.get("status") is False:
                raise RuntimeError(f"Login failed: {data}")

            payload = data.get("data") or {}
            self._auth_token = payload.get("jwtToken")
            self._refresh_token = payload.get("refreshToken")

            feed_token = await loop.run_in_executor(None, self._smart.getfeedToken)
            self._feed_token = feed_token
            self._session_started_at = _time.time()
            log.success("AngelOne login OK")
            return {
                "auth_token": self._auth_token or "",
                "refresh_token": self._refresh_token or "",
                "feed_token": self._feed_token or "",
            }

    async def ensure_session(self) -> None:
        """Re-login if no session or stale (>6h)."""
        if (
            self._auth_token is None
            or self._session_started_at is None
            or (_time.time() - self._session_started_at) > 6 * 3600
        ):
            await self.login()

    @property
    def feed_token(self) -> str:
        if not self._feed_token:
            raise RuntimeError("Feed token not available. Login first.")
        return self._feed_token

    @property
    def auth_token(self) -> str:
        if not self._auth_token:
            raise RuntimeError("Auth token not available. Login first.")
        return self._auth_token

    # ------------------------------------------------------------------
    # Profile / health
    # ------------------------------------------------------------------
    async def get_profile(self) -> Dict[str, Any]:
        await self.ensure_session()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._smart.getProfile(self._refresh_token))

    async def logout(self) -> None:
        if self._auth_token is None:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, lambda: self._smart.terminateSession(self.client_id))
            log.info("AngelOne session terminated")
        except Exception as exc:  # pragma: no cover - best-effort cleanup
            log.warning(f"AngelOne logout error: {exc!r}")
        finally:
            self._auth_token = None
            self._refresh_token = None
            self._feed_token = None
            self._session_started_at = None

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------
    async def get_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> List[List[Any]]:
        """Fetch historical candles.

        `interval` must be one of Angel One's supported values, e.g.
        'ONE_MINUTE', 'FIVE_MINUTE', 'FIFTEEN_MINUTE', 'ONE_HOUR', 'ONE_DAY'.
        Returns list of [timestamp, open, high, low, close, volume].
        """
        await self.ensure_session()

        if from_dt.tzinfo is None:
            from_dt = IST.localize(from_dt)
        if to_dt.tzinfo is None:
            to_dt = IST.localize(to_dt)

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }
        log.debug(f"getCandleData params={params}")
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, lambda: self._smart.getCandleData(params))
        if not resp or resp.get("status") is False:
            raise RuntimeError(f"getCandleData failed: {resp}")
        return resp.get("data") or []

    @staticmethod
    def interval_for_timeframe(timeframe: str) -> str:
        """Map our internal `5min` to Angel's `FIVE_MINUTE`."""
        mapping = {
            "1min": "ONE_MINUTE",
            "3min": "THREE_MINUTE",
            "5min": "FIVE_MINUTE",
            "10min": "TEN_MINUTE",
            "15min": "FIFTEEN_MINUTE",
            "30min": "THIRTY_MINUTE",
            "60min": "ONE_HOUR",
            "1h": "ONE_HOUR",
            "1d": "ONE_DAY",
        }
        if timeframe not in mapping:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]


__all__ = ["AngelOneClient"]
