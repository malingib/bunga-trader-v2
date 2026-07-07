---
name: ai-signal-validator
description: How the multi-LLM signal validation chain works in Bunga Trader v2. Use when adding signals, changing scoring, or debugging provider fallbacks.
---

# AI Signal Validator

Located in `core_backend/ai_engine.py`. Hybrid rule-based + LLM scoring (0.0–1.0).

## Fallback chain (in order)

1. **Google AI Studio** — 1,500 req/day free
2. **Groq** — 1,000 req/day free
3. **OpenRouter** — 200 req/day free

When a provider returns 429 / 5xx / network error, the chain advances. A signal needs ≥ 0.7 score from at least one provider to be forwarded for human approval.

## Adding a new provider

1. Create `core_backend/llm_providers/<name>.py` implementing the `LLMProvider` protocol from `core_backend/llm_providers/base.py`
2. Add to the `FALLBACK_CHAIN` list in `ai_engine.py`
3. Add rate-limit env vars to `.env.example`
4. Update `tests/test_ai_engine.py` with a mock provider

## Output schema (Pydantic)

```python
class ValidationResult(BaseModel):
    score: float            # 0.0 to 1.0
    confidence: float       # 0.0 to 1.0
    reasoning: str          # ≤200 chars, shown in dashboard
    provider: str           # which LLM scored it
    model: str              # exact model name
    raw: dict | None        # full provider response for debugging
```

## Caching

Results are cached in `data/llm_cache.sqlite` keyed by `(signal_hash, provider, model)`. Cache TTL: 24h. To bust cache: `rm data/llm_cache.sqlite`.

## Cost guard rails

- Each call logs token usage to `logs/llm_usage.jsonl`
- Dashboard `Settings → AI` shows running total per provider
- Hard kill switch: `DISABLE_AI=1` env var forces rule-based scoring only
