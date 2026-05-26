"""Print every chat that has recently messaged the bot.

Usage:
    1. Open Telegram, search for your bot, send /start (or any text).
    2. Run: .venv/bin/python scripts/find_chat_id.py
    3. Copy the chat_id printed and paste it into TELEGRAM_CHAT_IDS in .env.

For a group: add the bot to the group, send any message in the group,
then run this script. Group IDs look like `-100123456789` (negative).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


async def main() -> int:
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing in .env", file=sys.stderr)
        return 2

    bot = Bot(token=TOKEN)
    async with bot:
        try:
            me = await bot.get_me()
            print(f"Bot: @{me.username} (id={me.id})\n")
        except TelegramError as exc:
            print(f"ERROR: get_me() failed: {exc!s}", file=sys.stderr)
            return 1

        try:
            updates = await bot.get_updates(timeout=5)
        except TelegramError as exc:
            print(f"ERROR: get_updates() failed: {exc!s}", file=sys.stderr)
            return 1

    if not updates:
        print("No recent messages found.")
        print("In Telegram, open the bot and send /start (or any text), then re-run.")
        return 1

    seen: dict[int, str] = {}
    for u in updates:
        msg = u.message or u.edited_message or u.channel_post
        if msg is None or msg.chat is None:
            continue
        chat = msg.chat
        label = (
            chat.title
            or " ".join(filter(None, [chat.first_name, chat.last_name]))
            or chat.username
            or ""
        )
        seen[chat.id] = f"{chat.type:<10} {label}".rstrip()

    print(f"Found {len(seen)} chat(s) that recently messaged this bot:\n")
    for cid, label in seen.items():
        print(f"  chat_id = {cid}   ({label})")

    print("\nUpdate .env with the chat id(s) you want to receive alerts on, e.g.:")
    first = next(iter(seen.keys()))
    print(f"  TELEGRAM_CHAT_IDS={first}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
