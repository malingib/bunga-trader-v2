# Bunga Trader v2

Local-first automated trading system with free LLM-powered signal validation, web dashboard, and mobile API.

## What's New in v2

- **Free LLM Fallback Stack** - Google AI Studio (1,500/day) → Groq (1,000/day) → OpenRouter (200/day)
- **AI Signal Validation** - Hybrid rule-based + LLM scoring (0.0-1.0)
- **Web Dashboard** - Dark theme, real-time updates, auto-approve toggle
- **Mobile API** - `/mobile/*` endpoints optimized for Android/iOS apps
- **Multi-TP Support** - Parse TP1, TP2, TP3 from signals
- **Pending Orders** - BUY_LIMIT, SELL_LIMIT, BUY_STOP, SELL_STOP

## Architecture

```
Telegram Channels → Listener → SQLite → Parser → AI Validation → Approval → WebSocket → Bridge → MT5
                                    ↓
                              Web Dashboard (http://localhost:8000)
                                    ↓
                              Mobile API (/mobile/*)
```

## Quick Start

### 1. Prerequisites
- Python 3.10+
- MetaTrader 5 terminal
- Telegram API credentials (https://my.telegram.org)
- At least one free LLM API key

### 2. Installation

```bash
cd bunga-trader-v2
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. Get Free LLM API Keys

| Provider | URL | Free Tier |
|----------|-----|-----------|
| Google AI Studio | https://aistudio.google.com/app/apikey | 1,500 req/day |
| Groq | https://console.groq.com/keys | 30 RPM / 1,000/day |
| OpenRouter | https://openrouter.ai/keys | 20 RPM / 200/day |

### 5. Run

**Option A: Unified Runner (all 3 components)**
```bash
python run.py
```

**Option B: Separate Terminals**
```bash
# Terminal 1 - API + Dashboard
uvicorn core_backend.main:app --host 127.0.0.1 --port 8000

# Terminal 2 - Telegram Listener
python -m core_backend.telegram_listener

# Terminal 3 - MT5 Bridge
python -m bridge_app.executor
```

### 6. Access Dashboard

Open http://127.0.0.1:8000 in your browser.

## API Endpoints

All endpoints are **local, single-user only** — no server-side auth except the
TradingView webhook (which requires `WEBHOOK_SECRET`) and the optional dashboard
token (set `DASHBOARD_TOKEN` to require `X-Dashboard-Token` on mutating requests).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web Dashboard |
| `/health` | GET | Health check |
| `/status` | GET | System status (incl. `trading_halted` circuit state) |
| `/signals/pending` | GET | List pending signals |
| `/signals/{id}/approve` | POST | Approve & dispatch (manual human gate) |
| `/signals/{id}/reject` | POST | Reject signal |
| `/signals/approve-all` | POST | Approve all pending |
| `/trades` | GET | Trade history |
| `/trades/open-positions` | GET | Active broker open positions (read-only) |
| `/trades/reconcile` | GET | Run position reconciliation pass (finalizes closed trades' P&L) |
| `/broker/status` | GET | Broker connection state |
| `/broker/connect` | POST | Reconnect broker + re-dispatch orphaned APPROVED signals |
| `/broker/switch` | POST | Switch active broker |
| `/broker/reset-circuit` | POST | Reset dispatch circuit breaker ("Resume trading") |
| `/strategy/status` | GET | Strategy engine config |
| `/strategy/poll` | POST | Force one strategy evaluation cycle |
| `/strategy/last-signals` | GET | Recent strategy-generated signals (live DB) |
| `/strategy/history` | GET | Aggregated trade history + equity curve |
| `/strategy/toggle` | POST | Pause/resume strategy polling |
| `/strategy/config` | POST | Update strategy config at runtime |
| `/performance/per-symbol` | GET | Per-symbol P&L |
| `/market/live` | GET | Latest prices (cached 10s) |
| `/logs/latest` | GET | Tail of latest log file |
| `/webhook/tradingview` | POST | TradingView Pine Script alert (requires `WEBHOOK_SECRET`) |

## Data Model

The single source of truth for every signal that reaches the pipeline is the
`parsed_signals` table (`ParsedSignal` ORM model). Notable columns:

- `status` — `PENDING` → `APPROVED` → `EXECUTED` (or `REJECTED`). Signals are
  written as `PENDING` and **only** dispatched after a human approves them in
  the dashboard; nothing auto-executes.
- `strategy_generated_at` — ISO-8601 timestamp (`VARCHAR(32)`) set when a signal
  originates from the strategy engine (as opposed to a manually pasted or
  TradingView signal). It is how `/strategy/last-signals` distinguishes
  strategy-generated signals from the rest. The column is added by a manual, idempotent
  migration in `core_backend/database.py:apply_migrations()` (no Alembic), so
  existing databases pick it up on next startup.

> **P&L note:** a `TradeLog` row is written at *execution* time with a nominal
> P&L (entry ≈ fill). Realized P&L is finalized two ways: (1) the hourly
> position-reconciliation loop (`GET /trades/reconcile`, also runs in the cleanup
> loop) detects when a broker position has closed and stamps `closed_at` + an
> approximate realized P&L; or (2) a human posts the exact figure via
> `POST /trades/{id}/feedback`, which takes precedence and is never overwritten
> by the reconciler.

## Signal Formats Supported

```
BUY EURUSD 1.2500 SL 1.2450 TP 1.2600
SELL GBPUSD @ 1.2500 SL: 1.2450 TP: 1.2600
BUY EURUSD Entry: 1.2500 SL: 1.2450 TP: 1.2600
BUY USDJPY SL 140.00 TP 139.50
BUY EURUSD 1.2500 SL 1.2450 TP1 1.2550 TP2 1.2600 TP3 1.2650
BUY LIMIT EURUSD 1.2400 SL 1.2350 TP 1.2500
```

## Android App

There is no mobile client yet. The `/mobile/*` routes referenced below are
**not implemented** (the `mobile_api` module was never added) — do not rely on
them. The web dashboard is the only client. When a mobile client is built, the
endpoints below describe the intended surface.

```bash
# Get dashboard data (single call)
GET /mobile/dashboard

# Get signals
GET /mobile/signals?status=pending&limit=50

# Get notification data
GET /mobile/notifications

# Approve/reject
POST /mobile/signals/{id}/approve
POST /mobile/signals/{id}/reject
```

Approval is a manual human action in the local web dashboard. The API has no
server-side authentication: it is intended for **local, single-user use only**
and must never be exposed on a public network. The TradingView webhook requires
`WEBHOOK_SECRET` to be set, and rejects unsigned requests when it is not.

## LLM Provider Stack

The system automatically falls through providers when rate limits are hit:

1. **Google AI Studio** (Gemini 2.0 Flash) - Primary, 1,500/day
2. **Groq** (Llama 3.3 70B) - Fallback 1, 1,000/day
3. **OpenRouter** (Llama 3.3 70B) - Fallback 2, 200/day

Borderline signals (score 0.2-0.85) get LLM analysis. Clear signals skip LLM to save quota.

## Risk Management

- Lot sizing based on account balance, risk %, and SL distance
- Daily P&L-based gating: stop trading on loss limit or profit target
- Consecutive loss circuit breaker
- Max lot cap
- Manual approval required for every trade from dashboard/mobile
- Duplicate prevention and position tracking
- Note: day-stopping gating is P&L-based, not trade-count-based, so API rate limits should be managed via polling interval rather than daily trade caps

## License

MIT
