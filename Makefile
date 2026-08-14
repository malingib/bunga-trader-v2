# Bunga Trader v2 — common tasks
# The venv is 3.12; the shell may leak a 3.11 Hermes-agent PYTHONPATH that
# breaks imports. Every python/pytest target strips PYTHONPATH first.

VENV := .venv/bin
PY := env -u PYTHONPATH $(VENV)/python
PYTEST := env -u PYTHONPATH $(VENV)/python -m pytest

.PHONY: test test-fast run lint

test:  ## Run the full test suite (strips leaked PYTHONPATH)
	$(PYTEST) -q

test-fast:  ## Run only tests whose name matches a pattern: make test-fast f=risk
	$(PYTEST) -q -k "$(f)"

run:  ## Start all services via the unified runner
	$(VENV)/python run.py

lint:  ## Run ruff + mypy if installed (best-effort; reports but does not fail the build)
	@if [ -x "$(VENV)/ruff" ]; then echo ">> ruff"; $(VENV)/ruff check core_backend tests || true; else echo "ruff not installed — skipping"; fi
	@if [ -x "$(VENV)/mypy" ]; then echo ">> mypy"; $(VENV)/mypy core_backend || true; else echo "mypy not installed — skipping"; fi
	@echo "lint done"
