"""Research-only pytest configuration.

Kept separate from the application conftest so historical research tests do
not import Telegram/DB/execution dependencies.
"""
import sys
from pathlib import Path

BACKTESTS = str(Path(__file__).resolve().parents[2] / "backtests")
if BACKTESTS not in sys.path:
    sys.path.insert(0, BACKTESTS)
