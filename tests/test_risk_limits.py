"""Tests for P&L-based daily limit gating."""
import pytest

from core_backend.risk_engine import check_daily_limits
from core_backend.config import CONFIG

from tests._async_helpers import install_async_db


@pytest.fixture
def risk_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "risk_limits.db")


async def test_block_daily_loss_hit(risk_db, monkeypatch):
    async def fake_pnl_percent(account_balance):
        return -CONFIG.max_daily_loss_percent - 0.1

    monkeypatch.setattr(
        "core_backend.risk_engine.get_daily_pnl_percent", fake_pnl_percent
    )
    allowed, reason = await check_daily_limits(1000.0)
    assert allowed is False
    assert "loss limit" in str(reason).lower()


async def test_block_daily_profit_target(risk_db, monkeypatch):
    async def fake_pnl_percent(account_balance):
        return CONFIG.daily_profit_target_percent + 0.1

    monkeypatch.setattr(
        "core_backend.risk_engine.get_daily_pnl_percent", fake_pnl_percent
    )
    allowed, reason = await check_daily_limits(1000.0)
    assert allowed is False
    assert "profit target" in str(reason).lower()


async def test_block_consecutive_losses(risk_db, monkeypatch):
    async def fake_pnl_percent(account_balance):
        return 0.0

    async def fake_consec():
        return CONFIG.max_consecutive_losses + 1

    monkeypatch.setattr(
        "core_backend.risk_engine.get_daily_pnl_percent", fake_pnl_percent
    )
    monkeypatch.setattr(
        "core_backend.risk_engine.get_consecutive_losses", fake_consec
    )
    allowed, reason = await check_daily_limits(1000.0)
    assert allowed is False
    assert "consecutive losses" in str(reason).lower()


async def test_allow_healthy_day(risk_db, monkeypatch):
    async def fake_pnl_percent(account_balance):
        return 0.0

    async def fake_consec():
        return 0

    monkeypatch.setattr(
        "core_backend.risk_engine.get_daily_pnl_percent", fake_pnl_percent
    )
    monkeypatch.setattr(
        "core_backend.risk_engine.get_consecutive_losses", fake_consec
    )
    allowed, reason = await check_daily_limits(1000.0)
    assert allowed is True
    assert reason is None
