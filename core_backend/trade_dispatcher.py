"""Bunga Trader - WebSocket Trade Dispatcher"""
import asyncio
import json
from typing import List, Dict
from fastapi import WebSocket
from .logger import setup_logger

logger = setup_logger("TradeDispatcher")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"Bridge connected. Total: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"Bridge disconnected. Total: {len(self.active_connections)}")

    async def broadcast_trade(self, trade_data: dict) -> Dict:
        message = json.dumps(trade_data, default=str)
        sent = 0
        failed = 0
        disconnected = []
        async with self._lock:
            connections = list(self.active_connections)
        for connection in connections:
            try:
                await connection.send_text(message)
                sent += 1
            except Exception as e:
                logger.error(f"Failed to send to bridge: {e}")
                failed += 1
                disconnected.append(connection)
        for conn in disconnected:
            try:
                await self.disconnect(conn)
            except:
                pass
        result = {"sent_count": sent, "failed_count": failed, "total": len(connections)}
        logger.info(f"Trade broadcast: {sent} sent, {failed} failed")
        return result

    async def get_status(self) -> Dict:
        return {
            "connected_bridges": len(self.active_connections),
            "status": "healthy" if self.active_connections else "no_bridges",
        }

manager = ConnectionManager()
