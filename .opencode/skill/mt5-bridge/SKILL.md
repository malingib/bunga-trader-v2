---
name: mt5-bridge
description: How the MT5 ↔ FastAPI bridge works in Bunga Trader v2. Use when debugging order execution, positions, or sync issues.
---

# MT5 Bridge

The bridge is the only component that talks to MetaTrader5 directly. Everything else goes through the FastAPI endpoints in `core_backend/main.py`.

## Architecture

```
FastAPI (core_backend/)  ──HTTP──▶  bridge_app/  ──MT5 SDK──▶  MT5 Terminal
       ▲                                  │                     │
       │                                  └── status polling ───┘
       └──── WebSocket push of fills/positions ────────────────┘
```

## Key endpoints

- `POST /api/orders` — submit order (validated, queued, then sent to bridge)
- `GET  /api/positions` — live positions
- `GET  /api/account` — account info (balance, equity, margin)
- `POST /api/positions/{ticket}/close` — close a position
- `WS   /ws` — real-time updates (fills, position changes)

## Common bugs

- **MT5 not running on Windows / Wine** → bridge silently fails. Check `data/bridge.log`
- **Symbol suffix mismatch** (e.g. `EURUSD` vs `EURUSD.m`) → orders rejected. Configured in `bridge_app/config.py`
- **Order send timeout** → MT5 terminal may be busy. Bridge retries 3× with backoff
- **Stale positions** → WS drops. Restart bridge (`python bridge_app/main.py`)

## Testing the bridge without real MT5

- Use the `mock_mt5` fixture in `tests/conftest.py` — stubs out the SDK
- For integration, run a `wine mt5terminal` container (see `tests/integration/`)
- **Never** test against a real account unless you mean it
