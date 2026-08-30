# Bunga Trader v2 — common tasks
VENV := .venv/bin
PY := env -u PYTHONPATH $(VENV)/python
PYTEST := env -u PYTHONPATH $(VENV)/python -m pytest

.PHONY: test test-research test-fast run lint lint-research

test:
	$(PYTEST) -q

test-research:
	$(PYTEST) -q tests/test_research_lab.py

test-fast:
	$(PYTEST) -q -k "$(f)"

run:
	$(VENV)/python run.py

run-research:
	$(PY) backtests/run_research.py

lint:
	@if [ -x "$(VENV)/ruff" ]; then $(VENV)/ruff check core_backend tests || true; else echo "ruff not installed — skipping"; fi
	@if [ -x "$(VENV)/mypy" ]; then $(VENV)/mypy core_backend || true; else echo "mypy not installed — skipping"; fi

lint-research:
	@if [ -x "$(VENV)/ruff" ]; then $(VENV)/ruff check backtests || true; else echo "ruff not installed — skipping"; fi
