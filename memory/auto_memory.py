"""
memory/auto_memory.py — JARVIS maintains his own memory.

THE PROBLEM
    Memory previously grew in exactly two ways: the model called save_memory
    when it felt like it, and one LLM-summarised line landed in 'notes' at
    session end. Facts the model never bothered to save were lost; temporary
    session debris ('notes/session-2026-09-21') piled up forever; and nothing
    ever learned *habits* — that 'open youtube' always means the music, that
    the user checks the battery constantly.

WHAT THIS DOES (all automatic, no user action, no tool calls needed)
    1. LEARN — scans the verbatim session turns and stores:
         • temporary facts the model never saved: "remember my wifi password
           is X", "my exam is on Friday" → memory['notes'] with a 7-day TTL
         • habits: recurring command patterns (≥3 occurrences) →
           memory['preferences'] as 'habit::<slug>' so the model can
           anticipate instead of asking every time.
    2. EXPIRE — anything in memory['notes'] whose entry carries
       'temp': true and an 'expires' date in the past is deleted. This is the
       "remove temporary memory by itself" half: temporary notes are written
       with a TTL and vanish on their own, without touching anything
       permanent.
    3. POINTER — note_session_summary() stores each LLM session summary as a
       recallable 'note/session-YYYY-MM-DD-HHMM' entry (capped at the last
       40), so "what did we talk about yesterday" hits memory instead of
       guesswork.

SAFETY
    Only reads the store the app already uses (memory_manager), only touches
    the 'notes' and 'preferences' categories, and never deletes anything that
    lacks a temp/expires marker. Every entry still has the standard
    {'value', 'updated'} shape the prompt renderer expects.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from memory.memory_manager import load_memory, save_memory

_HABIT_MIN_COUNT = 3          # seen this many times → it is a habit
_SESSION_POINTER_CAP = 40     # keep the last N session summaries
_MAX_TTL_DAYS = 7

# Turns that look like transient commands, not facts to remember.
_TRANSIENT_RE = re.compile(
    r"^(open|close|play|pause|stop|click|type|press|scroll|minimi[sz]e|"
    r"maximi[sz]e|search|find|mute|unmute|volume|wake|sleep|shutdown|"
    r"restart|screenshot|what|who|when|where|how|is|are|can|do|does)\b",
    re.IGNORECASE,
)
# Explicitly temporary facts the user hands over mid-conversation.
_REMEMBER_RE = re.compile(
    r"\b(remember|note that|don'?t forget|keep in mind)\b[:\s]+(.{4,300})",
    re.IGNORECASE,
)


def _slug(text: str, maxlen: int = 36) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:maxlen].rstrip("-") or "x"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _expire_temporary(memory: dict) -> int:
    """Delete expired temporary notes. Returns how many were removed."""
    notes = memory.get("notes")
    if not isinstance(notes, dict):
        return 0
    today = _today()
    dropped = [k for k, e in notes.items()
               if isinstance(e, dict) and e.get("temp") and e.get("expires", "9999") < today]
    for k in dropped:
        del notes[k]
    return len(dropped)


def _learn_remembered(memory: dict, turns: list[str]) -> int:
    """Catch 'remember …' facts the model forgot to save — as temp notes
    with a TTL (most 'remember X' instructions are time-scoped)."""
    learned = 0
    notes = memory.setdefault("notes", {})
    for turn in turns:
        if not turn.lower().startswith("user:"):
            continue
        for _m in _REMEMBER_RE.finditer(turn):
            fact = _m.group(2).strip().strip("\"'")
            if len(fact) < 4:
                continue
            key = f"remembered-{_slug(fact)}"
            if key in notes and notes[key].get("value") == fact:
                continue
            notes[key] = {
                "value": fact,
                "updated": _today(),
                "temp": True,
                "expires": (datetime.now() + timedelta(days=_MAX_TTL_DAYS)).strftime("%Y-%m-%d"),
            }
            learned += 1
    return learned


def _learn_habits(memory: dict, turns: list[str]) -> int:
    """Commands repeated ≥3 times across the session become preference
    entries the system prompt already renders — the model then anticipates
    them instead of re-asking."""
    counts: dict[str, int] = {}
    for turn in turns:
        if not turn.lower().startswith("user:"):
            continue
        text = turn.split(":", 1)[1].strip()
        if not (4 <= len(text) <= 80) or _TRANSIENT_RE.match(text):
            counts[text.lower()] = counts.get(text.lower(), 0) + 1
    learned = 0
    prefs = memory.setdefault("preferences", {})
    for text, n in counts.items():
        if n < _HABIT_MIN_COUNT:
            continue
        key = f"habit::{_slug(text)}"
        val = f"often asks: '{text}' — anticipate this"
        if prefs.get(key, {}).get("value") != val:
            prefs[key] = {"value": val, "updated": _today()}
            learned += 1
    return learned


def adapt_memory_from_session(turns: list[str]) -> dict:
    """Fold one finished session's turns into permanent memory.

    Called from main._save_session_summary — already off the audio path.
    Order matters: expire first (frees keys), then learn.
    """
    if not turns:
        return load_memory()
    memory = load_memory()
    expired = _expire_temporary(memory)
    remembered = _learn_remembered(memory, turns)
    habits = _learn_habits(memory, turns)
    if expired or remembered or habits:
        save_memory(memory)
        try:
            print(f"[Memory] auto: expired {expired}, saved {remembered} remembered "
                  f"fact(s), {habits} habit(s)")
        except Exception:
            pass   # cp1252 consoles must never break the memory pass
    return memory


def note_session_summary(summary: str) -> None:
    """Store the LLM's session summary as a recallable rolling pointer."""
    try:
        memory = load_memory()
        notes = memory.setdefault("notes", {})
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        # Expire old pointers first so the cap is applied to live entries.
        _expire_temporary(memory)
        notes[f"session-{stamp.replace(':', '-').replace(' ', '-')}"] = {
            "value": summary[:380],
            "updated": _today(),
            "temp": True,
            "expires": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d"),
        }
        # Cap: newest N session pointers survive.
        skeys = sorted(k for k in notes if k.startswith("session-"))
        for k in skeys[:-_SESSION_POINTER_CAP] if len(skeys) > _SESSION_POINTER_CAP else []:
            del notes[k]
        save_memory(memory)
    except Exception as e:
        print(f"[Memory] ⚠️ session pointer failed: {e}")
