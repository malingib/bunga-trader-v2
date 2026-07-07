import os
import json
from typing import Set

_EXEC_SIG_PATH = os.path.join(os.path.dirname(__file__), "executed_signals.json")


def load_executed_signals() -> Set[int]:
    if not os.path.exists(_EXEC_SIG_PATH):
        return set()
    try:
        with open(_EXEC_SIG_PATH, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_executed_signals(sig_set: Set[int]):
    try:
        with open(_EXEC_SIG_PATH, "w") as f:
            json.dump(list(sig_set), f)
    except Exception:
        pass
