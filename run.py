#!/usr/bin/env python3
"""Bunga Trader - Unified Runner"""
import subprocess
import sys
import signal
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

processes = []

def signal_handler(sig, frame):
    print("\nShutting down all components...")
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=5)
        except:
            p.kill()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def start_component(name: str, cmd: str, *, env: dict | None = None):
    print(f"Starting {name}...")
    p = subprocess.Popen(
        cmd,
        shell=True,
        cwd=Path(__file__).parent,
        env=env,
    )
    processes.append(p)
    return p


def main():
    print("="*60)
    print("BUNGA TRADER v2 - Unified Runner")
    print("="*60)
    print()
    venv_python = Path(__file__).parent / ".venv" / "bin" / "python"
    venv_uvicorn = Path(__file__).parent / ".venv" / "bin" / "uvicorn"

    env = os.environ.copy()

    if venv_python.exists():
        env["VIRTUAL_ENV"] = str((Path(__file__).parent / ".venv").resolve())

    # Use the venv executables to avoid mismatched environments.
    api_cmd = f"{venv_uvicorn} core_backend.main:app --host 127.0.0.1 --port 8000" if venv_uvicorn.exists() else "uvicorn core_backend.main:app --host 127.0.0.1 --port 8000"

    start_component("API Server", api_cmd, env=env)
    import time
    time.sleep(3)

    tel_cmd = f"{venv_python} -m core_backend.telegram_listener" if venv_python.exists() else "python3 -m core_backend.telegram_listener"
    mt5_cmd = f"{venv_python} -m bridge_app.executor" if venv_python.exists() else "python3 -m bridge_app.executor"

    start_component("Telegram Listener", tel_cmd, env=env)
    start_component("MT5 Bridge", mt5_cmd, env=env)

    print()
    print("All components started!")
    print("Dashboard: http://127.0.0.1:8000")
    print("API Docs: http://127.0.0.1:8000/docs")
    print()
    print("Press Ctrl+C to stop all components")
    print("="*60)
    try:
        for p in processes:
            p.wait()
    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == "__main__":
    main()
