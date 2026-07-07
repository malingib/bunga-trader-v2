"""Bunga Trader - WebSocket Client"""
import asyncio
import websockets
import json
from typing import Callable, Optional
from core_backend.logger import setup_logger

logger = setup_logger("WSClient")

class BridgeWebSocketClient:
    def __init__(self, uri: str, on_trade: Callable, on_ping: Optional[Callable] = None):
        self.uri = uri
        self.on_trade = on_trade
        self.on_ping = on_ping
        self.ws = None
        self._running = False
        self._reconnect_delay = 3
        self._max_reconnect_delay = 60

    async def connect_and_listen(self):
        self._running = True
        while self._running:
            try:
                logger.info(f"Connecting to {self.uri}...")
                async with websockets.connect(self.uri, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                    self.ws = ws
                    self._reconnect_delay = 3
                    logger.info("Connected to Bunga Core")
                    async for message in ws:
                        await self._handle_message(message)
            except websockets.ConnectionClosed as e:
                logger.warning(f"WS connection closed: {e}")
            except websockets.InvalidURI:
                logger.error(f"Invalid WebSocket URI: {self.uri}")
                self._running = False
                return
            except Exception as e:
                logger.error(f"WS error: {e}")
            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            msg_type = data.get("type", "")
            if msg_type == "ping":
                if self.on_ping:
                    self.on_ping()
                return
            if msg_type == "new_trade":
                logger.info(f"Trade received: {data.get('action')} {data.get('symbol')}")
                asyncio.create_task(self._execute_trade_async(data))
            else:
                logger.debug(f"Unknown message type: {msg_type}")
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON received: {message[:100]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _execute_trade_async(self, data: dict):
        try:
            result = await asyncio.to_thread(self.on_trade, data)
            feedback = {"type": "trade_feedback", "signal_id": data.get("signal_id"), "success": result.get("success", False), "ticket": result.get("ticket"), "price": result.get("price"), "error": result.get("error")}
            await self.send_feedback(feedback)
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            await self.send_feedback({"type": "trade_feedback", "signal_id": data.get("signal_id"), "success": False, "error": str(e)})

    async def send_feedback(self, feedback: dict):
        if self.ws and self.ws.open:
            try:
                await self.ws.send(json.dumps(feedback, default=str))
                logger.debug(f"Feedback sent: {feedback}")
            except Exception as e:
                logger.error(f"Failed to send feedback: {e}")

    def stop(self):
        self._running = False
        logger.info("WS client stop requested")

async def connect_and_listen(uri: str, on_trade: Callable):
    client = BridgeWebSocketClient(uri, on_trade)
    await client.connect_and_listen()
