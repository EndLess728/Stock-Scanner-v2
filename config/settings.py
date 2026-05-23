"""Application settings.

- `Settings` (pydantic-settings) loads secrets and runtime knobs from .env
- `AppConfig` is the typed view over `config/config.yaml` that gets hot-reloaded
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# -----------------------------------------------------------------------------
# Secrets / env
# -----------------------------------------------------------------------------
class Settings(BaseSettings):
    """Loaded from `.env`. Only secrets and bootstrap paths live here."""

    # Angel One
    angel_api_key: str = Field(..., alias="ANGEL_API_KEY")
    angel_client_id: str = Field(..., alias="ANGEL_CLIENT_ID")
    angel_password: str = Field(..., alias="ANGEL_PASSWORD")
    angel_totp_secret: str = Field(..., alias="ANGEL_TOTP_SECRET")

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_ids: str = Field("", alias="TELEGRAM_CHAT_IDS")

    # Runtime
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    tz: str = Field("Asia/Kolkata", alias="TZ")
    database_path: str = Field("data/scanner.db", alias="DATABASE_PATH")
    config_path: str = Field("config/config.yaml", alias="CONFIG_PATH")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def chat_ids(self) -> List[int]:
        out: List[int] = []
        for raw in self.telegram_chat_ids.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(int(raw))
            except ValueError:
                continue
        return out


# -----------------------------------------------------------------------------
# YAML-backed config (hot-reloadable)
# -----------------------------------------------------------------------------
class MarketConfig(BaseModel):
    timezone: str = "Asia/Kolkata"
    open_time: str = "09:15"
    close_time: str = "15:30"
    pre_open_buffer_minutes: int = 2
    trading_days: List[str] = Field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    holidays: List[str] = Field(default_factory=list)


class IndexConfig(BaseModel):
    enabled: bool = True
    exchange: str = "NSE"
    symbol: str
    token: str
    exchange_type: int = 1
    tick_size: float = 0.05


class TimeframesConfig(BaseModel):
    default: str = "5min"
    available: List[str] = Field(default_factory=lambda: ["1min", "3min", "5min", "15min"])


class SetupBaseConfig(BaseModel):
    enabled: bool = True
    timeframe: str = "5min"
    indices: List[str] = Field(default_factory=list)
    alert_cooldown_seconds: int = 60
    max_buy_alerts_per_day: int = 1
    max_sell_alerts_per_day: int = 1

    # Setup-specific knobs are passed through
    model_config = {"extra": "allow"}


class PatternEngineConfig(BaseModel):
    enabled: bool = False
    model_config = {"extra": "allow"}


class TelegramConfig(BaseModel):
    parse_mode: str = "Markdown"
    send_startup_message: bool = True
    send_shutdown_message: bool = True
    rate_limit_per_minute: int = 20


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/scanner.log"
    rotation: str = "10 MB"
    retention: str = "14 days"
    backtrace: bool = True
    diagnose: bool = False


class AppConfig(BaseModel):
    """Top-level YAML config object."""

    market: MarketConfig = Field(default_factory=MarketConfig)
    indices: Dict[str, IndexConfig] = Field(default_factory=dict)
    timeframes: TimeframesConfig = Field(default_factory=TimeframesConfig)
    setups: Dict[str, SetupBaseConfig] = Field(default_factory=dict)
    patterns: PatternEngineConfig = Field(default_factory=PatternEngineConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("indices", mode="before")
    @classmethod
    def _coerce_indices(cls, v: Any) -> Any:
        return v or {}

    def enabled_indices(self) -> List[str]:
        return [name for name, cfg in self.indices.items() if cfg.enabled]

    def enabled_setups(self) -> List[str]:
        return [name for name, cfg in self.setups.items() if cfg.enabled]


def load_yaml_config(path: Optional[str] = None) -> AppConfig:
    """Load and validate `config.yaml`."""
    cfg_path = Path(path or settings.config_path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    return AppConfig.model_validate(data)


# Module-level singleton for env settings.
settings = Settings()  # type: ignore[call-arg]
