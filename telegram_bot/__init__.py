"""Telegram bot package.

Named `telegram_bot` (not `telegram`) to avoid shadowing the
`python-telegram-bot` library's top-level `telegram` package.
"""
from telegram_bot.bot import TelegramBot

__all__ = ["TelegramBot"]
