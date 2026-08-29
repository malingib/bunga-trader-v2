# Bunga Trader Research Lab

This directory is the historical quantitative research layer of Bunga Trader.

## Mission

Discover systematic edges across forex, metals, indices, commodities and crypto by running reproducible experiments, improving strategies from evidence, combining complementary signals and rejecting fragile results.

## Research-only boundary

The lab does **not** require or invoke Telegram, AI signal validation, human approval, broker execution or live orders. Those concerns are outside this research path.

## Main modules

- `strategy_interface.py` — common strategy contract and OHLCV validation
- `strategy_library.py` — deterministic strategy building blocks
- `strategy_catalog.py` — strategy families, markets, timeframes and combination templates
- `research_lab.py` — splits, experiment registry, scoring and stability
- `research_runner.py` — parameter research and frozen OOS evaluation
- `regime.py` — trend/volatility regime features
- `crypto_research.py` — crypto-specific cross-sectional and market-regime tools
- `robustness.py` — Monte Carlo and cost sensitivity
- `portfolio_lab.py` — correlation, curves and portfolio drawdown
- `research_tournament.py` — cross-market strategy ranking

## Development rule

A better in-sample result is not automatically an improvement. Changes must survive validation and final OOS testing, with complexity and robustness considered. Every failed experiment is retained as evidence.

## Recommended workflow

`data -> validate -> baseline -> hypothesis -> train -> validation -> freeze -> final OOS -> robustness -> tournament -> portfolio research`

The lab should prefer simple, stable strategies over highly parameterized historical winners.
