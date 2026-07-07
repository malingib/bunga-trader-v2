"""Bunga Trader - Configuration Module"""
import logging
import os
import sys
from dataclasses import dataclass
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("Config")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    tg_api_id: int
    tg_api_hash: str
    tg_phone: str
    signal_channels: List[str]
    mt5_login: int
    mt5_password: str
    mt5_server: str
    default_risk_percent: float
    max_lot: float
    max_consecutive_losses: int
    max_daily_loss_percent: float
    daily_profit_target_percent: float
    min_rr_ratio: float
    signal_max_age_minutes: int
    demo_balance: float
    api_key: Optional[str]
    log_level: str
    google_api_key: str
    groq_api_key: str
    openrouter_api_key: str
    ws_host: str = "127.0.0.1"
    ws_port: int = 8000
    database_url: str = "sqlite:///data/bunga.db"


def load_config() -> Config:
    errors = []
    try:
        api_id = int(os.getenv("TG_API_ID", "0"))
        if api_id == 0:
            errors.append("TG_API_ID required")
    except ValueError:
        errors.append("TG_API_ID must be integer")
        api_id = 0

    api_hash = os.getenv("TG_API_HASH", "")
    if not api_hash:
        errors.append("TG_API_HASH required")

    phone = os.getenv("TG_PHONE", "")
    if not phone:
        errors.append("TG_PHONE required")
    elif not phone.startswith("+"):
        errors.append("TG_PHONE needs country code")

    channels_raw = os.getenv("SIGNAL_CHANNELS", "")
    channels = [c.strip() for c in channels_raw.split(",") if c.strip()] if channels_raw else []
    if not channels:
        errors.append("SIGNAL_CHANNELS required (comma-separated)")

    try:
        mt5_login = int(os.getenv("MT5_LOGIN", "0"))
    except ValueError:
        mt5_login = 0

    try:
        risk_pct = float(os.getenv("DEFAULT_RISK_PERCENT", "1.0"))
        if not 0.1 <= risk_pct <= 10.0:
            errors.append("DEFAULT_RISK_PERCENT must be 0.1-10")
    except ValueError:
        risk_pct = 1.0
        errors.append("DEFAULT_RISK_PERCENT must be number")

    try:
        max_lot = float(os.getenv("MAX_LOT", "1.0"))
        if max_lot <= 0:
            errors.append("MAX_LOT must be positive")
    except ValueError:
        max_lot = 1.0
        errors.append("MAX_LOT must be number")

    try:
        consec_losses = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))
    except ValueError:
        consec_losses = 3

    try:
        max_loss = float(os.getenv("MAX_DAILY_LOSS_PERCENT", "5.0"))
    except ValueError:
        max_loss = 5.0

    try:
        profit_target = float(os.getenv("DAILY_PROFIT_TARGET_PERCENT", "5.0"))
    except ValueError:
        profit_target = 5.0

    try:
        min_rr_ratio = float(os.getenv("MIN_RR_RATIO", "1.0"))
        if min_rr_ratio <= 0:
            errors.append("MIN_RR_RATIO must be positive")
    except ValueError:
        min_rr_ratio = 1.0
        errors.append("MIN_RR_RATIO must be number")

    try:
        demo_balance = float(os.getenv("DEMO_BALANCE", "10000.0"))
    except ValueError:
        demo_balance = 10000.0

    try:
        signal_max_age_minutes = int(os.getenv("SIGNAL_MAX_AGE_MINUTES", "30"))
        if signal_max_age_minutes <= 0:
            errors.append("SIGNAL_MAX_AGE_MINUTES must be positive")
    except ValueError:
        signal_max_age_minutes = 30
        errors.append("SIGNAL_MAX_AGE_MINUTES must be an integer")

    api_key = os.getenv("API_KEY", "").strip() or None
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/bunga.db")

    google_key = os.getenv("GOOGLE_API_KEY", "")
    groq_key = os.getenv("GROQ_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    if not any([google_key, groq_key, openrouter_key]):
        errors.append(
            "At least one LLM API key required (GOOGLE_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY)"
        )

    if errors:
        logger.error("Configuration errors: %s", "; ".join(errors))
        sys.exit(1)

    return Config(
        tg_api_id=api_id,
        tg_api_hash=api_hash,
        tg_phone=phone,
        signal_channels=channels,
        mt5_login=mt5_login,
        mt5_password=os.getenv("MT5_PASSWORD", ""),
        mt5_server=os.getenv("MT5_SERVER", ""),
        default_risk_percent=risk_pct,
        max_lot=max_lot,
        max_consecutive_losses=consec_losses,
        max_daily_loss_percent=max_loss,
        daily_profit_target_percent=profit_target,
        min_rr_ratio=min_rr_ratio,
        signal_max_age_minutes=signal_max_age_minutes,
        demo_balance=demo_balance,
        api_key=api_key,
        log_level=log_level,
        google_api_key=google_key,
        groq_api_key=groq_key,
        openrouter_api_key=openrouter_key,
        database_url=database_url,
    )


CONFIG = load_config()
