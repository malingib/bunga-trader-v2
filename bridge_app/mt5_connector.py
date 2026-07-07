"""Bunga Trader - MetaTrader 5 Connector"""
import time
from typing import Optional, Dict, List
import MetaTrader5 as mt5
from core_backend.logger import setup_logger
from core_backend.symbols import mt5_candidates, normalize_signal_symbol

logger = setup_logger("MT5Connector")

class MT5Connector:
    def __init__(self, login: int, password: str, server: str):
        self.login = login
        self.password = password
        self.server = server
        self.connected = False
        self._initialized = False

    def initialize(self) -> bool:
        if self._initialized:
            return self.connected
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"MT5 init attempt {attempt}/{max_retries}...")
                mt5.shutdown()
                time.sleep(1)
                init_result = mt5.initialize(login=self.login, password=self.password, server=self.server, timeout=60000)
                if not init_result:
                    logger.error(f"MT5 init failed: {mt5.last_error()}")
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    return False
                account_info = mt5.account_info()
                if account_info is None:
                    logger.error(f"MT5 no account info: {mt5.last_error()}")
                    mt5.shutdown()
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    return False
                self.connected = True
                self._initialized = True
                logger.info(f"MT5 connected | Account: {account_info.login} | Balance: {account_info.balance:.2f} {account_info.currency}")
                return True
            except Exception as e:
                logger.error(f"MT5 init exception: {e}")
                if attempt < max_retries:
                    time.sleep(2)
                else:
                    return False
        return False

    def shutdown(self):
        if self._initialized:
            try:
                mt5.shutdown()
                logger.info("MT5 connection closed")
            except Exception as e:
                logger.error(f"Error shutting down MT5: {e}")
            finally:
                self.connected = False
                self._initialized = False

    def is_connected(self) -> bool:
        if not self._initialized:
            return False
        try:
            info = mt5.terminal_info()
            return info is not None and info.connected
        except:
            return False

    def get_account_info(self) -> Optional[Dict]:
        if not self.is_connected():
            return None
        try:
            info = mt5.account_info()
            if info:
                return {"login": info.login, "balance": info.balance, "equity": info.equity, "margin": info.margin, "free_margin": info.margin_free, "currency": info.currency, "leverage": info.leverage}
        except Exception as e:
            logger.error(f"Error getting account info: {e}")
        return None

    def get_balance(self) -> float:
        info = self.get_account_info()
        return info["balance"] if info else 0.0

    def _resolve_symbol(self, symbol: str) -> Optional[str]:
        """Find the broker symbol for a normalized trade symbol."""
        candidates = mt5_candidates(symbol)
        for candidate in candidates:
            sym_info = mt5.symbol_info(candidate)
            if sym_info is not None:
                if not sym_info.visible:
                    if not mt5.symbol_select(candidate, True):
                        continue
                return candidate
        return None

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        if not self.is_connected():
            return None
        try:
            resolved_symbol = self._resolve_symbol(symbol)
            if resolved_symbol is None:
                logger.error(f"Symbol {symbol} not found")
                return None
            sym_info = mt5.symbol_info(resolved_symbol)
            if sym_info is None:
                logger.error(f"Symbol {resolved_symbol} not found after resolution")
                return None
            if not sym_info.visible:
                if not mt5.symbol_select(resolved_symbol, True):
                    logger.error(f"Failed to select {resolved_symbol}")
                    return None
                logger.info(f"Added {resolved_symbol} to Market Watch")
            filling_modes = self._get_valid_filling_modes(sym_info)
            return {"name": sym_info.name, "symbol": resolved_symbol, "bid": sym_info.bid, "ask": sym_info.ask, "point": sym_info.point, "digits": sym_info.digits, "trade_tick_size": sym_info.trade_tick_size, "trade_contract_size": sym_info.trade_contract_size, "volume_min": sym_info.volume_min, "volume_max": sym_info.volume_max, "volume_step": sym_info.volume_step, "filling_modes": filling_modes, "trade_stops_level": sym_info.trade_stops_level}
        except Exception as e:
            logger.error(f"Error getting symbol info for {symbol}: {e}")
            return None

    def _get_valid_filling_modes(self, sym_info) -> List[int]:
        modes = []
        if sym_info.filling_mode & 1:
            modes.append(mt5.ORDER_FILLING_FOK)
        if sym_info.filling_mode & 2:
            modes.append(mt5.ORDER_FILLING_IOC)
        if sym_info.filling_mode & 4:
            modes.append(mt5.ORDER_FILLING_RETURN)
        if not modes:
            modes = [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]
        return modes

    def _get_order_type(self, action: str) -> int:
        action_upper = action.upper()
        if action_upper == "BUY":
            return mt5.ORDER_TYPE_BUY
        elif action_upper == "SELL":
            return mt5.ORDER_TYPE_SELL
        elif action_upper == "BUY_LIMIT":
            return mt5.ORDER_TYPE_BUY_LIMIT
        elif action_upper == "SELL_LIMIT":
            return mt5.ORDER_TYPE_SELL_LIMIT
        elif action_upper == "BUY_STOP":
            return mt5.ORDER_TYPE_BUY_STOP
        elif action_upper == "SELL_STOP":
            return mt5.ORDER_TYPE_SELL_STOP
        else:
            raise ValueError(f"Unknown action: {action}")

    def _get_trade_action(self, action: str) -> int:
        action_upper = action.upper()
        if action_upper in ("BUY", "SELL"):
            return mt5.TRADE_ACTION_DEAL
        else:
            return mt5.TRADE_ACTION_PENDING

    def place_order(self, symbol: str, action: str, lot: float, sl: Optional[float], tp: Optional[float], entry: Optional[float] = None, comment: str = "BungaTrader") -> Dict:
        if not self.is_connected():
            return {"success": False, "error": "MT5 not connected"}
        sym_info = self.get_symbol_info(symbol)
        if not sym_info:
            return {"success": False, "error": f"Symbol {symbol} not available"}
        resolved_symbol = sym_info["symbol"]
        lot = round(lot, 2)
        if lot < sym_info["volume_min"]:
            return {"success": False, "error": f"Lot {lot} below minimum {sym_info['volume_min']}"}
        if lot > sym_info["volume_max"]:
            return {"success": False, "error": f"Lot {lot} above maximum {sym_info['volume_max']}"}
        step = sym_info["volume_step"]
        lot = round(lot / step) * step
        lot = round(lot, 2)
        try:
            order_type = self._get_order_type(action)
            trade_action = self._get_trade_action(action)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        tick = mt5.symbol_info_tick(resolved_symbol)
        if tick is None:
            return {"success": False, "error": "Could not get current price"}
        if trade_action == mt5.TRADE_ACTION_DEAL:
            price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
        else:
            if entry is None:
                return {"success": False, "error": "Entry price required for pending orders"}
            price = entry
        request = {"action": trade_action, "symbol": resolved_symbol, "volume": float(lot), "type": order_type, "price": float(price), "deviation": 20, "magic": 123456, "comment": comment, "type_time": mt5.ORDER_TIME_GTC}
        if sl is not None:
            request["sl"] = float(sl)
        if tp is not None:
            request["tp"] = float(tp)
        filling_modes = sym_info["filling_modes"]
        last_error = None
        for filling_mode in filling_modes:
            request["type_filling"] = filling_mode
            try:
                logger.info(f"Sending order: {action} {resolved_symbol} {lot} lots @ {price} SL:{sl} TP:{tp} [filling={filling_mode}]")
                result = mt5.order_send(request)
                if result is None:
                    last_error = f"order_send returned None: {mt5.last_error()}"
                    continue
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    logger.info(f"Order executed | Ticket: {result.order} | Price: {result.price} | Volume: {result.volume}")
                    return {"success": True, "ticket": result.order, "price": result.price, "volume": result.volume, "retcode": result.retcode}
                else:
                    last_error = f"Retcode {result.retcode}: {result.comment}"
                    logger.warning(f"Order failed with {filling_mode}: {last_error}")
                    if result.retcode in (10004, 10027):
                        tick = mt5.symbol_info_tick(resolved_symbol)
                        if tick:
                            request["price"] = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid
                            logger.info("Retrying with updated price...")
                            result2 = mt5.order_send(request)
                            if result2 and result2.retcode == mt5.TRADE_RETCODE_DONE:
                                return {"success": True, "ticket": result2.order, "price": result2.price, "volume": result2.volume, "retcode": result2.retcode}
            except Exception as e:
                last_error = str(e)
                logger.error(f"Exception placing order: {e}")
        return {"success": False, "error": f"All filling modes failed. Last: {last_error}"}

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        if not self.is_connected():
            return []
        try:
            if symbol:
                resolved_symbol = self._resolve_symbol(symbol) or normalize_signal_symbol(symbol)
                positions = mt5.positions_get(symbol=resolved_symbol)
            else:
                positions = mt5.positions_get()
            if positions is None:
                return []
            return [{"ticket": pos.ticket, "symbol": pos.symbol, "type": "BUY" if pos.type == 0 else "SELL", "volume": pos.volume, "open_price": pos.price_open, "current_price": pos.price_current, "sl": pos.sl, "tp": pos.tp, "profit": pos.profit, "magic": pos.magic} for pos in positions]
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []

    def close_position(self, ticket: int) -> Dict:
        if not self.is_connected():
            return {"success": False, "error": "MT5 not connected"}
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return {"success": False, "error": f"Position {ticket} not found"}
            pos = position[0]
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                return {"success": False, "error": "Could not get price"}
            price = tick.bid if pos.type == 0 else tick.ask
            request = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume, "type": mt5.ORDER_TYPE_SELL if pos.type == 0 else mt5.ORDER_TYPE_BUY, "position": ticket, "price": price, "deviation": 20, "magic": pos.magic, "comment": "BungaTrader close"}
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                return {"success": True, "ticket": result.order}
            else:
                return {"success": False, "error": f"Close failed: {result.comment if result else 'Unknown'}"}
        except Exception as e:
            return {"success": False, "error": str(e)}
