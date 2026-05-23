"""Inside Candle Breakout Setup.

Timeline (default config):
    09:15  reference candle opens
    09:20  09:15 reference candle closes (silent, state-only)
    09:25  09:20 candle closes — checked silently
    09:30  09:25 candle closes — checked silently
    09:35  09:30 (3rd inside) candle closes
              -> 🎯 SETUP ARMED notification fires (only if all 3 inside)
    >09:35  any close above ref.high  -> 🟢 BUY breakout alert
            any close below ref.low   -> 🔴 SELL breakdown alert

Notification policy:
- Exactly one informational notification per day per index: the
  "SETUP ARMED" message when all three inside candles confirm.
- Intermediate progress and invalidation events are logged but NOT sent
  to Telegram.
- Armed notification is de-duplicated per day and gated on freshness so
  a mid-day restart never re-pings the user.

Rules:
- At most one BUY and one SELL per index per day (enforced by AlertEngine quotas).
- State persisted via StateEngine; survives restarts.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

from models.candle import Candle
from models.signal import Signal, SignalDirection
from setups.base_setup import BaseSetup
from utils.logger import log
from utils.time_utils import IST, now_ist, timeframe_to_seconds


# A candle older than this (from its close time) is considered a stale replay
FRESH_WINDOW_SEC = 90


class InsideCandleSetup(BaseSetup):
    name = "inside_candle"

    DEFAULT_REFERENCE = "09:15"
    DEFAULT_INSIDE = ("09:20", "09:25", "09:30")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reference_hhmm: str = self.config.get("reference_candle", self.DEFAULT_REFERENCE)
        inside_cfg = self.config.get("inside_candles", list(self.DEFAULT_INSIDE))
        self.inside_hhmms: tuple[str, ...] = tuple(inside_cfg)
        # When True, send the single "SETUP ARMED" notification once all
        # three inside candles confirm. No other progress / invalidation
        # notifications are ever sent.
        self.notify_armed: bool = bool(self.config.get("notify_armed", True))
        log.info(
            f"InsideCandleSetup armed | ref={self.reference_hhmm} "
            f"inside={self.inside_hhmms} notify_armed={self.notify_armed}"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _get_state(self, symbol: str) -> Dict[str, Any]:
        return await self.state.get(self.name, symbol)

    async def _set_state(self, symbol: str, state: Dict[str, Any]) -> None:
        await self.state.set(self.name, symbol, state)

    @staticmethod
    def _is_fresh(candle: Candle) -> bool:
        """True if the candle just closed (within FRESH_WINDOW_SEC)."""
        close_ts = candle.start + timedelta(seconds=timeframe_to_seconds(candle.timeframe))
        return (now_ist() - close_ts).total_seconds() <= FRESH_WINDOW_SEC

    def _dedup(self, symbol: str, event: str) -> str:
        return f"{now_ist().date().isoformat()}|{self.name}|{symbol}|{event}"

    async def _notify_armed(self, symbol: str, text: str, candle: Candle) -> None:
        """Send the single 'SETUP ARMED' notification (freshness-gated, dedup'd)."""
        if not self.notify_armed:
            return
        if not self._is_fresh(candle):
            log.debug(
                f"Suppressing stale armed notification {symbol} "
                f"(candle close > {FRESH_WINDOW_SEC}s old)"
            )
            return
        await self.alerts.notify(text=text, dedup_key=self._dedup(symbol, "armed"))

    # ------------------------------------------------------------------
    # Candle handler
    # ------------------------------------------------------------------
    async def on_candle(self, symbol: str, candle: Candle) -> Optional[Signal]:
        if not candle.is_closed:
            return None

        hhmm = candle.start.astimezone(IST).strftime("%H:%M")
        state = await self._get_state(symbol)

        # ---- 1. Capture reference candle ---------------------------------
        if hhmm == self.reference_hhmm:
            state = {
                "reference": {
                    "hhmm": hhmm,
                    "high": candle.high,
                    "low": candle.low,
                    "open": candle.open,
                    "close": candle.close,
                },
                "inside_seen": [],
                "armed": False,
                "buy_fired": False,
                "sell_fired": False,
                "invalidated": False,
            }
            await self._set_state(symbol, state)
            log.info(
                f"[{symbol}] Inside-candle reference captured @ {hhmm}: "
                f"H={candle.high} L={candle.low}"
            )
            # Silent: no Telegram notification at reference capture.
            return None

        ref = state.get("reference")
        if not ref or state.get("invalidated"):
            return None  # No reference yet (or already invalidated today)

        ref_high = float(ref["high"])
        ref_low = float(ref["low"])

        # ---- 2. Validate inside candles ---------------------------------
        if hhmm in self.inside_hhmms and hhmm not in state.get("inside_seen", []):
            is_inside = candle.high < ref_high and candle.low > ref_low

            if is_inside:
                seen = list(state.get("inside_seen", []))
                seen.append(hhmm)
                state["inside_seen"] = seen
                state["armed"] = len(seen) >= len(self.inside_hhmms)
                await self._set_state(symbol, state)
                log.info(
                    f"[{symbol}] Inside candle confirmed @ {hhmm} "
                    f"({len(seen)}/{len(self.inside_hhmms)}) armed={state['armed']}"
                )
                # Only notify when the 3rd inside candle confirms.
                if state["armed"]:
                    total = len(self.inside_hhmms)
                    text = (
                        f"🎯 *INSIDE CANDLE SETUP ARMED*\n\n"
                        f"*Index:* {symbol}\n"
                        f"*Time:* {hhmm}\n"
                        f"*Reference High:* {ref_high:.2f}\n"
                        f"*Reference Low:* {ref_low:.2f}\n\n"
                        f"All {total} inside candles confirmed.\n"
                        f"🟢 Close above *{ref_high:.2f}* → BUY\n"
                        f"🔴 Close below *{ref_low:.2f}* → SELL"
                    )
                    await self._notify_armed(symbol, text, candle)
            else:
                # First non-inside candle invalidates the setup for the day.
                # Logged only; no Telegram notification.
                state["reference"] = None
                state["inside_seen"] = []
                state["armed"] = False
                state["invalidated"] = True
                await self._set_state(symbol, state)
                log.info(
                    f"[{symbol}] Candle @ {hhmm} NOT inside "
                    f"(H={candle.high}, L={candle.low}; ref H={ref_high} L={ref_low}) — setup invalidated"
                )
            return None

        # ---- 3. Breakout detection --------------------------------------
        if not state.get("armed"):
            return None

        if not state.get("buy_fired") and candle.closes_above(ref_high):
            state["buy_fired"] = True
            await self._set_state(symbol, state)
            return Signal(
                setup=self.name,
                index=symbol,
                direction=SignalDirection.BUY,
                price=candle.close,
                reference_high=ref_high,
                reference_low=ref_low,
                timeframe=candle.timeframe,
                timestamp=candle.start,
                metadata={"trigger_candle": hhmm},
            )

        if not state.get("sell_fired") and candle.closes_below(ref_low):
            state["sell_fired"] = True
            await self._set_state(symbol, state)
            return Signal(
                setup=self.name,
                index=symbol,
                direction=SignalDirection.SELL,
                price=candle.close,
                reference_high=ref_high,
                reference_low=ref_low,
                timeframe=candle.timeframe,
                timestamp=candle.start,
                metadata={"trigger_candle": hhmm},
            )

        return None


__all__ = ["InsideCandleSetup"]
