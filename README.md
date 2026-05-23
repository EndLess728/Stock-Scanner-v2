# Stock Scanner v2 — Intraday Alert Bot (Angel One + Telegram)

A production-ready, async, event-driven **alert bot** for Indian indices
(`NIFTY50`, `BANKNIFTY`) using **Angel One SmartAPI** live ticks and
**Telegram** for delivery.

> This is **NOT** an auto-trading bot. It only sends alerts.

---

## Features

- 🔌 Angel One SmartAPI (REST + WebSocket V2) with TOTP login
- 📈 Live 5-min candle aggregation from raw ticks
- 🧠 Pluggable **setup** system (Strategy Pattern + auto-registry)
  - `inside_candle` breakout/breakdown
- 🔍 Pluggable **pattern engine** (future-ready, off by default)
  - Head & Shoulders / Inverse H&S
  - Custom M / W (double top / bottom)
  - Parallel channels (asc / desc / horizontal)
  - Fibonacci retracement / extensions
- 🤖 Telegram bot with rich commands and multi-chat broadcast
- 💾 SQLite-backed dedup, state, and user preferences
- 🪵 Loguru rotating logs
- ♻️ Hot-reloadable YAML config (`/reload_config`)
- 🚦 Daily quotas + idempotent dedup (no duplicate alerts)
- 🔁 WebSocket auto-reconnect, stale-tick recovery, healthcheck

---

## Project structure

```
Stock-Scanner-v2/
├── main.py                      # Orchestrator (asyncio)
├── run.py                       # Entrypoint
├── config/
│   ├── settings.py              # .env (pydantic-settings) + YAML loader
│   └── config.yaml              # All runtime knobs
├── broker/
│   ├── angelone_client.py       # REST (login / TOTP / history)
│   └── websocket_client.py      # SmartWebSocketV2 with auto-reconnect
├── telegram_bot/                # Renamed from `telegram` to avoid PTB conflict
│   ├── bot.py
│   └── handlers.py
├── setups/
│   ├── base_setup.py            # Abstract base + registry
│   └── inside_candle.py         # 9:15 ref + 9:20/9:25/9:30 inside
├── patterns/
│   ├── swing_detector.py
│   ├── trendline_engine.py
│   ├── head_shoulders.py
│   ├── inverse_head_shoulders.py
│   ├── m_pattern.py
│   ├── w_pattern.py
│   ├── parallel_channel.py
│   └── fibonacci.py
├── engines/
│   ├── candle_engine.py         # Ticks → OHLCV → observers
│   ├── setup_engine.py          # Routes candles to setups
│   ├── pattern_engine.py        # Routes candles to pattern detectors
│   ├── alert_engine.py          # Render + dedup + broadcast
│   └── state_engine.py          # Persistent setup state
├── database/
│   └── sqlite.py                # aiosqlite repository
├── models/
│   ├── candle.py
│   ├── signal.py
│   └── pattern.py
├── services/
│   └── market_data_service.py   # Wires broker → engines
├── utils/
│   ├── logger.py
│   ├── helpers.py
│   └── time_utils.py
├── logs/
├── data/
├── requirements.txt
├── .env.example
└── README.md
```

> **Note** — the project folder is `telegram_bot/`, not `telegram/`,
> to avoid shadowing the `python-telegram-bot` library's top-level
> `telegram` package.

---

## 1. Installation

```bash
git clone <your-fork-url> Stock-Scanner-v2
cd Stock-Scanner-v2

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Python **3.14+** is required.

---

## 2. Angel One SmartAPI setup

1. Create a SmartAPI app at <https://smartapi.angelbroking.com/>.
2. Note down:
   - `API Key`
   - `Client ID` (your Angel One client code)
   - `PIN` / password (Angel One uses MPIN for login)
3. Enable TOTP on your Angel One account and copy the **TOTP secret**
   (the base32 string under the QR code).
4. Make sure your subscription includes the live data feed for
   **NSE indices** (NIFTY 50 and Nifty Bank).

---

## 3. Telegram bot setup

1. Open Telegram and message [`@BotFather`](https://t.me/BotFather).
2. `/newbot` → choose a name → copy the **token**.
3. Start a chat with your new bot once so it can message you.
4. To get your chat ID:
   - DM the bot, then visit
     `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser.
   - Find `"chat":{"id":<number>}` in the response.
5. For a **group**, add the bot to the group, send any message,
   call `getUpdates`, and pick up the negative group chat ID
   (looks like `-100123456789`).

---

## 4. Environment configuration

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
ANGEL_API_KEY=your_api_key
ANGEL_CLIENT_ID=A1234
ANGEL_PASSWORD=1234        # MPIN
ANGEL_TOTP_SECRET=BASE32SECRET

TELEGRAM_BOT_TOKEN=123456:ABC-...
TELEGRAM_CHAT_IDS=123456789,-100987654321

