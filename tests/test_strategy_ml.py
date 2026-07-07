"""Tests for flat-file ML data storage/strategy pipeline integration."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core_backend.strategies.engine import MLDataLogger, StrategySignal
from core_backend.strategies.config import QUADAPT_CFG


class TestMLDataLogger:
    def test_logs_signal_to_session_and_training_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core_backend.strategies.engine.QUADAPT_CFG.ml_data_dir",
            str(tmp_path),
        )

        logger = MLDataLogger()
        signal = StrategySignal(
            symbol="EURUSD",
            action="BUY",
            entry_price=1.1000,
            sl=1.0950,
            tp=1.1100,
            quality_score=78.5,
            signal_source="Quadapt_ML_Trader",
            confidence="high",
            generated_at=datetime.utcnow(),
        )

        logger.log_signal(signal, {"atr": 0.005, "regime": "trending"})

        session_files = list(tmp_path.glob("session_*.jsonl"))
        assert len(session_files) == 1

        training = tmp_path / "training_data.jsonl"
        assert training.exists()

        with open(session_files[0]) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) >= 1

        import json

        record = json.loads(lines[0])
        assert record["symbol"] == "EURUSD"
        assert record["action"] == "BUY"
        assert record["quality_score"] == 78.5
        assert "features" in record
        assert record["features"]["regime"] == "trending"

    def test_update_outcome_appends_record(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "core_backend.strategies.engine.QUADAPT_CFG.ml_data_dir",
            str(tmp_path),
        )

        logger = MLDataLogger()
        ts = datetime.utcnow()

        logger.update_outcome("EURUSD", ts, "win", pnl=12.5)

        child = next(tmp_path.glob("session_*.jsonl"))
        with open(child) as f:
            lines = [l.strip() for l in f if l.strip()]

        import json

        outcome = json.loads(lines[-1])
        assert outcome["type"] == "outcome_update"
        assert outcome["outcome"] == "win"
        assert outcome["pnl"] == 12.5
        assert outcome["symbol"] == "EURUSD"


class TestStrategyPollerIntegration:
    def test_poll_once_foreach_signal_writes_db(self):
        from core_backend.sources.strategy_source import StrategyPoller

        signal = StrategySignal(
            symbol="EURUSD",
            action="BUY",
            entry_price=1.1000,
            sl=1.0950,
            tp=1.1100,
            quality_score=75.0,
            signal_source="Quadapt_ML_Trader",
            confidence="high",
        )

        poller = StrategyPoller.__new__(StrategyPoller)
        poller.engine = MagicMock()
        poller.engine.run_poll.return_value = [signal]

        with patch(
            "core_backend.sources.strategy_source._signal_to_db",
            return_value=5,
        ) as mock_db_write:
            import asyncio

            results = asyncio.run(poller.poll_once())
            mock_db_write.assert_called_once_with(signal)
            assert results == [signal]

    def test_poll_once_skips_when_no_signals(self):
        from core_backend.sources.strategy_source import StrategyPoller

        poller = StrategyPoller.__new__(StrategyPoller)
        poller.engine = MagicMock()
        poller.engine.run_poll.return_value = []

        with patch(
            "core_backend.sources.strategy_source._signal_to_db",
            return_value=5,
        ) as mock_db_write:
            import asyncio

            assert asyncio.run(poller.poll_once()) == []
            mock_db_write.assert_not_called()
