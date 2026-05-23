"""Send sample alerts to every chat ID in .env.

Run:
    .venv/bin/python scripts/send_test_alert.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
RAW_CHATS = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()


def parse_chat_ids(raw: str) -> list[int]:
    out: list[int] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.append(int(piece))
        except ValueError:
            print(f"  ! Skipping invalid chat id: {piece!r}", file=sys.stderr)
    return out


# Sample payloads — identical in shape to what AlertEngine.render() / .notify() produce
SAMPLE_ARMED = (
    "🎯 *INSIDE CANDLE SETUP ARMED*  _(TEST)_\n\n"
    "*Index:* NIFTY50\n"
    "*Time:* 09:30\n"
    "*Reference High:* 22148.10\n"
    "*Reference Low:* 22095.55\n\n"
    "All 3 inside candles confirmed.\n"
    "🟢 Close above *22148.10* → BUY\n"
    "🔴 Close below *22095.55* → SELL"
)

SAMPLE_BUY = (
    "🟢 *INSIDE CANDLE BREAKOUT BUY*  _(TEST)_\n\n"
    "*Index:* NIFTY50\n"
    "*Time:* 09:40\n"
    "*Breakout Price:* 22150.30\n"
    "*Reference High:* 22148.10\n"
    "*Reference Low:* 22095.55\n"
)

SAMPLE_SELL = (
    "🔴 *INSIDE CANDLE BREAKDOWN SELL*  _(TEST)_\n\n"
    "*Index:* BANKNIFTY\n"
    "*Time:* 09:45\n"
    "*Breakdown Price:* 47020.15\n"
    "*Reference High:* 47180.50\n"
    "*Reference Low:* 47038.25\n"
)

PING = (
    "✅ *Stock Scanner v2 — Test Ping*\n\n"
    "If you can read this, your Telegram bot token and chat ID are wired correctly.\n"
    "Three sample alerts will follow."
)


async def main() -> int:
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing in .env", file=sys.stderr)
        return 2
    chats = parse_chat_ids(RAW_CHATS)
    if not chats:
        print("ERROR: TELEGRAM_CHAT_IDS missing or empty in .env", file=sys.stderr)
        return 2

    bot = Bot(token=TOKEN)
    print(f"Sending test alerts to {len(chats)} chat(s): {chats}")

    rc = 0
    async with bot:
        try:
            me = await bot.get_me()
            print(f"  Bot identity: @{me.username} (id={me.id})")
        except TelegramError as exc:
            print(f"ERROR: get_me() failed: {exc!s}", file=sys.stderr)
            return 1

        for chat_id in chats:
            print(f"\n→ chat_id={chat_id}")
            for label, body in [
                ("ping", PING),
                ("armed", SAMPLE_ARMED),
                ("buy", SAMPLE_BUY),
                ("sell", SAMPLE_SELL),
            ]:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=body,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    )
                    print(f"  ✓ {label}")
                except TelegramError as exc:
                    print(f"  ✗ {label}: {exc!s}", file=sys.stderr)
                    rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
