"""Backtest the InsideCandleSetup over the last N days using Angel One historical data.

Usage:
    .venv/bin/python scripts/backtest_inside_candle.py [--days 30] [--index NIFTY50,BANKNIFTY]

Notes:
- Uses the EXACT production setup logic from setups/inside_candle.py.
- No Telegram messages are sent (stub AlertEngine captures them in-memory and
  the setup's freshness gate would suppress them for stale candles anyway).
- State is in-memory (per-day reset) so each trading day is independent.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Allow `import config`, `import setups`, etc. when run from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from broker.angelone_client import AngelOneClient  # noqa: E402
from config import load_yaml_config, settings  # noqa: E402
from models.candle import Candle  # noqa: E402
from models.signal import Signal, SignalDirection  # noqa: E402
from setups.inside_candle import InsideCandleSetup  # noqa: E402
from utils.logger import log, setup_logger  # noqa: E402
from utils.time_utils import IST  # noqa: E402


# ---------------------------------------------------------------------------
# Stubs — let the production setup run without touching SQLite or Telegram
# ---------------------------------------------------------------------------
class StubStateEngine:
    """In-memory state. Mirrors the StateEngine surface used by InsideCandleSetup."""

    def __init__(self) -> None:
        self._store: dict[tuple, dict[str, Any]] = {}

    async def get(self, setup: str, symbol: str, day=None) -> dict[str, Any]:
        return dict(self._store.get((setup, symbol), {}))

    async def set(self, setup: str, symbol: str, state: dict[str, Any], day=None) -> None:
        self._store[(setup, symbol)] = dict(state)

    async def update(self, setup: str, symbol: str, patch: dict[str, Any], day=None):
        cur = await self.get(setup, symbol, day)
        cur.update(patch)
        await self.set(setup, symbol, cur, day)
        return cur

    def reset(self) -> None:
        self._store.clear()


class StubAlertEngine:
    """Captures notify() calls (used only when freshness gate passes — rarely)."""

    def __init__(self) -> None:
        self.notifications: list[tuple[str | None, str]] = []

    async def notify(self, text: str, dedup_key=None, parse_mode="Markdown") -> bool:
        self.notifications.append((dedup_key, text))
        return True

    async def dispatch(self, signal: Signal) -> bool:  # not used by setup directly
        return True


# ---------------------------------------------------------------------------
# Data model for backtest results
# ---------------------------------------------------------------------------
@dataclass
class SignalEval:
    """Post-trade evaluation of a single signal, intraday only."""

    direction: str  # "BUY" or "SELL"
    entry_price: float
    entry_time: str
    mfe_pct: float = 0.0  # Max favorable excursion (signed positive when good)
    mae_pct: float = 0.0  # Max adverse excursion (positive number)
    eod_pct: float = 0.0  # EOD close P/L in % (positive = favorable)
    hit_target: bool = False  # Target hit before stop
    hit_stop: bool = False  # Stop hit before target

    @property
    def winner_first(self) -> str:
        if self.hit_target and not self.hit_stop:
            return "WIN"
        if self.hit_stop and not self.hit_target:
            return "LOSS"
        return "OPEN"  # never hit either band


@dataclass
class DayResult:
    date: str
    index: str
    ref_high: float | None = None
    ref_low: float | None = None
    inside_seen: list[str] = field(default_factory=list)
    armed: bool = False
    invalidated: bool = False
    invalidated_at: str | None = None
    buy: Signal | None = None
    sell: Signal | None = None
    buy_eval: SignalEval | None = None
    sell_eval: SignalEval | None = None

    @property
    def outcome(self) -> str:
        if self.buy and self.sell:
            return "BOTH"
        if self.buy:
            return "BUY"
        if self.sell:
            return "SELL"
        if self.armed:
            return "ARMED (no breakout)"
        if self.invalidated:
            return f"INVALIDATED @ {self.invalidated_at}"
        if not self.inside_seen:
            return "NO REF"
        return f"PARTIAL ({len(self.inside_seen)}/3)"


def evaluate_signal(
    direction: str,
    entry_price: float,
    entry_time: str,
    after_candles: list[Candle],
    target_pct: float,
    stop_pct: float,
) -> SignalEval:
    """Compute MFE/MAE/EOD and target/stop ordering for one signal.

    Uses each candle's high and low for excursions and the LAST candle's
    close for EOD result. Target/stop hits assume an intra-candle break
    (worst-case order if both touched in same candle).
    """
    ev = SignalEval(direction=direction, entry_price=entry_price, entry_time=entry_time)
    if not after_candles or entry_price <= 0:
        return ev

    if direction == "BUY":
        target = entry_price * (1 + target_pct / 100.0)
        stop = entry_price * (1 - stop_pct / 100.0)
    else:
        target = entry_price * (1 - target_pct / 100.0)
        stop = entry_price * (1 + stop_pct / 100.0)

    for c in after_candles:
        if direction == "BUY":
            fav = (c.high - entry_price) / entry_price * 100
            adv = (entry_price - c.low) / entry_price * 100
            t_hit = c.high >= target
            s_hit = c.low <= stop
        else:
            fav = (entry_price - c.low) / entry_price * 100
            adv = (c.high - entry_price) / entry_price * 100
            t_hit = c.low <= target
            s_hit = c.high >= stop

        if fav > ev.mfe_pct:
            ev.mfe_pct = fav
        if adv > ev.mae_pct:
            ev.mae_pct = adv

        if not ev.hit_target and not ev.hit_stop:
            # If both touched in one candle, treat as STOP (conservative)
            if s_hit:
                ev.hit_stop = True
            elif t_hit:
                ev.hit_target = True

    last_close = after_candles[-1].close
    if direction == "BUY":
        ev.eod_pct = (last_close - entry_price) / entry_price * 100
    else:
        ev.eod_pct = (entry_price - last_close) / entry_price * 100
    return ev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_angel_ts(value: Any) -> datetime:
    """Angel returns timestamps like '2025-04-23T09:15:00+05:30'."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=IST)
    return value


