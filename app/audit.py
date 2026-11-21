"""Append-only JSONL audit log of every tool call and server decision."""
import json
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()


def _summary(args: dict) -> str:
    parts = []
    for k, v in (args or {}).items():
        if k in ("tool_context",):
            continue
        s = str(v).replace("\n", " ")
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)[:240]


def write(agent: str, tool: str, args: dict | None, decision: str, reason: str = "", why: str = "") -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent": agent,
        "tool": tool,
        "args": _summary(args or {}),
        "decision": decision,  # allowed / blocked / info / fallback
        "reason": (reason or "")[:240],
        "why": (why or "")[:240],
    }
    config.AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(config.AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read(limit: int = 200) -> list[dict]:
    if not config.AUDIT_PATH.exists():
        return []
    with _lock, open(config.AUDIT_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def clear() -> None:
    config.AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(config.AUDIT_PATH, "w", encoding="utf-8"):
        pass
