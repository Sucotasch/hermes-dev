#!/usr/bin/env python3
"""Hermes tool wrapper for local provider management."""
from __future__ import annotations

from provider_manager import ProviderController


def start(name: str) -> Dict:
    c = ProviderController()
    ok, detail = c.start(name)
    return {"ok": ok, "detail": detail}


def stop(name: str) -> Dict:
    c = ProviderController()
    ok, detail = c.stop(name)
    return {"ok": ok, "detail": detail}


def health(name: str) -> Dict:
    c = ProviderController()
    ok, body = c.is_healthy(name)
    return {"ok": ok, "body": body}


def re_auth(name: str) -> Dict:
    c = ProviderController()
    ok, detail = c.re_auth(name)
    return {"ok": ok, "detail": detail}


def status_all() -> Dict:
    c = ProviderController()
    out = {}
    for name in ("qwen", "deepseek"):
        out[name] = c.get_status(name)
    return out


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "qwen"
    print(json.dumps(health(name), ensure_ascii=False))
