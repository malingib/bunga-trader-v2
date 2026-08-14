"""Bunga Trader - Database Module"""
from contextlib import contextmanager
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from .config import CONFIG

engine = create_engine(
    CONFIG.database_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=10000")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

# --- Schema migrations (manual SQL, no Alembic) ---
# Bump the comment below when adding a new migration so the history is
# visible in git blame. Order-sensitive: each step is idempotent.
#
# v2 (2026-07-15): ParsedSignal no longer links to a Telegram raw_signal.
#     Dropped the `raw_signal_id` column that the removed telegram_listener
#     owned. Keeping it caused NOT NULL insert failures on every strategy
#     signal.
#
# v3 (2026-08-14): Added `strategy_generated_at` (String(32), indexed) so a
#     trade-close can backfill its outcome onto the matching ML training
#     record. The ORM model gained the column in commit e17df00 but the
#     migration never added it to existing databases, so `db.query
#     (ParsedSignal).filter(...)` raised "no such column" on the live DB and
#     auto-approve silently failed.

def _columns(conn) -> set:
    """Return the set of column names on parsed_signals right now."""
    return {
        r[1]
        for r in conn.execute(text("PRAGMA table_info(parsed_signals)")).fetchall()
    }


def apply_migrations() -> None:
    """Apply manual schema migrations to the live database.

    Idempotent and safe to call on every startup. Runs inside a single
    transaction so a partial failure rolls back cleanly.

    Handles two ordered cases:
      1. Legacy `raw_signal_id` column present → full table rebuild (it is
         wrapped in a UNIQUE + FK that ALTER TABLE DROP COLUMN cannot remove).
      2. Missing `strategy_generated_at` column → ALTER TABLE ADD COLUMN.
    """
    with engine.begin() as conn:
        cols = _columns(conn)

        # Case 1: legacy column still present → rebuild and drop it.
        if "raw_signal_id" in cols:
            # raw_signal_id is wrapped in a UNIQUE constraint AND a FOREIGN KEY
            # to the (now-removed) raw_signals table. SQLite's ALTER TABLE DROP
            # COLUMN cannot rebuild a table where the column is part of a
            # FK/UNIQUE, so we rebuild parsed_signals manually, dropping the
            # column and its constraints in the process.
            conn.execute(text("""
                CREATE TABLE parsed_signals_new (
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
                    PRIMARY KEY (id),
                    CONSTRAINT valid_action CHECK (
                        action IN ('BUY', 'SELL', 'BUY_LIMIT', 'SELL_LIMIT', 'BUY_STOP', 'SELL_STOP')
                    )
                )
            """))
            conn.execute(text("""
                INSERT INTO parsed_signals_new
                    (id, action, symbol, entry_price, sl, tp, tp2, tp3,
                     raw_text, parsed_at, status, lot_size, risk_percent,
                     ai_score, ai_reason, executed_at, execution_result)
                SELECT id, action, symbol, entry_price, sl, tp, tp2, tp3,
                       raw_text, parsed_at, status, lot_size, risk_percent,
                       ai_score, ai_reason, executed_at, execution_result
                FROM parsed_signals
            """))
            conn.execute(text("DROP TABLE parsed_signals"))
            conn.execute(text("ALTER TABLE parsed_signals_new RENAME TO parsed_signals"))
            cols = _columns(conn)

        # Case 2: ensure strategy_generated_at exists (added in ORM rev e17df00).
        if "strategy_generated_at" not in cols:
            conn.execute(text(
                "ALTER TABLE parsed_signals "
                "ADD COLUMN strategy_generated_at VARCHAR(32)"
            ))
            # Best-effort index mirror of the ORM `index=True`.
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_strategy_generated_at "
                "ON parsed_signals (strategy_generated_at)"
            ))

@contextmanager
def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def get_db_dependency() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
