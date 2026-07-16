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

def apply_migrations() -> None:
    """Apply manual schema migrations to the live database.

    Idempotent and safe to call on every startup. Runs inside a single
    transaction so a partial failure rolls back cleanly.
    """
    with engine.begin() as conn:
        cols = [
            r[1]
            for r in conn.execute(text("PRAGMA table_info(parsed_signals)")).fetchall()
        ]
        if "raw_signal_id" not in cols:
            return
        # raw_signal_id is wrapped in a UNIQUE constraint AND a FOREIGN KEY to
        # the (now-removed) raw_signals table. SQLite's ALTER TABLE DROP COLUMN
        # cannot rebuild a table where the column is part of a FK/UNIQUE, so we
        # rebuild parsed_signals manually, dropping the column and its
        # constraints in the process.
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
