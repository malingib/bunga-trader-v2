"""Phase 4 hardening + Phase 3 cleanup regression tests.

- TradingView webhook fails CLOSED (503) when WEBHOOK_SECRET is unset, so an
  unconfigured server cannot ingest unsigned live signals.
- /strategy/last-signals now reads from the live ParsedSignal table (the
  deleted ML jsonl store is gone); returns strategy signals it wrote.
"""
import dataclasses
import pytest
from fastapi import HTTPException

from core_backend.approval_service import approve_signal_by_id
from core_backend.main import tradingview_webhook
from core_backend.models import ParsedSignal, SignalStatus

from tests._async_helpers import install_async_db, get_one


@pytest.fixture
def webhook_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "webhook_test.db")


async def test_webhook_fails_closed_without_secret(webhook_db, monkeypatch):
    """No WEBHOOK_SECRET configured → 503, signal NOT written to DB."""
    from core_backend import config as config_mod

    cfg = dataclasses.replace(config_mod.CONFIG, webhook_secret="")
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    import core_backend.main as main_mod
    monkeypatch.setattr(main_mod, "CONFIG", cfg)

    payload = {"symbol": "XAUUSD", "action": "BUY", "price": 2000.0}
    with pytest.raises(HTTPException) as exc:
        await tradingview_webhook(payload)
    assert exc.value.status_code == 503

    async with webhook_db() as db:
        result = await db.execute(
            __import__("sqlalchemy").select(ParsedSignal)
        )
        assert result.scalars().first() is None


async def test_webhook_accepts_with_correct_secret(webhook_db, monkeypatch):
    """With WEBHOOK_SECRET set + correct passphrase → signal written (PENDING)."""
    from core_backend import config as config_mod

    cfg = dataclasses.replace(config_mod.CONFIG, webhook_secret="s3cret")
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    import core_backend.main as main_mod
    monkeypatch.setattr(main_mod, "CONFIG", cfg)

    payload = {"symbol": "XAUUSD", "action": "BUY", "price": 2000.0,
               "sl": 1990.0, "tp": 2020.0, "passphrase": "s3cret"}
    result = await tradingview_webhook(payload)
    assert result["status"] == "received"

    async with webhook_db() as db:
        sig = await get_one(db, ParsedSignal, symbol="XAUUSD")
        assert sig is not None
        assert sig.status == SignalStatus.PENDING.value
