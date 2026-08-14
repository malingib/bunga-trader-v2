"""Tests for manual SQLite schema migrations in core_backend.database.

These guard against the regression where a column added to the ORM model
was never added to existing databases, causing "no such column" errors at
runtime (e.g. auto-approve failing on the live data/bunga.db).
"""
import os
import sqlite3

import pytest

# Keep any core_backend import from pulling live config.
os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+10000000000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")


def _make_legacy_db(path: str) -> None:
    """Create a parsed_signals table WITHOUT strategy_generated_at.

    Mirrors a database that predates commit e17df00 where the ORM gained the
    `strategy_generated_at` column but apply_migrations() didn't add it.
    """
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE parsed_signals (
            id INTEGER NOT NULL,
            action VARCHAR(16) NOT NULL,
            symbol VARCHAR(16) NOT NULL,
            entry_price FLOAT,
            sl FLOAT,
            tp FLOAT,
            tp2 FLOAT,
            tp3 FLOAT,
            raw_text TEXT NOT NULL,
            parsed_at DATETIME,
            status VARCHAR(16),
            lot_size FLOAT,
            risk_percent FLOAT,
            ai_score FLOAT,
            ai_reason TEXT,
            executed_at DATETIME,
            execution_result TEXT,
            PRIMARY KEY (id)
        )
        """
    )
    conn.execute(
        "INSERT INTO parsed_signals (id, action, symbol, raw_text, status) "
        "VALUES (1, 'BUY', 'XAUUSD', 'legacy row', 'pending')"
    )
    conn.commit()
    conn.close()


def test_apply_migrations_adds_strategy_generated_at(tmp_path):
    from core_backend import database

    db_path = tmp_path / "legacy.db"
    _make_legacy_db(str(db_path))

    # Point the module-level engine at the legacy DB and re-run migrations.
    engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.engine = engine

    database.apply_migrations()

    Session = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker(bind=engine)
    with Session() as db:
        cols = {
            r[1]
            for r in db.execute(
                __import__("sqlalchemy").text(
                    "PRAGMA table_info(parsed_signals)"
                )
            ).fetchall()
        }
    assert "strategy_generated_at" in cols
    assert "raw_signal_id" not in cols  # no-op, legacy already clean


def test_parsedsignal_writes_strategy_generated_at(tmp_path):
    """A ParsedSignal carrying strategy_generated_at must persist, no
    'no such column' error, against a migrated pre-existing DB."""
    from core_backend import database
    from core_backend.models import Base, ParsedSignal, SignalStatus

    db_path = tmp_path / "legacy2.db"
    _make_legacy_db(str(db_path))

    engine = database.create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    database.engine = engine
    database.SessionLocal = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    database.apply_migrations()

    with database.get_db() as db:
        ps = ParsedSignal(
            action="BUY",
            symbol="XAUUSD",
            raw_text="[Strategy] test",
            status=SignalStatus.PENDING.value,
            strategy_generated_at="2026-08-14T12:00:00",
        )
        db.add(ps)
        db.commit()
        db.refresh(ps)
        assert ps.id is not None

    # Verify the column round-trips through a raw read.
    conn = sqlite3.connect(str(db_path))
    val = conn.execute(
        "SELECT strategy_generated_at FROM parsed_signals WHERE id = ?",
        (ps.id,),
    ).fetchone()[0]
    conn.close()
    assert val == "2026-08-14T12:00:00"
