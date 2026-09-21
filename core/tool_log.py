"""
Tool-activity recorder — the data behind the UI's session log viewer.

Every tool call goes through JarvisLive._execute_tool; that method appends one
structured event here, and the UI's TOOL LOG overlay renders the last N events
with their full arguments and results. The ring buffer lives in this module (not
on the instance) so the overlay, future dashboards, or debugging sessions can
read the same trail without plumbing a reference through the UI.
"""
from __future__ import annotations

import json
import threading
import time

_LOCK = threading.Lock()
_EVENTS: list[dict] = []          # oldest first
_MAX_EVENTS = 2000                # ~a full day of activity; ring buffer
                                  # (the phone's 24h-history button reads this)

# Argument values longer than this are truncated in the stored copy — the log
# is for "what did it do", not a data warehouse. Results are clipped harder:
# a web_search dump can be thousands of characters.
_MAX_ARG = 160
_MAX_RESULT = 300


def _clip(value, limit: int):
    """JSON-safe, size-capped copy of an argument or result value."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def record_tool_event(name: str, args: dict | None, result: str, ok: bool,
                      duration_s: float | None = None) -> None:
    """Append one tool call to the ring buffer. Never raises."""
    try:
        event = {
            "ts": time.time(),
            "name": str(name)[:80],
            "args": {str(k)[:40]: _clip(v, _MAX_ARG)
                     for k, v in (args or {}).items()},
            "result": _clip(str(result), _MAX_RESULT),
            "ok": bool(ok),
            "duration": (round(duration_s, 2) if duration_s is not None else None),
        }
        with _LOCK:
            _EVENTS.append(event)
            if len(_EVENTS) > _MAX_EVENTS:
                del _EVENTS[: len(_EVENTS) - _MAX_EVENTS]
    except Exception:
        pass  # a logging failure must never break a tool call


def events_since(since_ts: float) -> list[dict]:
    """All tool events with ts >= since_ts, oldest first. Never raises."""
    try:
        with _LOCK:
            return [e for e in _EVENTS
                    if float(e.get("ts", 0) or 0) >= since_ts]
    except Exception:
        return []


def recent_events(count: int = 60) -> list[dict]:
    """The newest `count` events, oldest first."""
    with _LOCK:
        return list(_EVENTS[-max(0, count):])


def format_events_for_ui(count: int = 60) -> str:
    """Human-readable transcript for the TOOL LOG overlay."""
    events = recent_events(count)
    if not events:
        return "No tool calls yet in this session.\n\n" \
               "Everything JARVIS does through its tools — opening apps, " \
               "searching the web, controlling the computer — will appear " \
               "here with the exact arguments it used."

    lines = []
    for ev in events:
        t = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        dur = f"  {ev['duration']}s" if ev.get("duration") else ""
        mark = "OK  " if ev["ok"] else "FAIL"
        lines.append(f"[{t}] {mark}  {ev['name']}{dur}")

        if ev["args"]:
            for k, v in ev["args"].items():
                lines.append(f"    {k}: {v}")
        else:
            lines.append("    (no arguments)")
        lines.append(f"    -> {ev['result']}")
        lines.append("")

    return "\n".join(lines).rstrip() + f"\n\n-- last {len(events)} tool calls --"
