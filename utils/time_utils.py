"""Timezone and time-bucketing helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Tuple

import pytz

IST = pytz.timezone("Asia/Kolkata")


def now_ist() -> datetime:
    """Return the current time in IST (tz-aware)."""
    return datetime.now(IST)


def parse_hhmm(value: str) -> time:
    """Parse 'HH:MM' to a `time` object."""
    hh, mm = value.strip().split(":")
    return time(hour=int(hh), minute=int(mm))


def is_trading_day(day: date, trading_days: list[str], holidays: list[str]) -> bool:
    """Check trading-day rules.

    `trading_days` should be a list like ["Mon", "Tue", ...].
    """
    weekday_abbrev = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day.weekday()]
    if weekday_abbrev not in trading_days:
        return False
    if day.isoformat() in holidays:
        return False
    return True


def is_market_open(
    moment: datetime,
    open_time: str,
    close_time: str,
    trading_days: list[str],
    holidays: list[str],
) -> bool:
    """Return True iff `moment` is during configured market hours."""
    moment = moment.astimezone(IST) if moment.tzinfo else IST.localize(moment)
    if not is_trading_day(moment.date(), trading_days, holidays):
        return False
    o = parse_hhmm(open_time)
    c = parse_hhmm(close_time)
    return o <= moment.time() <= c


def timeframe_to_seconds(tf: str) -> int:
    """Convert '5min'/'15min'/'1min' to seconds."""
    tf = tf.lower().strip()
    if tf.endswith("min"):
        return int(tf[:-3]) * 60
    if tf.endswith("m"):
        return int(tf[:-1]) * 60
    if tf.endswith("s"):
        return int(tf[:-1])
    raise ValueError(f"Unsupported timeframe: {tf}")


def floor_to_timeframe(moment: datetime, tf: str) -> datetime:
    """Floor a timestamp down to the start of its `tf` candle bucket.

    Example: 09:17:23 floored to 5min -> 09:15:00
    """
    if moment.tzinfo is None:
        moment = IST.localize(moment)
    secs = timeframe_to_seconds(tf)
    # Anchor to market open (09:15) so 5-min buckets align with NSE candles.
    anchor = moment.replace(hour=9, minute=15, second=0, microsecond=0)
    delta = (moment - anchor).total_seconds()
    if delta < 0:
        # Before market open -> previous day's anchor; still floor by tf
        return (moment - timedelta(seconds=moment.second, microseconds=moment.microsecond)).replace(
            microsecond=0
        )
    bucket = int(delta // secs)
    return anchor + timedelta(seconds=bucket * secs)


def next_candle_close(moment: datetime, tf: str) -> datetime:
    """Return the close timestamp of the candle containing `moment`."""
    return floor_to_timeframe(moment, tf) + timedelta(seconds=timeframe_to_seconds(tf))


def candle_window(moment: datetime, tf: str) -> Tuple[datetime, datetime]:
    """Return (open_ts, close_ts) for the candle that contains `moment`."""
    start = floor_to_timeframe(moment, tf)
    return start, start + timedelta(seconds=timeframe_to_seconds(tf))


__all__ = [
    "IST",
    "now_ist",
    "parse_hhmm",
    "is_trading_day",
    "is_market_open",
    "timeframe_to_seconds",
    "floor_to_timeframe",
    "next_candle_close",
    "candle_window",
]
