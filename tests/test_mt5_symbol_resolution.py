"""Tests for MT5 symbol resolution fallback."""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace


if "MetaTrader5" not in sys.modules:
    mt5_stub = types.ModuleType("MetaTrader5")
    mt5_stub.ORDER_FILLING_FOK = 0
    mt5_stub.ORDER_FILLING_IOC = 1
    mt5_stub.ORDER_FILLING_RETURN = 2
    mt5_stub.ORDER_TYPE_BUY = 0
    mt5_stub.ORDER_TYPE_SELL = 1
    mt5_stub.ORDER_TYPE_BUY_LIMIT = 2
    mt5_stub.ORDER_TYPE_SELL_LIMIT = 3
    mt5_stub.ORDER_TYPE_BUY_STOP = 4
    mt5_stub.ORDER_TYPE_SELL_STOP = 5
    mt5_stub.TRADE_ACTION_DEAL = 10
    mt5_stub.TRADE_ACTION_PENDING = 11
    mt5_stub.TRADE_RETCODE_DONE = 10009
    mt5_stub.ORDER_TIME_GTC = 0
    mt5_stub.initialize = lambda *args, **kwargs: True
    mt5_stub.shutdown = lambda: None
    mt5_stub.last_error = lambda: (0, "ok")
    mt5_stub.account_info = lambda: None
    mt5_stub.terminal_info = lambda: None
    mt5_stub.symbol_info = lambda symbol: None
    mt5_stub.symbol_select = lambda symbol, visible: False
    mt5_stub.symbol_info_tick = lambda symbol: None
    mt5_stub.order_send = lambda request: None
    mt5_stub.positions_get = lambda *args, **kwargs: []
    sys.modules["MetaTrader5"] = mt5_stub

from bridge_app.mt5_connector import MT5Connector


def test_resolve_gold_symbol_falls_back_to_broker_alias(monkeypatch):
    connector = MT5Connector(login=1, password="x", server="y")
    connector._initialized = True
    connector.connected = True

    def fake_symbol_info(symbol):
        if symbol == "XAUUSDm":
            return SimpleNamespace(visible=False)
        return None

    selected = []

    def fake_symbol_select(symbol, visible):
        selected.append((symbol, visible))
        return symbol == "XAUUSDm" and visible is True

    monkeypatch.setattr("bridge_app.mt5_connector.mt5.symbol_info", fake_symbol_info)
    monkeypatch.setattr("bridge_app.mt5_connector.mt5.symbol_select", fake_symbol_select)

    assert connector._resolve_symbol("GOLD") == "XAUUSDm"
    assert ("XAUUSDm", True) in selected
