#!/usr/bin/env python3
"""
Launch orchestrator for Hermes + local provider proxies.
GUI-first entry point. Stops Hermes, ensures providers up, then starts Hermes + watchdog.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

from provider_manager import ProviderController
from hermes_provider_config import merge_providers

HERMES_PROFILES_DIR = Path.home() / ".hermes"
PROVIDERS_DIR = Path(__file__).parent
STATE_PATH = PROVIDERS_DIR / "launcher_state.json"
WATCHDOG_SCRIPT = PROVIDERS_DIR / "watchdog.py"
PYTHON = sys.executable


def _load_state() -> Dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"hermes_pid": None, "watchdog_pid": None}


def _save_state(state: Dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_hermes_process() -> int | None:
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if "python.exe" in line.lower():
                    parts = [p.strip('"') for p in line.split(",")]
                    if len(parts) >= 2 and parts[0].lower() == "python.exe":
                        return int(parts[1])
    except Exception:
        pass
    return None


def _stop_hermes() -> None:
    state = _load_state()
    pid = state.get("hermes_pid")
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(int(pid), 9)
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/IM", "python.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    state["hermes_pid"] = None
    _save_state(state)


def _start_hermes() -> None:
    # Try to launch Hermes via CLI if available, else fallback to direct module run.
    candidates = [
        ["hermes", "start"],
        ["python", "-m", "hermes", "start"],
        ["python", "-m", "hermes_agent.cli", "start"],
    ]
    for cmd in candidates:
        try:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0,
            )
            state = _load_state()
            state["hermes_pid"] = p.pid
            _save_state(state)
            time.sleep(1.0)
            return
        except Exception:
            continue


def _start_watchdog() -> None:
    try:
        p = subprocess.Popen(
            [PYTHON, str(WATCHDOG_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0,
        )
        state = _load_state()
        state["watchdog_pid"] = p.pid
        _save_state(state)
    except Exception as e:
        print(f"[launcher] Failed to start watchdog: {e}", file=sys.stderr)


def _ensure_providers(controller: ProviderController) -> None:
    for name in ("qwen", "deepseek"):
        ok, _ = controller.is_healthy(name)
        if not ok:
            started, detail = controller.start(name)
            if not started:
                print(f"[launcher] Provider {name} failed to start: {detail}", file=sys.stderr)


def run() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hermes", action="store_true", help="Start only providers")
    args = parser.parse_args()

    # 1. Stop Hermes if running
    _stop_hermes()
    time.sleep(0.5)

    # 2. Merge provider entries (atomic, with backup)
    res = merge_providers()
    if not res.get("ok"):
        print(f"[launcher] Warning: provider config merge failed: {res.get('error')}", file=sys.stderr)

    # 3. Ensure providers up
    controller = ProviderController()
    _ensure_providers(controller)
    time.sleep(0.5)

    if args.no_hermes:
        print("[launcher] Providers ready; Hermes not started (--no-hermes).")
        return

    # 4. Start Hermes
    _start_hermes()
    time.sleep(0.5)

    # 5. Start watchdog
    _start_watchdog()


if __name__ == "__main__":
    run()
