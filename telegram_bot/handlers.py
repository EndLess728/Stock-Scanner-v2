"""Telegram command handlers.

The orchestrator injects these components into bot.bot_data:
  - "config"        : AppConfig
  - "setup_engine"  : SetupEngine
  - "market_service": MarketDataService
  - "reload_fn"     : Callable[[], Awaitable[None]] hot-reload entrypoint
"""
from __future__ import annotations

from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from utils.logger import log


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return dict(context.application.bot_data)


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.message is None:
        return
    text = (
        "👋 *Stock Scanner v2 — Intraday Alert Bot*\n\n"
        "I monitor Indian indices live and ping you when configured setups trigger.\n\n"
        "Type /help for commands."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    text = (
        "*Available commands*\n\n"
        "/start — welcome message\n"
        "/help — this message\n"
        "/status — engine health\n"
        "/indices — show monitored indices\n"
        "/active_indices — show enabled indices\n"
        "/active_setups — show enabled setups\n"
        "/enable_setup `<name>` — turn a setup on\n"
        "/disable_setup `<name>` — turn a setup off\n"
        "/reload_config — reload `config.yaml`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    bd = _ctx(context)
    svc = bd.get("market_service")
    cfg = bd.get("config")
    if svc is None or cfg is None:
        await update.message.reply_text("Engine not ready.", parse_mode=ParseMode.MARKDOWN)
        return

    ws_connected = svc.ws.is_connected() if svc.ws is not None else False
    last_tick = svc.ws.seconds_since_last_tick() if svc.ws is not None else float("inf")
    indices = ", ".join(cfg.enabled_indices()) or "(none)"
    setups = ", ".join(cfg.enabled_setups()) or "(none)"

    text = (
        "*Engine Status*\n\n"
        f"WebSocket connected: `{ws_connected}`\n"
        f"Seconds since last tick: `{last_tick:.1f}`\n"
        f"Active indices: `{indices}`\n"
        f"Active setups: `{setups}`\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /indices
# ---------------------------------------------------------------------------
async def cmd_indices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _ctx(context).get("config")
    if cfg is None:
        await update.message.reply_text("Config not loaded.")
        return

    lines = ["*Configured indices*"]
    for name, idx in cfg.indices.items():
        mark = "✅" if idx.enabled else "❌"
        lines.append(f"{mark} `{name}` — {idx.symbol} (token={idx.token})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /active_indices
# ---------------------------------------------------------------------------
async def cmd_active_indices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _ctx(context).get("config")
    if cfg is None:
        await update.message.reply_text("Config not loaded.")
        return
    indices = cfg.enabled_indices() or ["(none)"]
    await update.message.reply_text(
        "*Active indices:*\n" + "\n".join(f"• `{x}`" for x in indices),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# /active_setups
# ---------------------------------------------------------------------------
async def cmd_active_setups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    setup_engine = _ctx(context).get("setup_engine")
    if setup_engine is None:
        await update.message.reply_text("Setup engine not ready.")
        return
    names = setup_engine.enabled_names() or ["(none)"]
    await update.message.reply_text(
        "*Active setups:*\n" + "\n".join(f"• `{x}`" for x in names),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# /enable_setup <name>
# ---------------------------------------------------------------------------
async def cmd_enable_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("Usage: `/enable_setup <name>`", parse_mode=ParseMode.MARKDOWN)
        return
    name = context.args[0]
    setup_engine = _ctx(context).get("setup_engine")
    if setup_engine is None:
        await update.message.reply_text("Setup engine not ready.")
        return
    if setup_engine.enable(name):
        await update.message.reply_text(f"✅ Setup `{name}` enabled.", parse_mode=ParseMode.MARKDOWN)
        log.info(f"Setup '{name}' enabled via Telegram")
    else:
        await update.message.reply_text(
            f"⚠️ Setup `{name}` not found in config.", parse_mode=ParseMode.MARKDOWN
        )


# ---------------------------------------------------------------------------
# /disable_setup <name>
# ---------------------------------------------------------------------------
async def cmd_disable_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("Usage: `/disable_setup <name>`", parse_mode=ParseMode.MARKDOWN)
        return
    name = context.args[0]
    setup_engine = _ctx(context).get("setup_engine")
    if setup_engine is None:
        await update.message.reply_text("Setup engine not ready.")
        return
    if setup_engine.disable(name):
        await update.message.reply_text(f"⛔️ Setup `{name}` disabled.", parse_mode=ParseMode.MARKDOWN)
        log.info(f"Setup '{name}' disabled via Telegram")
    else:
        await update.message.reply_text(
            f"⚠️ Setup `{name}` not found in config.", parse_mode=ParseMode.MARKDOWN
        )


# ---------------------------------------------------------------------------
# /reload_config
# ---------------------------------------------------------------------------
async def cmd_reload_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    reload_fn = _ctx(context).get("reload_fn")
    if reload_fn is None:
        await update.message.reply_text("Reload hook not wired.")
        return
    try:
        await reload_fn()
        await update.message.reply_text("🔄 Configuration reloaded.", parse_mode=ParseMode.MARKDOWN)
        log.info("config.yaml reloaded via Telegram")
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Reload failed: `{exc!s}`", parse_mode=ParseMode.MARKDOWN
        )
        log.exception(f"Reload failed: {exc!r}")


def register_all(bot: Any) -> None:
    """Attach every command handler to a TelegramBot instance."""
    bot.register_command("start", cmd_start)
    bot.register_command("help", cmd_help)
    bot.register_command("status", cmd_status)
    bot.register_command("indices", cmd_indices)
    bot.register_command("active_indices", cmd_active_indices)
    bot.register_command("active_setups", cmd_active_setups)
    bot.register_command("enable_setup", cmd_enable_setup)
    bot.register_command("disable_setup", cmd_disable_setup)
    bot.register_command("reload_config", cmd_reload_config)


__all__ = ["register_all"]
