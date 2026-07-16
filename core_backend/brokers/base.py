"""Abstract broker interface for OANDA / Deriv / future adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class OrderResult:
    """Normalised result returned by every broker's place_order()."""
    success: bool
    order_id: Optional[str] = None
    fill_price: Optional[float] = None
    filled_units: Optional[float] = None
    error: Optional[str] = None
    broker_order_id: Optional[str] = None


@dataclass
class PositionInfo:
    """Normalised open-position info."""
    symbol: str
    side: str  # "BUY" | "SELL"
    size: float
    entry_price: float
    current_price: float
    pnl: float
    broker_position_id: str


class BrokerBase(ABC):
    """Every broker adapter must implement this interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier e.g. 'oanda', 'deriv'."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection / authenticate. Returns True on success."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down connection gracefully."""

    @abstractmethod
    async def get_balance(self) -> Optional[float]:
        """Return account balance in USD, or None on failure."""

    @abstractmethod
    async def place_order(
        self,
        *,
        action: str,        # BUY | SELL | BUY_LIMIT | SELL_LIMIT | BUY_STOP | SELL_STOP
        symbol: str,         # internal symbol (XAUUSD, SP500, NAS100)
        entry_price: Optional[float],
        sl: Optional[float],
        tp: Optional[float],
        tp2: Optional[float],
        tp3: Optional[float],
        lot: float,
    ) -> OrderResult:
        """Place a trade order derived from an approved signal."""

    @abstractmethod
    async def get_positions(self) -> List[PositionInfo]:
        """Return list of currently open positions."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the adapter currently holds a live connection."""
