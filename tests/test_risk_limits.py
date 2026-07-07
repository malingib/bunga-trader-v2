"""Tests for P&L-based daily limit gating."""
from core_backend.risk_engine import check_daily_limits
from core_backend.config import CONFIG


class TestProfitBasedLimits:
    def test_block_daily_loss_hit(self, risk_db_session, monkeypatch):
        monkeypatch.setattr(
            "core_backend.risk_engine.get_daily_pnl_percent",
            lambda account_balance: -CONFIG.max_daily_loss_percent - 0.1,
        )
        allowed, reason = check_daily_limits(1000.0)
        assert allowed is False
        assert "loss limit" in str(reason).lower()

    def test_block_daily_profit_target(self, risk_db_session, monkeypatch):
        monkeypatch.setattr(
            "core_backend.risk_engine.get_daily_pnl_percent",
            lambda account_balance: CONFIG.daily_profit_target_percent + 0.1,
        )
        allowed, reason = check_daily_limits(1000.0)
        assert allowed is False
        assert "profit target" in str(reason).lower()

    def test_block_consecutive_losses(self, risk_db_session, monkeypatch):
        monkeypatch.setattr(
            "core_backend.risk_engine.get_daily_pnl_percent",
            lambda account_balance: 0.0,
        )
        monkeypatch.setattr(
            "core_backend.risk_engine.get_consecutive_losses",
            lambda: CONFIG.max_consecutive_losses + 1,
        )
        allowed, reason = check_daily_limits(1000.0)
        assert allowed is False
        assert "consecutive losses" in str(reason).lower()

    def test_allow_healthy_day(self, risk_db_session, monkeypatch):
        monkeypatch.setattr(
            "core_backend.risk_engine.get_daily_pnl_percent",
            lambda account_balance: 0.0,
        )
        monkeypatch.setattr(
            "core_backend.risk_engine.get_consecutive_losses",
            lambda: 0,
        )
        allowed, reason = check_daily_limits(1000.0)
        assert allowed is True
        assert reason is None
