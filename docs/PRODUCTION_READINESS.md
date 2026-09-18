# Bunga Trader v2 — Production Readiness

## Six completion tracks

1. Safety and correctness: every signal passes risk validation; approval is an atomic state transition before broker dispatch; invalid actions and price geometry are rejected.
2. Research validation: backtests must model spread, commission, slippage and execution assumptions, and report untouched out-of-sample results separately.
3. Application completion: dashboard, API, broker status, order lifecycle and operational health expose enough state to diagnose failures without duplicate orders.
4. Tests: core backend changes require automated tests using temporary SQLite; CI runs the suite on pull requests.
5. Deployment: secrets stay outside source control; production configuration uses a deliberate broker environment; live execution remains off until the research gate is signed off.
6. Release/review: money-sensitive changes go through a pull request and review; master receives changes only after CI and the execution/research gates pass.

## Live-trading gate

- [ ] CI is green on the exact release commit.
- [ ] Risk tests cover invalid side/SL/TP geometry and non-finite inputs.
- [ ] Concurrent approval cannot claim the same signal twice.
- [ ] Broker failures leave an auditable state and do not silently retry.
- [ ] Broker reconciliation is tested for timeout, disconnect and partial/unknown execution.
- [ ] API mutation endpoints are protected by authentication at the application or deployment boundary.
- [ ] Logs and API responses contain no credentials or tokens.
- [ ] Backtests include transaction costs and execution assumptions.
- [ ] Final out-of-sample data was not used to tune parameters.
- [ ] Fixed-risk and capped-risk results are reported separately from fixed-unit research.
- [ ] Demo/forward testing completes without unexplained order discrepancies.

## Research rule

The result in backtests/final_validation.py is a research result using fixed one-unit sizing. It does not establish deployable profitability. Its embedded target conclusion must not be treated as a production approval signal.

## Rollback

If an execution anomaly occurs, disconnect the broker, disable strategy polling, preserve logs and database state, reconcile broker-side orders against trade_logs, and only then investigate or restart. Do not repeatedly retry an unknown order outcome.
