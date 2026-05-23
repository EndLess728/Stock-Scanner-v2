"""Inside Candle Breakout Setup.

Reference candle:  09:15  (5-min, configurable)
Inside candles:    09:20, 09:25, 09:30 — each must satisfy:
                       high < ref.high  AND  low > ref.low
After all 3 inside candles form, any subsequent 5-min candle that:
  - CLOSES above ref.high  -> 🟢 BUY breakout
  - CLOSES below ref.low   -> 🔴 SELL breakdown

Rules:
- At most one BUY and one SELL per index per day (enforced by AlertEngine quotas).
- State persisted via StateEngine; survives restarts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from models.candle import Candle
from models.signal import Signal, SignalDirection
from setups.base_setup import BaseSetup
from utils.logger import log
from utils.time_utils import IST


class InsideCandleSetup(BaseSetup):
    name = "inside_candle"

    DEFAULT_REFERENCE = "09:15"
    DEFAULT_INSIDE = ("09:20", "09:25", "09:30")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reference_hhmm: str = self.config.get("reference_candle", self.DEFAULT_REFERENCE)
        inside_cfg = self.config.get("inside_candles", list(self.DEFAULT_INSIDE))
        self.inside_hhmms: tuple[str, ...] = tuple(inside_cfg)
        log.info(
            f"InsideCandleSetup armed | ref={self.reference_hhmm} "
            f"inside={self.inside_hhmms}"
        )

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    async def _get_state(self, symbol: str) -> Dict[str, Any]:
        return await self.state.get(self.name, symbol)

    async def _set_state(self, symbol: str, state: Dict[str, Any]) -> None:
        await self.state.set(self.name, symbol, state)

    # ------------------------------------------------------------------
    # Candle handler
    # ------------------------------------------------------------------
    async def on_candle(self, symbol: str, candle: Candle) -> Optional[Signal]:
        if not candle.is_closed:
            return None

        hhmm = candle.start.astimezone(IST).strftime("%H:%M")
        state = await self._get_state(symbol)

        # --- 1. Capture reference candle -----------------------------------
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
            }
            await self._set_state(symbol, state)
            log.info(
                f"[{symbol}] Inside-candle reference captured @ {hhmm}: "
                f"H={candle.high} L={candle.low}"
            )
            return None

        ref = state.get("reference")
        if not ref:
            return None  # No reference yet today

        ref_high = float(ref["high"])
        ref_low = float(ref["low"])

        # --- 2. Validate inside candles ------------------------------------
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
            else:
                log.info(
                    f"[{symbol}] Candle @ {hhmm} NOT inside "
                    f"(H={candle.high}, L={candle.low}; ref H={ref_high} L={ref_low}) — setup invalidated"
                )
                state["reference"] = None
                state["inside_seen"] = []
                state["armed"] = False
                await self._set_state(symbol, state)
            return None

        # --- 3. Breakout detection -----------------------------------------
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
