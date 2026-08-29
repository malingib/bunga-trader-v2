# Bunga Trader Research Protocol

## Purpose

Bunga Trader is currently a historical-data research laboratory. The research path must not depend on Telegram, AI validation, human approval, broker execution, or live orders.

## Research cycle

1. Select a symbol, timeframe and strategy hypothesis.
2. Validate and chronologically sort historical data.
3. Split data into TRAIN / VALIDATION / FINAL OOS.
4. Establish a reproducible baseline.
5. Diagnose failures and formulate a specific improvement hypothesis.
6. Test the change on TRAIN.
7. Select variants using VALIDATION only.
8. Freeze the candidate before opening FINAL OOS.
9. Run FINAL OOS exactly once for the frozen candidate.
10. Run robustness tests: parameter sensitivity, costs, regimes, years and symbols where data permits.
11. Record PASS, FAIL or KILLED with the reason.
12. Keep every experiment and failed variant; never silently overwrite research history.

## No-lookahead rules

- Indicators use only current and prior observations.
- Signals generated on bar N cannot use bar N+1 information.
- Entries and exits must have an explicit event sequence.
- Final OOS data cannot influence parameter selection, feature selection or strategy changes.
- If OHLC data cannot determine whether stop or target was hit first, use a conservative ambiguity rule or finer-grained data; never choose the favorable outcome.

## Cost model

Every strategy must be tested with explicit spread, slippage and commissions where applicable. Crypto experiments should additionally support funding, maker/taker fees and other known carrying costs when the historical dataset contains them.

## Improvement rule

A modification is accepted only when it improves validation without unacceptable deterioration in complexity, drawdown, trade count or robustness. A higher TRAIN result alone is not evidence of improvement.

## Combination rule

Strategies may be combined freely, including trend + momentum, breakout + volatility, price action + trend, regime switching and cross-asset signals. Combinations receive a complexity penalty and must beat their simpler parents on validation/OOS robustness.

## Markets

Research may include forex, metals, indices, commodities and crypto. Crypto is a first-class universe and should support both single-asset and cross-sectional research where historical data is available.

## Research outputs

Each experiment should record: strategy/version, parents, symbol, timeframe, data period, parameters, hypothesis, trade count, return, expectancy, profit factor, Sharpe, max drawdown, costs, OOS metrics, stability metrics, complexity and final status.