def row_to_candle(row: list, symbol: str, timeframe: str) -> Candle:
    ts = parse_angel_ts(row[0])
    if ts.tzinfo is None:
        ts = IST.localize(ts)
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        start=ts.astimezone(IST),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]) if len(row) > 5 else 0.0,
        is_closed=True,
    )


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------
async def backtest_index(
    broker: AngelOneClient,
    name: str,
    exchange: str,
    token: str,
    timeframe: str,
    days: int,
    setup_cfg: dict[str, Any],
    target_pct: float = 0.30,
    stop_pct: float = 0.30,
) -> list[DayResult]:
    interval = AngelOneClient.interval_for_timeframe(timeframe)
    end = datetime.now(IST).replace(hour=15, minute=30, second=0, microsecond=0)
    start = (end - timedelta(days=days)).replace(hour=9, minute=15, second=0, microsecond=0)

    log.info(f"[{name}] fetching candles {start.date()} → {end.date()} ({interval})")
    raw = await broker.get_candles(
        exchange=exchange,
        symbol_token=token,
        interval=interval,
        from_dt=start,
        to_dt=end,
    )
    log.info(f"[{name}] got {len(raw)} rows")

    # Group rows by trading date
    by_day: dict[Any, list[list]] = defaultdict(list)
    for row in raw:
        ts = parse_angel_ts(row[0])
        if ts.tzinfo is None:
            ts = IST.localize(ts)
        by_day[ts.astimezone(IST).date()].append(row)

    state = StubStateEngine()
    alerts = StubAlertEngine()
    setup = InsideCandleSetup(
        name="inside_candle",
        config=setup_cfg,
        state=state,
        alerts=alerts,
    )

    results: list[DayResult] = []
    for day in sorted(by_day.keys()):
        state.reset()
        result = DayResult(date=day.isoformat(), index=name)
        intraday: list[Candle] = []
        buy_idx: int | None = None
        sell_idx: int | None = None

        # Sort intraday by time
        rows = sorted(by_day[day], key=lambda r: parse_angel_ts(r[0]))
        for i, row in enumerate(rows):
            candle = row_to_candle(row, name, timeframe)
            intraday.append(candle)
            signal = await setup.on_candle(name, candle)

            # Snapshot state after this candle
            s = await state.get("inside_candle", name)
            ref = s.get("reference")
            if ref:
                result.ref_high = float(ref["high"])
                result.ref_low = float(ref["low"])
            result.inside_seen = list(s.get("inside_seen", []))
            if s.get("armed"):
                result.armed = True
            if s.get("invalidated") and not result.invalidated:
                result.invalidated = True
                result.invalidated_at = candle.start.astimezone(IST).strftime("%H:%M")

            if signal is not None:
                if signal.direction == SignalDirection.BUY and result.buy is None:
                    result.buy = signal
                    buy_idx = i
                elif signal.direction == SignalDirection.SELL and result.sell is None:
                    result.sell = signal
                    sell_idx = i

        # ---- Per-signal post-trade evaluation (same-day candles only) -----
        if result.buy is not None and buy_idx is not None:
            after = intraday[buy_idx + 1 :]
            result.buy_eval = evaluate_signal(
                "BUY",
                result.buy.price,
                result.buy.time_str,
                after,
                target_pct=target_pct,
                stop_pct=stop_pct,
            )
        if result.sell is not None and sell_idx is not None:
            after = intraday[sell_idx + 1 :]
            result.sell_eval = evaluate_signal(
                "SELL",
                result.sell.price,
                result.sell.time_str,
                after,
                target_pct=target_pct,
                stop_pct=stop_pct,
            )

        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _fmt_signal(sig: Signal | None, ev: SignalEval | None) -> str:
    if sig is None:
        return "-"
    base = f"{sig.price:.1f}@{sig.time_str}"
    if ev is None:
        return base
    return f"{base} [{ev.winner_first} eod{ev.eod_pct:+.2f}%]"


