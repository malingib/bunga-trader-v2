"""Bunga Trader - Bridge Executor"""
import os
import asyncio
from typing import Dict, Set, Optional
from dotenv import load_dotenv
from .mt5_connector import MT5Connector
from .ws_client import BridgeWebSocketClient
from core_backend.logger import setup_logger
from core_backend.config import CONFIG
from .executor_persistence import load_executed_signals, save_executed_signals

logger = setup_logger("BridgeExecutor")

load_dotenv()

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
WS_URI = f"ws://{CONFIG.ws_host}:{CONFIG.ws_port}/ws"

class TradeExecutor:
    def __init__(self):
        self.mt5 = MT5Connector(MT5_LOGIN, MT5_PASSWORD, MT5_SERVER)
        self.ws_client = BridgeWebSocketClient(WS_URI, self.execute_trade)
        self.executed_signals: Set[int] = load_executed_signals()
        self._running = False

    def execute_trade(self, data: dict) -> dict:
        signal_id = data.get("signal_id")
        if signal_id in self.executed_signals:
            logger.warning(f"Signal {signal_id} already executed, skipping")
            return {"success": False, "error": "Duplicate signal"}
        required = ["action", "symbol", "lot", "sl", "tp"]
        missing = [f for f in required if f not in data or data[f] is None]
        if missing:
            return {"success": False, "error": f"Missing fields: {missing}"}
        if not self.mt5.is_connected():
            logger.error("MT5 not connected, attempting reconnect...")
            if not self.mt5.initialize():
                return {"success": False, "error": "MT5 connection failed"}
        symbol = data["symbol"]
        existing_positions = self.mt5.get_positions(symbol=symbol)
        if existing_positions:
            logger.warning(f"Existing position on {symbol}, skipping to avoid double-trade")
            return {"success": False, "error": f"Position already open on {symbol}"}
        result = self.mt5.place_order(symbol=symbol, action=data["action"], lot=data["lot"], sl=data.get("sl"), tp=data.get("tp"), entry=data.get("entry"), comment=f"BungaTrader #{signal_id}")
        if result["success"]:
            self.executed_signals.add(signal_id)
            save_executed_signals(self.executed_signals)
            logger.info(f"Trade executed: {data['action']} {symbol} {data['lot']} lots | Ticket: {result['ticket']}")
        else:
            logger.error(f"Trade failed: {result['error']}")
        return result

    async def run(self):
        self._running = True
        logger.info("Initializing MT5 connection...")
        if not self.mt5.initialize():
            logger.error("Failed to initialize MT5. Exiting.")
            return
        balance_task = asyncio.create_task(self._balance_sync_loop())
        try:
            await self.ws_client.connect_and_listen()
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        except Exception as e:
            logger.error(f"Executor error: {e}")
        finally:
            self._running = False
            balance_task.cancel()
            try:
                await balance_task
            except asyncio.CancelledError:
                pass
            self.mt5.shutdown()
            logger.info("Bridge executor stopped")

    async def _balance_sync_loop(self):
        while self._running:
            try:
                if self.mt5.is_connected():
                    balance = self.mt5.get_balance()
                    logger.debug(f"Account balance: {balance:.2f}")
            except Exception as e:
                logger.error(f"Balance sync error: {e}")
            await asyncio.sleep(30)

    def stop(self):
        self._running = False
        self.ws_client.stop()
        logger.info("Stop signal sent")

def patch_main():
    pass

async def main():
    executor = TradeExecutor()
    try:
        await executor.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        executor.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
