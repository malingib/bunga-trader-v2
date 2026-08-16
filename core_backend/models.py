"""Bunga Trader - Database Models"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, ForeignKey, Index, CheckConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone
import enum

Base = declarative_base()

class SignalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"

class ParsedSignal(Base):
    __tablename__ = "parsed_signals"
    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(16), nullable=False, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    entry_price = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    tp2 = Column(Float, nullable=True)
    tp3 = Column(Float, nullable=True)
    raw_text = Column(Text, nullable=False)
    parsed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    status = Column(String(16), default=SignalStatus.PENDING.value, index=True)
    lot_size = Column(Float, nullable=True)
    risk_percent = Column(Float, nullable=True)
    ai_score = Column(Float, nullable=True)
    ai_reason = Column(Text, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    execution_result = Column(Text, nullable=True)
    # Reconciliation key: the engine's generated_at (ISO). Lets a trade
    # close be matched back to this signal by (symbol, generated_at).
    strategy_generated_at = Column(String(32), nullable=True, index=True)
    __table_args__ = (
        CheckConstraint("action IN ('BUY', 'SELL', 'BUY_LIMIT', 'SELL_LIMIT', 'BUY_STOP', 'SELL_STOP')", name="valid_action"),
        Index("idx_status_symbol", "status", "symbol"),
    )

class TradeLog(Base):
    __tablename__ = "trade_logs"
    id = Column(Integer, primary_key=True, index=True)
    parsed_signal_id = Column(Integer, ForeignKey("parsed_signals.id"), nullable=False)
    symbol = Column(String(16), nullable=False, index=True)
    action = Column(String(16), nullable=False)
    lot_size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    tp = Column(Float, nullable=True)
    result = Column(String(16), nullable=False)
    pnl = Column(Float, nullable=True)
    executed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    closed_at = Column(DateTime, nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    __table_args__ = (Index("idx_executed_date", "executed_at"),)

class SystemState(Base):
    __tablename__ = "system_state"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
