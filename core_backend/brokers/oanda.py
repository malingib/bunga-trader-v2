"""
OANDA v20 REST API adapter.

Uses Bearer-token auth and communicates over HTTPS.
Supports practice (demo) and live environments.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import httpx

from .base import BrokerBase, OrderResult, PositionInfo

logger = logging.getLogger("OandaBroker")

# ---------------------------------------------------------------------------
# symbol map  (internal -> OANDA instrument name)
# ---------------------------------------------------------------------------
_INSTRUMENT_MAP: Dict[str, str] = {
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    "SP500": "SPX500_USD",
    "NAS100": "NAS100_USD",
    "US30": "US30_USD",
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "BTCUSD": "BTC_USD",
}

# Reverse map — we only need instrument -> internal for positions
_INTERNAL_SYMBOL = {v: k for k, v in _INSTRUMENT_MAP.items()}


def _resolve_instrument(symbol: str) -> str:
    """Return OANDA instrument name or raise."""
    inst = _INSTRUMENT_MAP.get(symbol.upper())
    if inst is None:
        raise ValueError(f"Unsupported symbol '{symbol}' for OANDA. Supported: {list(_INSTRUMENT_MAP.keys())}")
    return inst


def _resolve_action(action: str) -> int:
    """Return +1 for BUY, -1 for SELL (units sign)."""
    upper = action.upper()
    if upper in ("BUY", "BUY_STOP", "BUY_LIMIT"):
        return 1
    elif upper in ("SELL", "SELL_STOP", "SELL_LIMIT"):
        return -1
    raise ValueError(f"Unknown action: {action}")


class OandaBroker(BrokerBase):
    """OANDA v20 REST adapter.  Configure via env vars OANDA_API_KEY / OANDA_ACCOUNT_ID / OANDA_ENVIRONMENT."""

    def __init__(
        self,
        api_key: str = "",
        account_id: str = "",
        environment: str = "practice",
    ):
        self._api_key = api_key
        self._account_id = account_id
        self._environment = environment  # "practice" | "live"
        self._http: Optional[httpx.AsyncClient] = None
        self._base_url: str = ""
        self._connected = False

    # -- properties ---------------------------------------------------------

    @property
    def name(self) -> str:
        return "oanda"

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> bool:
        """Load config lazily so the adapter can be instantiated before env is ready."""
        if self._connected:
            return True
        if not self._api_key or not self._account_id:
            # Try to pick from environment via core_backend.config — imported late
            # to avoid circular import during module scan.
            from ..config import CONFIG  # type: ignore[import-untyped]

            self._api_key = getattr(CONFIG, "oanda_api_key", "") or self._api_key
            self._account_id = getattr(CONFIG, "oanda_account_id", "") or self._account_id
            env = getattr(CONFIG, "oanda_environment", "practice")
            if env:
                self._environment = env

        if not self._api_key or not self._account_id:
            logger.error("OANDA not configured — set OANDA_API_KEY and OANDA_ACCOUNT_ID in .env")
            return False

        domain = "api-fxpractice" if self._environment == "practice" else "api-fxtrade"
        self._base_url = f"https://{domain}.oanda.com/v3/accounts/{self._account_id}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(15.0),
        )
        # Verify connectivity by fetching account summary
        try:
            r = await self._http.get("")
            if r.status_code == 200:
                self._connected = True
                logger.info("OANDA connected to account %s (%s)", self._account_id[:8] + "…", self._environment)
                return True
            logger.error("OANDA connect failed: %s %s", r.status_code, r.text[:200])
        except httpx.RequestError as exc:
            logger.error("OANDA connect error: %s", exc)
        self._connected = False
        return False

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._connected = False
        logger.info("OANDA disconnected")

    # -- account ------------------------------------------------------------

    async def get_balance(self) -> Optional[float]:
        if not self._ensure_connected():
            return None
        try:
            r = await self._http.get("")  # type: ignore[union-attr]
            if r.status_code == 200:
                acct = r.json().get("account", {})
                return float(acct.get("balance", 0.0))
            logger.warning("OANDA balance fetch failed: %s", r.text[:200])
        except Exception as exc:
            logger.error("OANDA balance error: %s", exc)
        return None

    # -- orders -------------------------------------------------------------

    async def place_order(
        self,
        *,
        action: str,
        symbol: str,
        entry_price: Optional[float],
        sl: Optional[float],
        tp: Optional[float],
        tp2: Optional[float],
        tp3: Optional[float],
        lot: float,
    ) -> OrderResult:
        if not self._ensure_connected():
            return OrderResult(success=False, error="Broker not connected")

        instrument = _resolve_instrument(symbol)
        direction = _resolve_action(action)

        units = int(abs(lot) * direction)
        if units == 0:
            return OrderResult(success=False, error="Zero units calculated")

        # Build the OANDA order payload
        order_payload: dict = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
            }
        }

        # Attach stop-loss on fill
        if sl is not None and sl > 0:
            order_payload["order"]["stopLossOnFill"] = {
                "price": str(round(sl, 5)),
            }
        # Attach take-profit on fill
        if tp is not None and tp > 0:
            order_payload["order"]["takeProfitOnFill"] = {
                "price": str(round(tp, 5)),
            }

        try:
            r = await self._http.post("/orders", json=order_payload)  # type: ignore[union-attr]
            body = r.json()
            if r.status_code in (200, 201, 202):
                order_id = (
                    body.get("orderCreateTransaction", {})
                    .get("id")
                    or body.get("lastTransactionID", "")
                )
                fill_price = (
                    body.get("orderFillTransaction", {})
                    .get("price")
                    or body.get("orderCreateTransaction", {})
                    .get("price")
                )
                logger.info(
                    "OANDA order placed: %s %s %s lot=%s id=%s",
                    action, symbol, instrument, lot, order_id,
                )
                return OrderResult(
                    success=True,
                    order_id=order_id,
                    fill_price=float(fill_price) if fill_price else None,
                    filled_units=float(units),
                    broker_order_id=order_id,
                )
            # Rejection
            error_detail = body.get("errorMessage") or str(body.get("errorCode", ""))
            logger.warning("OANDA order rejected: %s", error_detail)
            return OrderResult(success=False, error=error_detail)
        except httpx.RequestError as exc:
            logger.error("OANDA order error: %s", exc)
            return OrderResult(success=False, error=str(exc))

    # -- positions ----------------------------------------------------------

    async def get_positions(self) -> List[PositionInfo]:
        if not self._ensure_connected():
            return []
        try:
            r = await self._http.get("/openPositions")  # type: ignore[union-attr]
            if r.status_code != 200:
                return []
            positions = []
            for pos in r.json().get("positions", []):
                inst = pos.get("instrument", "")
                internal = _INTERNAL_SYMBOL.get(inst, inst)
                # OANDA returns the long or short side that is non-zero
                long = pos.get("long", {}) or {}
                short = pos.get("short", {}) or {}
                if float(long.get("units", "0")) != 0:
                    side = "BUY"
                    size = abs(float(long["units"]))
                    entry = float(long.get("averagePrice", 0))
                    pnl = float(long.get("unrealizedPL", 0))
                else:
                    side = "SELL"
                    size = abs(float(short.get("units", "0")))
                    entry = float(short.get("averagePrice", 0))
                    pnl = float(short.get("unrealizedPL", 0))
                positions.append(
                    PositionInfo(
                        symbol=internal,
                        side=side,
                        size=size,
                        entry_price=entry,
                        current_price=entry,  # OANDA doesn't give current in same call
                        pnl=pnl,
                        broker_position_id=inst,
                    )
                )
            return positions
        except Exception as exc:
            logger.error("OANDA get_positions error: %s", exc)
            return []

    # -- helpers ------------------------------------------------------------

    def _ensure_connected(self) -> bool:
        if not self._connected:
            logger.warning("OANDA not connected — call connect() first")
        return self._connected
