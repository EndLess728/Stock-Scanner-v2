"""Telegram command handlers.

The orchestrator injects these components into bot.bot_data:
  - "config"        : AppConfig
  - "setup_engine"  : SetupEngine
  - "market_service": MarketDataService
  - "reload_fn"     : Callable[[], Awaitable[None]] hot-reload entrypoint
  - "toggle_fn"     : Callable[[scope, name], Awaitable[bool | None]] menu toggle
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config.settings import AppConfig
from utils.logger import log


# Callback-data scheme. Telegram caps callback_data at 64 bytes — short keys matter.
#   tog:s:<setup_name>   — toggle a setup
#   tog:i:<index_name>   — toggle an index
#   close                — dismiss the menu
_CB_TOGGLE = "tog"
_SCOPE_SETUP = "s"
_SCOPE_INDEX = "i"
_CB_CLOSE = "close"


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return dict(context.application.bot_data)


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------
def _build_setups_keyboard(cfg: AppConfig) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name, scfg in cfg.setups.items():
        marker = "✅" if scfg.enabled else "⬜"
        rows.append(
            [InlineKeyboardButton(f"{marker}  {name}", callback_data=f"{_CB_TOGGLE}:{_SCOPE_SETUP}:{name}")]
        )
    if not rows:
        rows.append([InlineKeyboardButton("(no setups configured)", callback_data=_CB_CLOSE)])
    rows.append([InlineKeyboardButton("✖️ Close", callback_data=_CB_CLOSE)])
    return InlineKeyboardMarkup(rows)


def _build_indices_keyboard(cfg: AppConfig) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for name, icfg in cfg.indices.items():
        marker = "✅" if icfg.enabled else "⬜"
        rows.append(
            [InlineKeyboardButton(f"{marker}  {name}", callback_data=f"{_CB_TOGGLE}:{_SCOPE_INDEX}:{name}")]
        )
    if not rows:
        rows.append([InlineKeyboardButton("(no indices configured)", callback_data=_CB_CLOSE)])
    rows.append([InlineKeyboardButton("✖️ Close", callback_data=_CB_CLOSE)])
    return InlineKeyboardMarkup(rows)


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
    # Wrap commands in backticks so underscores (e.g. /reload_config) don't get
    # parsed as Markdown italic delimiters and crash the API call.
    lines = ["*Available commands*", ""]
    for name, desc, _handler, _in_menu in COMMANDS:
        lines.append(f"`/{name}` — {desc}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


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
# /info — full configuration overview (indices + setups, enabled and disabled)
# ---------------------------------------------------------------------------
async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _ctx(context).get("config")
    if cfg is None:
        await update.message.reply_text("Config not loaded.")
        return

    lines: list[str] = ["*Configuration Overview*", ""]

    # Indices
    lines.append("📊 *Indices*")
    if not cfg.indices:
        lines.append("_none configured_")
    for name, idx in cfg.indices.items():
        marker = "✅" if idx.enabled else "⬜"
        lines.append(f"{marker} `{name}` — {idx.symbol} ({idx.exchange})")
    lines.append("")

    # Setups
    lines.append("⚙️ *Setups*")
    if not cfg.setups:
        lines.append("_none configured_")
    for name, scfg in cfg.setups.items():
        marker = "✅" if scfg.enabled else "⬜"
        # Per-setup index list may diverge from the global universe; show both.
        idx_list = ", ".join(scfg.indices) if scfg.indices else "(none)"
        lines.append(f"{marker} `{name}`  _({scfg.timeframe})_")
        lines.append(f"      ↳ tracks: `{idx_list}`")
        lines.append(
            f"      ↳ daily limit: {scfg.max_buy_alerts_per_day} BUY / "
            f"{scfg.max_sell_alerts_per_day} SELL"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# /indices — interactive multi-select
# ---------------------------------------------------------------------------
async def cmd_indices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _ctx(context).get("config")
    if cfg is None:
        await update.message.reply_text("Config not loaded.")
        return
    await update.message.reply_text(
        "*Indices* — tap to toggle on/off",
        reply_markup=_build_indices_keyboard(cfg),
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# /setups — interactive multi-select
# ---------------------------------------------------------------------------
async def cmd_setups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    cfg = _ctx(context).get("config")
    if cfg is None:
        await update.message.reply_text("Config not loaded.")
        return
    await update.message.reply_text(
        "*Setups* — tap to toggle on/off",
        reply_markup=_build_setups_keyboard(cfg),
        parse_mode=ParseMode.MARKDOWN,
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


# ---------------------------------------------------------------------------
# Inline-keyboard callback: handles every tap on /setups and /indices menus.
# ---------------------------------------------------------------------------
async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""

    if data == _CB_CLOSE:
        await query.answer()
        try:
            await query.edit_message_text("Menu closed.")
        except BadRequest:
            pass
        return

    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != _CB_TOGGLE:
        await query.answer("Unknown action.")
        return

    scope_code, name = parts[1], parts[2]
    scope = "setup" if scope_code == _SCOPE_SETUP else "index" if scope_code == _SCOPE_INDEX else None
    if scope is None:
        await query.answer("Unknown scope.")
        return

    bd = _ctx(context)
    toggle_fn = bd.get("toggle_fn")
    cfg = bd.get("config")
    if toggle_fn is None or cfg is None:
        await query.answer("Engine not ready.")
        return

    new_state = await toggle_fn(scope, name)
    if new_state is None:
        await query.answer(f"{scope.capitalize()} '{name}' not found.")
        return

    verb = "enabled" if new_state else "disabled"
    await query.answer(f"{name} {verb}")

    # Rebuild keyboard so the ✅/⬜ marker updates.
    new_kb = (
        _build_setups_keyboard(cfg) if scope == "setup" else _build_indices_keyboard(cfg)
    )
    try:
        await query.edit_message_reply_markup(reply_markup=new_kb)
    except BadRequest as exc:
        # "Message is not modified" is benign — happens if user double-taps the same button.
        if "not modified" not in str(exc).lower():
            log.warning(f"Menu refresh failed: {exc!r}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
# Single source of truth: (command, description, handler, in_menu).
# `in_menu=False` keeps the command typeable (autocomplete via /) but hides it
# from Telegram's Menu button — reserved for the few commands used daily.
COMMANDS: list[tuple[str, str, Any, bool]] = [
    ("start", "Welcome message", cmd_start, False),
    ("help", "List all commands", cmd_help, False),
    ("status", "Engine health and connection state", cmd_status, True),
    ("info", "Show selected indices and tracked setups", cmd_info, True),
    ("indices", "Toggle which indices to track", cmd_indices, True),
    ("setups", "Toggle which setups are active", cmd_setups, True),
    ("reload_config", "Hot-reload config.yaml", cmd_reload_config, False),
]


def register_all(bot: Any) -> None:
    """Attach every command handler and publish the Telegram menu."""
    for name, _desc, handler, _in_menu in COMMANDS:
        bot.register_command(name, handler)
    # Pattern restricts this callback to our menu's data scheme, leaving other
    # callback_query traffic (if added later) free for separate handlers.
    bot.register_callback_query(on_menu_callback, pattern=rf"^({_CB_TOGGLE}:|{_CB_CLOSE}$)")
    bot.set_command_menu([(name, desc) for name, desc, _, in_menu in COMMANDS if in_menu])


__all__ = ["register_all", "COMMANDS"]
