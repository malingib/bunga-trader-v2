"""
Deriv WebSocket API adapter.

Uses aiohttp WebSocket client to communicate with Deriv's WS API.
Supports contract-based trading (CALL = BUY, PUT = SELL) with
optional barriers for SL/TP approximation.

Auth: OAuth 2.0 token (recommended) or legacy API token via `authorize` message.
WS endpoint: wss://ws.derivws.com/websockets/v3?app_id={app_id}

LIMITATIONS
  - Deriv does spot/CFD via contracts with expiry, not open-ended positions.
    SL/TP are approximated via contract barriers.
  - Best suited for synthetic / volatility indices and short-duration trades.
  - OANDA adapter is preferred for XAUUSD / SP500 / NAS100 spot trading.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BrokerBase, OrderResult, PositionInfo

logger = logging.getLogger("DerivBroker")

WS_BASE = "wss://ws.derivws.com/websockets/v3"
DEFAULT_APP_ID = 1089  # Deriv's public demo app ID

_INSTRUMENT_MAP: Dict[str, str] = {
    "XAUUSD": "xauusd",
    "XAGUSD": "xagusd",
    "SP500": "us500",
    "NAS100": "us100",
    "US30": "us30",
    "EURUSD": "eurusd",
    "GBPUSD": "gbpusd",
    "USDJPY": "usdjpy",
    "BTCUSD": "btcusd",
}

_ACTION_TO_CONTRACT = {
    "BUY": "CALL",
    "SELL": "PUT",
    "BUY_LIMIT": "CALL",
    "SELL_LIMIT": "PUT",
    "BUY_STOP": "CALL",
    "SELL_STOP": "PUT",
}


def _resolve_symbol(symbol: str) -> str:
    s = _INSTRUMENT_MAP.get(symbol.upper())
    if s is None:
        raise ValueError(f"Unsupported symbol '{symbol}' for Deriv. Supported: {list(_INSTRUMENT_MAP.keys())}")
    return s


class DerivBroker(BrokerBase):
    """Deriv WS adapter. Configure via env vars DERIV_APP_ID and DERIV_API_TOKEN."""

    def __init__(self, app_id: int = 0, api_token: str = ""):
        self._app_id = app_id
        self._api_token = api_token
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._connected = False
        self._currency: str = "USD"
        self._loginid: str = ""

    # -- properties ---------------------------------------------------------

    @property
    def name(self) -> str:
        return "deriv"

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> bool:
        if self._connected:
            return True

        if not self._app_id:
            from ..config import CONFIG
            self._app_id = int(getattr(CONFIG, "deriv_app_id", 0) or 0)
            self._api_token = getattr(CONFIG, "deriv_api_token", "") or self._api_token

        if not self._app_id or self._app_id == 0:
            self._app_id = DEFAULT_APP_ID
            logger.info("Deriv: using default demo App ID %s", DEFAULT_APP_ID)

        if not self._api_token:
            logger.error("Deriv not configured — set DERIV_API_TOKEN in .env")
            return False

        self._session = aiohttp.ClientSession()
        url = f"{WS_BASE}?app_id={self._app_id}"
        try:
            self._ws = await self._session.ws_connect(url, heartbeat=30.0)
        except Exception as exc:
            logger.error("Deriv WS connect failed: %s", exc)
            await self.disconnect()
            return False

        auth_msg = {"authorize": self._api_token}
        resp = await self._send_and_wait(auth_msg, "authorize")
        if resp is None or "error" in resp:
            err = resp.get("error", {}).get("message", "auth failed") if resp else "no response"
            logger.error("Deriv auth failed: %s", err)
            await self.disconnect()
            return False

        auth_data = resp.get("authorize", {})
        self._currency = auth_data.get("currency", "USD")
        self._loginid = auth_data.get("loginid", "")
        balance = auth_data.get("balance", 0)
        self._connected = True
        logger.info(
            "Deriv connected: %s balance=%s %s",
            self._loginid, balance, self._currency,
        )
        return True

    async def disconnect(self) -> None:
        self._connected = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        logger.info("Deriv disconnected")

    # -- account ------------------------------------------------------------

    async def get_balance(self) -> Optional[float]:
        if not self._ensure_connected():
            return None
        resp = await self._send_and_wait({"balance": 1}, "balance")
        if resp and "balance" in resp:
            return float(resp["balance"].get("balance", 0.0))
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
            return OrderResult(success=False, error="Deriv not connected")

        deriv_symbol = _resolve_symbol(symbol)
        contract_type = _ACTION_TO_CONTRACT.get(action.upper(), "CALL")

        amount = max(lot * 10, 1.0)
        proposal_req: Dict[str, Any] = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": self._currency,
            "symbol": deriv_symbol,
            "duration": 1,
            "duration_unit": "d",
        }
        if sl is not None and sl > 0:
            proposal_req["barrier"] = str(round(sl, 5))

        proposal_resp = await self._send_and_wait(proposal_req, "proposal")
        if proposal_resp is None:
            return OrderResult(success=False, error="Proposal request failed")
        if "error" in proposal_resp:
            return OrderResult(
                success=False,
                error=proposal_resp["error"].get("message", "proposal error"),
            )

        proposal_id = proposal_resp.get("proposal", {}).get("id")
        proposal_price = proposal_resp.get("proposal", {}).get("ask_price", 0)
        if not proposal_id:
            return OrderResult(success=False, error="No proposal ID returned")

        buy_req = {"buy": proposal_id, "price": proposal_price}
        buy_resp = await self._send_and_wait(buy_req, "buy")
        if buy_resp is None:
            return OrderResult(success=False, error="Buy request timed out")
        if "error" in buy_resp:
            return OrderResult(
                success=False,
                error=buy_resp["error"].get("message", "buy error"),
            )

        buy_data = buy_resp.get("buy", {})
        contract_id = buy_data.get("contract_id", "")
        transaction_id = buy_data.get("transaction_id", 0)
        buy_price = float(buy_data.get("buy_price", 0))

        logger.info(
            "Deriv order placed: %s %s contract=%s price=%s id=%s",
            action, symbol, contract_type, buy_price, contract_id,
        )

        if tp is not None and tp > 0 and contract_id:
            await self._send_and_wait(
                {"contract_update": 1, "contract_id": contract_id,
                 "limit_order": {"take_profit": round(tp, 5)}},
                "contract_update",
            )
        if sl is not None and sl > 0 and contract_id:
            await self._send_and_wait(
                {"contract_update": 1, "contract_id": contract_id,
                 "limit_order": {"stop_loss": round(sl, 5)}},
                "contract_update",
            )

        return OrderResult(
            success=True,
            order_id=str(transaction_id),
            fill_price=buy_price,
            filled_units=lot,
            broker_order_id=str(contract_id),
        )

    # -- positions ----------------------------------------------------------

    async def get_positions(self) -> List[PositionInfo]:
        if not self._ensure_connected():
            return []
        try:
            resp = await self._send_and_wait({"portfolio": 1}, "portfolio")
            if not resp or "error" in resp:
                return []
            result: List[PositionInfo] = []
            for contract in resp.get("portfolio", {}).get("contracts", []):
                result.append(
                    PositionInfo(
                        symbol=contract.get("display_name", "?"),
                        side="BUY" if contract.get("contract_type") in ("CALL",) else "SELL",
                        size=float(contract.get("buy_price", 0)),
                        entry_price=float(contract.get("entry_tick", 0)),
                        current_price=float(contract.get("current_tick", 0)),
                        pnl=float(contract.get("profit", 0)),
                        broker_position_id=str(contract.get("contract_id", "")),
                    )
                )
            return result
        except Exception as exc:
            logger.error("Deriv positions error: %s", exc)
            return []

    # -- helpers ------------------------------------------------------------

    async def _send_and_wait(self, msg: dict, expect_key: str) -> Optional[dict]:
        if self._ws is None:
            return None
        try:
            msg["req_id"] = hash(f"{expect_key}{msg}") & 0x7FFFFFFF
            await self._ws.send_json(msg)
            while True:
                raw = await self._ws.receive(timeout=10.0)
                if raw.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(raw.data)
                    if expect_key in data or "error" in data:
                        return data
                elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    return None
        except Exception as exc:
            logger.warning("Deriv WS error (%s): %s", expect_key, exc)
            return None
        return None

    def _ensure_connected(self) -> bool:
        if not self._connected:
            logger.warning("Deriv not connected — call connect() first")
        return self._connected
