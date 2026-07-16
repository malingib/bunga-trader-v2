"""Broker registry — singleton broker_manager is imported by the rest of the app."""
from __future__ import annotations

import logging
from typing import Dict, Optional, Type

from .base import BrokerBase

# Register default adapters (lazy imports to avoid circular deps)
_registry: Dict[str, Type[BrokerBase]] = {}
_active_broker: Optional[BrokerBase] = None

logger = logging.getLogger("BrokerRegistry")


def _ensure_registered() -> None:
    """Import and register built-in adapters once."""
    if _registry:
        return
    from .oanda import OandaBroker
    from .deriv import DerivBroker
    _registry["oanda"] = OandaBroker
    _registry["deriv"] = DerivBroker
    logger.info("Brokers registered: oanda, deriv")


def register(name: str, klass: Type[BrokerBase]) -> None:
    """Register a broker adapter class under a short name."""
    _registry[name] = klass
    logger.info("Broker registered: %s -> %s", name, klass.__name__)


def list_available() -> Dict[str, str]:
    """Return {name: docstring} for every registered broker."""
    _ensure_registered()
    return {name: (klass.__doc__ or "").strip().split("\n")[0] for name, klass in _registry.items()}


def get_active() -> Optional[BrokerBase]:
    """Return the currently active broker instance, or None."""
    _ensure_registered()
    return _active_broker


async def switch_broker(name: Optional[str]) -> Optional[BrokerBase]:
    """Disconnect the current broker and connect *name* (or None to disconnect only)."""
    global _active_broker
    _ensure_registered()

    # disconnect current
    if _active_broker is not None:
        try:
            await _active_broker.disconnect()
        except Exception:
            logger.exception("Error disconnecting %s", _active_broker.name)
        logger.info("Disconnected broker: %s", _active_broker.name)
        _active_broker = None

    if name is None:
        return None

    klass = _registry.get(name)
    if klass is None:
        raise ValueError(f"Unknown broker '{name}'. Available: {list(_registry.keys())}")

    instance: BrokerBase = klass()
    connected = await instance.connect()
    if connected:
        _active_broker = instance
        logger.info("Connected broker: %s", name)
    else:
        logger.error("Failed to connect broker: %s", name)
    return _active_broker
