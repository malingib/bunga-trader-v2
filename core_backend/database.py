"""Bunga Trader - Database Module

Async SQLAlchemy 2.0 setup. The engine is async (aiosqlite driver) so DB
calls inside FastAPI request handlers can `await` and release the event loop
instead of blocking it on every query — the standard FastAPI + SQLAlchemy 2.0
pattern. Sessions are provided via async context managers / async dependencies.
"""
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from .config import CONFIG

# SQLite needs the aiosqlite driver for async. Rewrite the URL scheme if the
# configured URL is the synchronous sqlite:/// form.
_database_url = CONFIG.database_url
if _database_url.startswith("sqlite:///"):
    _database_url = _database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

_connect_args: dict = {"check_same_thread": False}
engine: AsyncEngine = create_async_engine(
    _database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

# Apply SQLite perf pragmas on every new connection.
from sqlalchemy import event  # noqa: E402


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=10000")
    cursor.close()


SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


# --- Schema migrations (manual SQL, no Alembic) ---
# Bump the comment below when adding a new migration so the history is
# visible in git blame. Order-sensitive: each step is idempotent.
#
# v2 (2026-07-15): ParsedSignal no longer links to a Telegram raw_signal.
#     Dropped the `raw_signal_id` column that the removed telegram_listener
#     owned. Keeping it caused NOT NULL insert failures on every strategy
#     signal.
#
# v3 (2026-08-14): Added `strategy_generated_at` (String(32), indexed) so
#     strategy-originated signals are distinguishable from manual/TradingView
#     ones and surfaced via /strategy/last-signals. The ORM model gained the
#     column but the migration never added it to existing databases, so reads
#     raised "no such column" on the live DB.
#
# v4 (2026-08-16): Added `trade_logs.closed_at` (DATETIME) for position
#     reconciliation — the loop that finalizes TradeLog rows whose broker
#     position has closed (approximate realized P&L). Idempotent: skipped when
#     trade_logs is absent or the column already exists.


async def _columns(conn) -> set:
    """Return the set of column names on parsed_signals right now."""
    result = await conn.execute(text("PRAGMA table_info(parsed_signals)"))
    return {row[1] for row in result.fetchall()}


async def apply_migrations() -> None:
    """Apply manual schema migrations to the live database.

    Idempotent and safe to call on every startup. Runs inside a single
    transaction so a partial failure rolls back cleanly.

    Handles two ordered cases:
      1. Legacy `raw_signal_id` column present → full table rebuild (it is
         wrapped in a UNIQUE + FK that ALTER TABLE DROP COLUMN cannot remove).
      2. Missing `strategy_generated_at` column → ALTER TABLE ADD COLUMN.
    """
    async with engine.begin() as conn:
        cols = await _columns(conn)

        # Case 1: legacy column still present → rebuild and drop it.
        if "raw_signal_id" in cols:
            await conn.execute(text("""
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
            await conn.execute(text("""
                INSERT INTO parsed_signals_new
                    (id, action, symbol, entry_price, sl, tp, tp2, tp3,
                     raw_text, parsed_at, status, lot_size, risk_percent,
                     ai_score, ai_reason, executed_at, execution_result)
                SELECT id, action, symbol, entry_price, sl, tp, tp2, tp3,
                       raw_text, parsed_at, status, lot_size, risk_percent,
                       ai_score, ai_reason, executed_at, execution_result
                FROM parsed_signals
            """))
            await conn.execute(text("DROP TABLE parsed_signals"))
            await conn.execute(text("ALTER TABLE parsed_signals_new RENAME TO parsed_signals"))
            cols = await _columns(conn)

        # Case 2: ensure strategy_generated_at exists (added in ORM rev e17df00).
        if "strategy_generated_at" not in cols:
            await conn.execute(text(
                "ALTER TABLE parsed_signals "
                "ADD COLUMN strategy_generated_at VARCHAR(32)"
            ))
            # Best-effort index mirror of the ORM `index=True`.
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_strategy_generated_at "
                "ON parsed_signals (strategy_generated_at)"
            ))

        # Case 3: ensure trade_logs.closed_at exists (position reconciliation).
        # _columns_trades returns an empty set when the table itself is absent
        # (fresh/legacy DB); in that case there is nothing to migrate.
        trades_cols = await _columns_trades(conn)
        if trades_cols and "closed_at" not in trades_cols:
            await conn.execute(text(
                "ALTER TABLE trade_logs ADD COLUMN closed_at DATETIME"
            ))


async def _columns_trades(conn) -> set:
    """Return the set of column names on trade_logs, or empty if table absent."""
    # Guard: trade_logs may not exist yet on a fresh/legacy DB (tables are
    # normally created by Base.metadata.create_all at startup). Without this
    # guard the ALTER below would raise "no such table: trade_logs".
    tables = await conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_logs'"
    ))
    if not tables.fetchall():
        return set()
    result = await conn.execute(text("PRAGMA table_info(trade_logs)"))
    return {row[1] for row in result.fetchall()}


@asynccontextmanager
async def get_db() -> AsyncSession:
    """Async context manager yielding a session that commits on success."""
    db = SessionLocal()
    try:
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()


async def get_db_dependency() -> AsyncSession:
    """FastAPI dependency that yields an AsyncSession for request handlers."""
    async with get_db() as db:
        yield db
