"""
memory/procedures.py — learned procedures: HOW multi-step tasks were done.

WHY THIS EXISTS
    Every screen task used to be re-solved from scratch: screen_find for each
    element, wrong attempts, retries — tens of seconds per task. Humans don't
    work that way: once you know the sequence ("open WhatsApp → click search →
    type the name → Enter → click the chat → click the message box"), you
    replay it. This module stores exactly those proven step sequences and
    injects them into the system prompt, so the next "message <someone> on
    WhatsApp" replays a known-good path instead of exploring.

STORAGE
    memory/procedures.json — human-editable, gitignored alongside the rest of
    the memory directory. Oldest-updated entries are dropped past the cap.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_PROC_PATH = Path(__file__).resolve().parent / "procedures.json"
_MAX_PROCEDURES = 40
_MAX_STEPS = 14


def _load() -> dict:
    try:
        data = json.loads(_PROC_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data: dict) -> None:
    try:
        _PROC_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def save_procedure(task: str, steps: list) -> bool:
    """Store (or update) the proven step sequence for a task. Returns success."""
    task = (task or "").strip().lower()
    if isinstance(steps, str):
        steps = [s.strip() for s in steps.split("\n") if s.strip()]
    steps = [str(s).strip() for s in (steps or []) if s and str(s).strip()]
    if not task or not steps:
        return False
    data = _load()
    key = task[:80]
    data[key] = {
        "steps": steps[:_MAX_STEPS],
        "updated": time.strftime("%Y-%m-%d"),
        "uses": int(data.get(key, {}).get("uses", 0)),
    }
    if len(data) > _MAX_PROCEDURES:
        for k, _ in sorted(data.items(),
                           key=lambda kv: kv[1].get("updated", ""))[
                :len(data) - _MAX_PROCEDURES]:
            data.pop(k, None)
    _write(data)
    return True


def mark_used(task: str) -> None:
    """Count a replay (the model calls this when it follows a procedure)."""
    data = _load()
    key = (task or "").strip().lower()[:80]
    if key in data:
        data[key]["uses"] = int(data[key].get("uses", 0)) + 1
        _write(data)


def forget_procedure(task: str) -> bool:
    """Delete a procedure by name (exact first, then unique substring).
    Returns True if something was deleted."""
    data = _load()
    key = (task or "").strip().lower()
    if not key or not data:
        return False
    if key in data:
        data.pop(key)
        _write(data)
        return True
    hits = [k for k in data if key in k]
    if len(hits) == 1:
        data.pop(hits[0])
        _write(data)
        return True
    return False   # not found, or ambiguous (never guess)


def list_procedures() -> dict:
    return _load()


def render_for_prompt() -> str:
    """'[LEARNED PROCEDURES]' block for the system prompt, or '' if none."""
    data = _load()
    if not data:
        return ""
    lines = [
        "[LEARNED PROCEDURES — proven step sequences for tasks this user has "
        "done before. When asked for one of these, REPLAY the steps directly "
        "(still use screen_find for each on-screen element, since UIs move) "
        "instead of exploring. After finishing, call mark_procedure_used.]",
    ]
    for task, v in list(data.items())[:20]:
        steps = " → ".join(v.get("steps", []))
        uses = int(v.get("uses", 0))
        lines.append(f"- {task} ({'done ' + str(uses) + 'x' if uses else 'new'}): "
                     f"{steps}")
    return "\n".join(lines) + "\n"