LOG_LEVEL=INFO
TZ=Asia/Kolkata
DATABASE_PATH=data/scanner.db
CONFIG_PATH=config/config.yaml
```

`TELEGRAM_CHAT_IDS` is a comma-separated list — every alert is fanned
out to every chat.

---

## 5. Runtime configuration (`config/config.yaml`)

Open `config/config.yaml` to customize:

- Market hours / holidays
- Active indices (`enabled: true/false`)
- Active setups + their parameters
- Pattern engine flags
- Telegram parse mode and rate limits
- Logging level / rotation / retention

You can reload the file at runtime with `/reload_config` — no restart needed.

---

## 6. Running

```bash
python run.py
```

Logs are written to console **and** `logs/scanner.log` (rotated).

On startup the bot:

1. Reads `.env` and `config.yaml`.
2. Opens SQLite (`data/scanner.db`) and prunes stale state rows.
3. Loads every setup module under `setups/` (auto-registry).
4. Loads every pattern module under `patterns/` (auto-registry).
5. Sends a startup ping to all `TELEGRAM_CHAT_IDS`.
6. Logs into Angel One (TOTP) and seeds today's candles via REST.
7. Subscribes to live ticks via WebSocket V2.

---

## 7. Telegram commands

| Command                 | Description                                          |
| ----------------------- | ---------------------------------------------------- |
| `/start`                | Welcome message                                      |
| `/help`                 | Full command list                                    |
| `/status`               | WebSocket health, time since last tick, active items |
| `/indices`              | Show all configured indices and their state          |
| `/active_indices`       | Only enabled indices                                 |
| `/active_setups`        | Only enabled setups                                  |
| `/enable_setup <name>`  | Enable a setup (e.g. `/enable_setup inside_candle`)  |
| `/disable_setup <name>` | Disable a setup                                      |
| `/reload_config`        | Reload `config.yaml` without restarting              |

---

## 8. Example alerts

**Inside-candle breakout (BUY):**

```
🟢 INSIDE CANDLE BREAKOUT BUY

Index: NIFTY50
Time: 09:35
Breakout Price: 22150.30
Reference High: 22148.10
Reference Low: 22095.55
```

**Inside-candle breakdown (SELL):**

```
🔴 INSIDE CANDLE BREAKDOWN SELL

Index: BANKNIFTY
Time: 09:40
Breakdown Price: 47020.15
Reference High: 47180.50
Reference Low: 47038.25
```

---

## 9. Inside-candle setup logic

- **Timeframe:** 5 minutes
- **Reference candle:** 09:15
- **Inside candles:** 09:20, 09:25, 09:30 — each must satisfy
  `high < ref.high` AND `low > ref.low`
- After all 3 inside candles confirm, ANY subsequent 5-min candle:
  - that **closes above** `ref.high` → 🟢 **BUY** alert
  - that **closes below** `ref.low` → 🔴 **SELL** alert
- **At most one BUY and one SELL per index per day.**
- Resets automatically at start-of-day.

---

## 10. Adding a new setup (no core changes)

1. Create `setups/my_setup.py`:

```python
from setups.base_setup import BaseSetup
from models.candle import Candle
from models.signal import Signal, SignalDirection

class MySetup(BaseSetup):
    name = "my_setup"

    async def on_candle(self, symbol, candle: Candle):
        if not candle.is_closed:
            return None
        if candle.close > candle.open * 1.005:
            return Signal(
                setup=self.name,
                index=symbol,
                direction=SignalDirection.BUY,
                price=candle.close,
                timeframe=candle.timeframe,
                timestamp=candle.start,
            )
        return None
```

2. Add it to `config/config.yaml`:

```yaml
setups:
  my_setup:
    enabled: true
    timeframe: "5min"
    indices: ["NIFTY50", "BANKNIFTY"]
    alert_cooldown_seconds: 60
    max_buy_alerts_per_day: 1
    max_sell_alerts_per_day: 1
```

3. Either restart, or send `/reload_config` in Telegram.

That's it — no engine code changes required.

---

## 11. Adding a new pattern detector

1. Create `patterns/my_pattern.py`:

```python
from engines.pattern_engine import register_detector
from models.candle import CandleSeries
from models.pattern import Pattern, PatternKind, PatternStatus

@register_detector("my_pattern")
def detect(series: CandleSeries, config: dict) -> list[Pattern]:
    if not config.get("enabled", False):
        return []
    ...
```

2. Add a key under `patterns:` in `config/config.yaml`:

```yaml
patterns:
  enabled: true
  my_pattern:
    enabled: true
```

---

## 12. Operational notes

- Logs rotate at `10 MB` and retain `14 days` (configurable).
- All alerts are de-duplicated using
  `<date>|<setup>|<index>|<direction>` as the idempotency key.
- WebSocket auto-reconnects with exponential backoff (cap 30s).
- Healthcheck restarts the WS if no tick arrives for 60s during
  market hours.
- The bot ignores market holidays and weekends — configure in
  `config.yaml > market.holidays` (ISO dates).

---

## 13. Common issues

| Symptom                                | Fix                                                                                      |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: SmartApi`        | `pip install smartapi-python`                                                            |
| `Login failed`                         | Re-verify API key, MPIN, and TOTP secret (it must be the **secret**, not a current code) |
| `Telegram send failed: chat not found` | Start a chat with the bot, then re-run `/getUpdates` to fetch the right chat ID          |
| No alerts fire                         | Check `/status` — WebSocket connected? Last tick recent? Setup enabled?                  |
| Stale state across days                | The bot prunes state older than today on every boot — no action needed                   |

---

## License

MIT — see `LICENSE`.
