#!/usr/bin/env bash
# Run the test suite with the project venv, stripping any leaked PYTHONPATH.
#
# The shell environment may carry Hermes-agent venv site-packages on PYTHONPATH
# (inherited from the desktop app). That makes this project's 3.12 venv load the
# WRONG (3.11) pydantic and crash collection with:
#   ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
# Stripping PYTHONPATH lets the venv resolve its own packages correctly.
set -euo pipefail
cd "$(dirname "$0")/.."
exec env -u PYTHONPATH .venv/bin/python -m pytest "$@"
