# AGENTS.md — Bunga Trader v2

> Local-first automated trading system. Telegram → LLM-validated signals → MT5.

## Stack

- **Language**: Python 3.11+
- **Framework**: FastAPI + Uvicorn (async)
- **DB**: SQLAlchemy 2.0 (SQLite)
- **Telegram**: Telethon (async)
- **Bridge**: MetaTrader5 (Windows / Wine)
- **AI**: Multi-provider (`core_backend/llm_providers/`) — Google AI Studio → Groq → OpenRouter fallback
- **Frontend**: Static HTML + vanilla JS (`web_dashboard/`)
- **Tests**: pytest + pytest-asyncio

## Module map

| Path | Purpose | Touch carefully |
|---|---|---|
| `core_backend/main.py` | FastAPI entrypoint | ✅ yes |
| `core_backend/telegram_listener.py` | Telethon client | ✅ yes |
| `core_backend/parser.py` | Signal parsing | ✅ yes |
| `core_backend/ai_engine.py` | LLM validation | ✅ yes |
| `core_backend/risk_engine.py` | Pre-trade risk checks | ⚠️ **DANGER** — money |
| `core_backend/trade_dispatcher.py` | MT5 order execution | ⚠️ **DANGER** — money |
| `core_backend/database.py` | SQLAlchemy models | ✅ yes |
| `core_backend/llm_providers/` | Provider clients | ✅ yes |
| `core_backend/market_context/` | Market data fetchers | ✅ yes |
| `core_backend/models.py` | Pydantic schemas | ✅ yes |
| `bridge_app/` | MT5 ↔ API bridge | ⚠️ **DANGER** |
| `web_dashboard/` | Static UI | ✅ yes |
| `core_backend/mobile_api/` | Mobile endpoints | ✅ yes |
| `run.py` | Unified runner (spawns all services) | ✅ yes |
| `data/` | SQLite DB + caches | 🚫 read-only |
| `logs/` | JSON logs | 🚫 read-only |

## Hard rules

### 💰 Money safety
- **NEVER** auto-approve or auto-execute trades without explicit human confirmation in dashboard
- **NEVER** modify `risk_engine.py` or `trade_dispatcher.py` without:
  1. A unit test for the change
  2. An updated `tests/` case
  3. A note in the PR description of what guard rail it touches
- If `risk_engine.validate_signal_risk()` returns `False`, the trade **must not** reach MT5 — verify this in any refactor
- Order sizing math (`lot_size`, `sl_pips`, `tp_pips`) is single-source-of-truth in `risk_engine` — don't recompute elsewhere

### 🔐 Secrets
- **NEVER** read, write, or log `.env*`, `*.pem`, `*.key`, `config.json` credentials
- All API keys go through `core_backend/config.py` (env-loaded)
- Telegram session files in `data/` — never commit

### 🧪 Testing
- Every change to `core_backend/` → add or update a test in `tests/`
- Run before commit: `python -m pytest -q`
- Don't mock the database — use a temp SQLite file

### 🎨 Code style
- PEP 8, type hints on all public functions
- Pydantic v2 models for all I/O boundaries (API in/out, LLM in/out)
- Async for any I/O (DB, HTTP, Telegram, LLM)
- `logger.py` JSON logger only — no `print()` in production paths

## Common commands

```bash
# Dev
python run.py                              # all services
uvicorn core_backend.main:app --reload     # API only

# Test  ⚠️ ALWAYS use the shim below
# The shell may inherit a Hermes-agent PYTHONPATH that makes the 3.12 venv load
# the wrong (3.11) pydantic and crash collection. Strip it first:
make test                                  # full suite (preferred)
bash scripts/test.sh -q                    # equivalent
env -u PYTHONPATH .venv/bin/python -m pytest -q   # manual

# ❌ Do NOT run bare `pytest` if PYTHONPATH contains /hermes-agent — it will fail.
# ❌ Bare `python -m pytest` also fails for the same reason.

# Lint (if available)
ruff check core_backend/                   # if ruff installed
mypy core_backend/                         # if mypy installed
```

## When the user asks for X, default to:

| Request | Default approach |
|---|---|
| "Add a new signal source" | New module in `core_backend/sources/`, register in `ai_engine` |
| "Add a new LLM provider" | Add client in `llm_providers/`, wire into fallback chain in `ai_engine` |
| "Change trade logic" | **Stop. Ask first.** This is money. |
| "New dashboard page" | Edit `web_dashboard/index.html` + static JS, no new framework |
| "New mobile endpoint" | Add to `core_backend/mobile_api/`, follow existing patterns, add test |
| "Refactor X" | Propose plan first, don't refactor money paths unsolicited |

## Conventions

- **Branch**: `feat/...`, `fix/...`, `chore/...`
- **Commits**: imperative mood, ≤72 char subject, body explains *why*
- **PRs**: link the issue, paste `pytest` output, screenshot UI changes
- **DB migrations**: manual SQL in `core_backend/database.py` (no Alembic yet) — bump schema version in a comment

## What this project is NOT

- Not a high-frequency trading system (latency-insensitive)
- Not multi-tenant (single user, local machine)
- Not production-redundant (no replicas, no k8s)

## Available skills (project-level)

- (none yet — drop a `SKILL.md` in `.opencode/skill/` if you create a project-specific workflow)

## Available skills (global, from `~/.config/opencode/skill/`)

- **superpowers** — TDD, debugging, brainstorming, code review, plans
- **anthropics/skills** — webapp-testing, pdf, frontend-design
- **microsoft/skills** — frontend-design-review, mcp-builder, skill-creator
- **trailofbits/skills** — security/vuln review
- **expo/skills** — only relevant if adding a mobile app
- **android/skills** — same
- **scientific-skills** — useful for backtesting / market analysis scripts
- **wshobson/agents** — many role-specific skills, browse with `find ~/.config/opencode/skill/wshobson-agents -name SKILL.md`
