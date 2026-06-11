#!/usr/bin/env python3
"""Persistent watchdog for local Hermes provider proxies."""
from __future__ import annotations

import json
import time
from pathlib import Path
from provider_manager import ProviderController

WATCHDOG_STATE = Path(__file__).with_suffix(".watchdog.json")
POLL_INTERVAL = 600  # 10 minutes


def _load_state() -> Dict:
    if WATCHDOG_STATE.exists():
        try:
            return json.loads(WATCHDOG_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"running": False}


def _save_state(state: Dict) -> None:
    WATCHDOG_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def tick() -> None:
    controller = ProviderController()
    for name in ("qwen", "deepseek"):
        try:
            ok, body = controller.is_healthy(name)
            if ok:
                continue
            # DOWN: try start, then re_auth if looks like auth issue
            started, detail = controller.start(name)
            if started:
                continue
            # start failed; check if recovery suggests re_auth
            recs = controller.suggest_recovery(name)
            if "re_auth" in recs:
                controller.re_auth(name)
        except Exception:
            pass


def main() -> None:
    state = _load_state()
    state["running"] = True
    _save_state(state)
    try:
        while state.get("running"):
            tick()
            time.sleep(POLL_INTERVAL)
    finally:
        state["running"] = False
        _save_state(state)


if __name__ == "__main__":
    main()
