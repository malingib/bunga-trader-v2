---
name: trading-safety
description: Guard rails for any work touching risk_engine.py, trade_dispatcher.py, or the MT5 bridge. Use BEFORE editing money-related code in Bunga Trader v2.
---

# Trading Safety

This project moves real money via MetaTrader5. Every change to the money path must follow these guard rails.

## Files in scope

- `core_backend/risk_engine.py` — `validate_signal()`, lot sizing, SL/TP math
- `core_backend/trade_dispatcher.py` — order execution
- `bridge_app/**` — MT5 ↔ FastAPI bridge (likely Windows / Wine)
- `core_backend/ai_engine.py` — only the part that auto-approves (must NOT)
- `core_backend/main.py` — only the `/approve` endpoints

## Required checklist before editing

- [ ] Read `core_backend/risk_engine.py` end-to-end first
- [ ] Confirm `validate_signal()` is the single source of truth
- [ ] If changing `lot_size`, `sl_pips`, or `tp_pips` math → update tests in `tests/test_risk_engine.py`
- [ ] If changing dispatcher → update `tests/test_trade_dispatcher.py`
- [ ] Verify `validate_signal() == False` still short-circuits the dispatcher (regression test)
- [ ] No new auto-approval paths, no env var that bypasses the dashboard

## Refusal patterns

Auto-reject any request that:

1. Adds an env var like `AUTO_APPROVE=true` or `DISABLE_RISK_CHECKS=1`
2. Tries to bypass `validate_signal()` for "trusted" sources
3. Adds direct MT5 calls outside `trade_dispatcher.py`
4. Logs account balance, positions, or PnL without scrubbing
5. Modifies the lot/SL/TP formula without a corresponding test

## What to do instead

- "Auto-approve" → keep human-in-loop, add a dashboard UI to make approval faster
- "Faster execution" → optimize the bridge latency, never skip risk checks
- "Skip validation for VIP signals" → add a stricter check, not a bypass
- "Direct MT5 from new endpoint" → route through `trade_dispatcher.py` only
