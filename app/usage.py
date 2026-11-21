"""Counts Gemini calls per process step and records failures (status code + step only)."""
import logging
import threading
from collections import Counter

log = logging.getLogger("transtudy.usage")
_lock = threading.Lock()
calls: Counter = Counter()
failures: list[dict] = []


def count(step: str) -> None:
    with _lock:
        calls[step] += 1


def fail(api: str, step: str, status) -> None:
    with _lock:
        failures.append({"api": api, "step": step, "status": str(status)})
    log.warning("API failure: api=%s step=%s status=%s", api, step, status)


def status_of(exc: Exception):
    for attr in ("code", "status_code", "status"):
        v = getattr(exc, attr, None)
        if v:
            return v
    resp = getattr(exc, "response", None)
    if resp is not None and getattr(resp, "status_code", None):
        return resp.status_code
    return type(exc).__name__


def snapshot() -> dict:
    with _lock:
        return {"gemini_calls": dict(calls), "gemini_total": sum(calls.values()), "failures": list(failures)}
