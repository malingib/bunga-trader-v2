---
name: telegram-listener
description: How the Telethon client listens to Telegram channels for trading signals. Use when adding channels, debugging parsing, or extending signal formats.
---

# Telegram Listener

`core_backend/telegram_listener.py` — long-running async client that joins channels and routes new messages to `parser.py`.

## Setup

1. Get `api_id` + `api_hash` from https://my.telegram.org
2. Add to `.env`:
   ```
   TELEGRAM_API_ID=...
   TELEGRAM_API_HASH=...
   TELEGRAM_SESSION=path/to/data/telethon.session
   ```
3. First run is interactive — enter phone + code, then session is persisted

## Channel allowlist

Configured in `data/channels.json`:

```json
{
  "channels": [
    { "id": -1001234567890, "name": "Signals A", "trust": "high" },
    { "id": -1009876543210, "name": "Signals B", "trust": "low" }
  ]
}
```

`trust: low` channels → AI validation is mandatory. `trust: high` → AI is optional but logged.

## Supported signal formats

- Plain text: `BUY EURUSD @ 1.0850 SL 1.0830 TP 1.0900`
- Multi-TP: `BUY EURUSD ... TP1 1.0880 TP2 1.0900 TP3 1.0930`
- Pending orders: `BUY_LIMIT EURUSD @ 1.0800 ...`
- Image OCR — Tesseract via `core_backend/market_context/ocr.py`

## Adding a new signal format

1. Add a regex + parser class to `core_backend/parser.py`
2. Add a test case in `tests/test_parser.py` (positive + negative)
3. Update the `Signal` Pydantic model in `core_backend/models.py` if new fields
4. Never modify `risk_engine.py` from a parser change

## Failure modes

- **Flood wait** → Telethon raises `FloodWaitError(seconds=N)`. Listener backs off automatically
- **Banned from channel** → logged to `data/bans.log`, listener continues with other channels
- **Session expired** → restart is interactive again. Use a dedicated user account, not your personal
