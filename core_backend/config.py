"""Bunga Trader - Configuration Module"""
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("Config")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    default_risk_percent: float
    max_lot: float
    max_consecutive_losses: int
    max_daily_loss_percent: float
    daily_profit_target_percent: float
    min_rr_ratio: float
    signal_max_age_minutes: int
    approved_signal_max_age_minutes: int
    demo_balance: float
    api_key: Optional[str]
    log_level: str
    ws_host: str = "127.0.0.1"
    ws_port: int = 8000
    webhook_secret: str = ""
    # Optional dashboard shared-secret. When set, all mutating (POST/PUT/DELETE)
    # requests must carry `X-Dashboard-Token: <value>`. Leave empty for the
    # default local-only loopback deployment (no auth needed on 127.0.0.1).
    dashboard_token: str = ""
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"
    deriv_app_id: int = 0
    deriv_api_token: str = ""
    database_url: str = "sqlite:///data/bunga.db"
    # Deprecated — kept for backward compat (Telegram, LLM providers)
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_phone: str = ""
    signal_channels: List[str] = field(default_factory=list)
    google_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    dispatch_circuit_max_failures: int = 3
    # Deriv MCP endpoint (for reference)
    # WS API: wss://ws.derivws.com/websockets/v3?app_id=1089
    # REST API: https://api.deriv.com
    # MCP API: https://mcp-api.deriv.com/mcp


def load_config() -> Config:
    errors = []

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

    try:
        approved_max_age = int(os.getenv("APPROVED_SIGNAL_MAX_AGE_MINUTES", "60"))
        if approved_max_age <= 0:
            errors.append("APPROVED_SIGNAL_MAX_AGE_MINUTES must be positive")
    except ValueError:
        approved_max_age = 60
        errors.append("APPROVED_SIGNAL_MAX_AGE_MINUTES must be an integer")

    api_key = os.getenv("API_KEY", "").strip() or None
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    database_url = os.getenv("DATABASE_URL", "sqlite:///data/bunga.db")

    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    dashboard_token = os.getenv("DASHBOARD_TOKEN", "").strip()

    # Broker env vars
    oanda_api_key = os.getenv("OANDA_API_KEY", "")
    oanda_account_id = os.getenv("OANDA_ACCOUNT_ID", "")
    oanda_environment = os.getenv("OANDA_ENVIRONMENT", "practice")
    try:
        deriv_app_id = int(os.getenv("DERIV_APP_ID", "0"))
    except ValueError:
        deriv_app_id = 0
    deriv_api_token = os.getenv("DERIV_API_TOKEN", "")

    if errors:
        logger.error("Configuration errors: %s", "; ".join(errors))
        sys.exit(1)

    return Config(
        default_risk_percent=risk_pct,
        max_lot=max_lot,
        max_consecutive_losses=consec_losses,
        max_daily_loss_percent=max_loss,
        daily_profit_target_percent=profit_target,
        min_rr_ratio=min_rr_ratio,
        signal_max_age_minutes=signal_max_age_minutes,
        approved_signal_max_age_minutes=approved_max_age,
        demo_balance=demo_balance,
        api_key=api_key,
        log_level=log_level,
        database_url=database_url,
        webhook_secret=webhook_secret,
        dashboard_token=dashboard_token,
        oanda_api_key=oanda_api_key,
        oanda_account_id=oanda_account_id,
        oanda_environment=oanda_environment,
        deriv_app_id=deriv_app_id,
        deriv_api_token=deriv_api_token,
    )


CONFIG = load_config()