def print_table(results: list[DayResult]) -> None:
    if not results:
        print("  (no data)")
        return
    hdr = f"  {'Date':<11} {'Outcome':<22} {'RefH':>10} {'RefL':>10}  {'BUY':<26}  {'SELL':<26}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results:
        rh = f"{r.ref_high:.2f}" if r.ref_high is not None else "-"
        rl = f"{r.ref_low:.2f}" if r.ref_low is not None else "-"
        buy = _fmt_signal(r.buy, r.buy_eval)
        sell = _fmt_signal(r.sell, r.sell_eval)
        print(f"  {r.date:<11} {r.outcome:<22} {rh:>10} {rl:>10}  {buy:<26}  {sell:<26}")


def print_summary(name: str, results: list[DayResult], target_pct: float, stop_pct: float) -> None:
    total = len(results)
    armed = sum(1 for r in results if r.armed)
    invalid = sum(1 for r in results if r.invalidated and not r.armed)
    buys = sum(1 for r in results if r.buy)
    sells = sum(1 for r in results if r.sell)
    no_ref = sum(1 for r in results if r.ref_high is None)

    # Collect all evals
    evals: list[SignalEval] = []
    for r in results:
        if r.buy_eval:
            evals.append(r.buy_eval)
        if r.sell_eval:
            evals.append(r.sell_eval)

    print(
        f"\n  {name} — {total} trading days\n"
        f"  ───────────────────────────────────────\n"
        f"  setup armed         : {armed:>3}  ({armed / max(total, 1) * 100:5.1f}% of days)\n"
        f"  invalidated         : {invalid:>3}  ({invalid / max(total, 1) * 100:5.1f}% of days)\n"
        f"  weekend / missing   : {no_ref:>3}\n"
        f"  BUY signals fired   : {buys:>3}\n"
        f"  SELL signals fired  : {sells:>3}\n"
        f"  total signals       : {len(evals):>3}"
    )

    if not evals:
        print("\n  No signals to evaluate.")
        return

    # ---------------- Accuracy metrics ----------------
    win_target_first = sum(1 for e in evals if e.winner_first == "WIN")
    loss_stop_first = sum(1 for e in evals if e.winner_first == "LOSS")
    open_neither = sum(1 for e in evals if e.winner_first == "OPEN")
    decided = win_target_first + loss_stop_first

    eod_positive = sum(1 for e in evals if e.eod_pct > 0)
    avg_mfe = sum(e.mfe_pct for e in evals) / len(evals)
    avg_mae = sum(e.mae_pct for e in evals) / len(evals)
    avg_eod = sum(e.eod_pct for e in evals) / len(evals)

    # Trade-expectancy using actual target/stop exits.
    # - hit_target  -> realized +target_pct
    # - hit_stop    -> realized -stop_pct
    # - neither     -> exit at EOD (use eod_pct)
    def realized(e: SignalEval) -> float:
        if e.hit_target:
            return target_pct
        if e.hit_stop:
            return -stop_pct
        return e.eod_pct

    trade_pnls = [realized(e) for e in evals]
    expectancy = sum(trade_pnls) / len(trade_pnls)
    gross_pct = sum(trade_pnls)
    rr = target_pct / stop_pct if stop_pct > 0 else 0.0
    breakeven_winrate = 100.0 / (1.0 + rr) if rr > 0 else 50.0

    print(
        f"\n  Accuracy (target +{target_pct:.2f}% / stop -{stop_pct:.2f}%  "
        f"R:R = 1:{rr:.2g}, break-even win-rate = {breakeven_winrate:.1f}%)\n"
        f"  ─────────────────────────────────────────────────────\n"
        f"  TARGET hit first    : {win_target_first:>3} / {len(evals)}  "
        f"({win_target_first / len(evals) * 100:5.1f}% of signals)\n"
        f"  STOP hit first      : {loss_stop_first:>3} / {len(evals)}  "
        f"({loss_stop_first / len(evals) * 100:5.1f}% of signals)\n"
        f"  Neither touched     : {open_neither:>3} / {len(evals)}  "
        f"({open_neither / len(evals) * 100:5.1f}% of signals)\n"
        f"  Win rate (decided)  : "
        f"{(win_target_first / decided * 100) if decided else 0.0:5.1f}% "
        f"({win_target_first}/{decided})\n"
        f"  EOD closed in favor : {eod_positive} / {len(evals)}  "
        f"({eod_positive / len(evals) * 100:5.1f}%)\n"
        f"\n  Avg MFE             : +{avg_mfe:.3f}%   (best favorable move)\n"
        f"  Avg MAE             : -{avg_mae:.3f}%   (worst adverse move)\n"
        f"  Avg EOD return      : {'+' if avg_eod >= 0 else ''}{avg_eod:.3f}%  "
        f"(signed in signal direction, hold to close)\n"
        f"\n  ▶ Trade expectancy  : {'+' if expectancy >= 0 else ''}{expectancy:.3f}%  per signal "
        f"(target/stop exits)\n"
        f"  ▶ Gross over period : {'+' if gross_pct >= 0 else ''}{gross_pct:.2f}% "
        f"across {len(evals)} signals (before costs)"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main(days: int, only: list[str] | None, target_pct: float, stop_pct: float) -> int:
    setup_logger(level=settings.log_level, log_file=None)
    cfg = load_yaml_config()
    inside_cfg = cfg.setups["inside_candle"].model_dump()
    timeframe = inside_cfg.get("timeframe", "5min")

    indices = [
        (name, idx)
        for name, idx in cfg.indices.items()
        if idx.enabled and (only is None or name in only)
    ]
    if not indices:
        print("No matching indices in config.")
        return 2

    broker = AngelOneClient()
    print("Logging into Angel One …")
    await broker.login()
    print("Login OK\n")

    try:
        for name, idx in indices:
            print(
                f"\n{'=' * 80}\n {name}  (token={idx.token}, exchange={idx.exchange})\n{'=' * 80}"
            )
            results = await backtest_index(
                broker=broker,
                name=name,
                exchange=idx.exchange,
                token=idx.token,
                timeframe=timeframe,
                days=days,
                setup_cfg=inside_cfg,
                target_pct=target_pct,
                stop_pct=stop_pct,
            )
            print_table(results)
            print_summary(name, results, target_pct, stop_pct)
    finally:
        await broker.logout()
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backtest inside-candle setup.")
    p.add_argument("--days", type=int, default=30, help="Lookback window in calendar days.")
    p.add_argument(
        "--index",
        type=str,
        default=None,
        help="Comma-separated index names (default: all enabled in config).",
    )
    # Default risk/reward is 1:2 — target is twice the stop (user preference).
    p.add_argument(
        "--target",
        type=float,
        default=0.60,
        help="Profit target in %% from entry (default 0.60 — 1:2 R:R with default stop).",
    )
    p.add_argument(
        "--stop", type=float, default=0.30, help="Stop loss in %% from entry (default 0.30)."
    )
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    only = [s.strip() for s in args.index.split(",")] if args.index else None
    sys.exit(asyncio.run(main(args.days, only, args.target, args.stop)))
