"""
core/window_context.py — what the user is looking at, for free.

WHY THIS EXISTS
    Commands like "close this" or "minimize this" are ambiguous without
    knowing which window the user means. A screenshot costs seconds; the
    foreground window title costs microseconds via Win32. Injected into the
    conversation, it lets the model resolve "this" instantly — and pick
    close_window (tab) vs close_app (program) correctly.
"""

from __future__ import annotations

import time
from datetime import datetime

_cache_name: str = ""
_cache_ts: float = 0.0


def get_active_window(max_age: float = 0.8) -> str:
    """Title of the foreground window (cached for max_age seconds)."""
    global _cache_name, _cache_ts
    now = time.monotonic()
    if _cache_name and (now - _cache_ts) < max_age:
        return _cache_name
    name = ""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                name = (buf.value or "").strip()
    except Exception:
        name = ""
    _cache_name, _cache_ts = name, now
    return name


def context_line() -> str:
    """One-line [ACTIVE WINDOW] context, or '' when nothing useful."""
    try:
        title = get_active_window()
        if not title:
            return ""
        hhmm = datetime.now().strftime("%I:%M %p")
        return f"[ACTIVE WINDOW] {title} (local time {hhmm})."
    except Exception:
        return ""
