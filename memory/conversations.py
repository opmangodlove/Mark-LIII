"""
memory/conversations.py — every word JARVIS hears and says, kept forever.

WHY THIS EXISTS
    The long-term memory (memory/long_term.json) stores a *summary* of what
    was talked about. The user asked for the conversations themselves — the
    full turns, verbatim — written to a file on the Desktop named 'bhaw bhaw'
    so they can be read back at any time.

    The path is configurable: an optional 'conversation_path' key in
    config/settings.json moves the journal (the user may want it elsewhere);
    the default is exactly what was asked for.

FORMAT
    Plain text, one turn per line, day headers between. Readable in Notepad
    on any PC, greppable, and it never needs parsing to be useful:

        ── Tuesday 2026-09-22 ──────────────
        07:14:03  You: Jarvis good morning
        07:14:21  JARVIS: Good morning, sir. Your battery is at 90%…

HOUSEKEEPING
    - One I/O syscall per turn: open(append) → write → close. Nothing is held
      in RAM and nothing is lost on a crash mid-turn.
    - A month of heavy use is a few hundred KB, but the journal still defends
      itself: past ~5 MB the oldest days are deleted until it fits again.
"""

import io
import os
import re
from datetime import datetime
from pathlib import Path

try:
    from memory.memory_manager import get_base_dir  # repo layout anchor
except Exception:                                    # pragma: no cover
    def get_base_dir() -> Path:
        return Path(__file__).resolve().parent.parent

BASE_DIR = get_base_dir()
DEFAULT_PATH = Path.home() / "Desktop" / "bhaw bhaw.txt"
MAX_JOURNAL_BYTES = 5 * 1024 * 1024      # self-defence: never let it grow unbounded
PRUNE_TARGET_BYTES = 4 * 1024 * 1024     # after pruning, the journal fits under this
_DAY_HEADER = "═" * 8

_bad = re.compile(r'[\\/:*?"<>|]')


def _journal_path() -> Path:
    """Configurable via config/settings.json → 'conversation_path'."""
    try:
        import json
        cfg = BASE_DIR / "config" / "settings.json"
        if cfg.exists():
            val = (json.loads(cfg.read_text(encoding="utf-8")) or {}).get("conversation_path")
            if isinstance(val, str) and val.strip():
                p = Path(os.path.expandvars(val.strip()))
                if p.parent.exists():
                    return p
    except Exception:
        pass
    return DEFAULT_PATH


def _prune(path: Path) -> None:
    """Drop whole day-blocks from the front until the file fits. Called only
    when the journal actually exceeds MAX_JOURNAL_BYTES."""
    try:
        if not path.exists() or path.stat().st_size <= MAX_JOURNAL_BYTES:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = text.split(_DAY_HEADER)
        kept: list[str] = []
        total = 0
        for blk in reversed(blocks):                 # newest first
            sz = len(blk.encode("utf-8", errors="replace"))
            if total + sz > PRUNE_TARGET_BYTES and kept:
                break
            kept.append(blk)
            total += sz
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_DAY_HEADER.join(reversed(kept)), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def append_turn(speaker: str, text: str, ts: datetime | None = None) -> None:
    """One conversation turn, verbatim, to the Desktop journal. Never raises:
    a read-only Desktop or a full disk must not take down the audio loop that
    calls this between turn_complete events."""
    try:
        t = text.strip()
        if not t:
            return
        now = ts or datetime.now()
        path = _journal_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        day = now.strftime("%A %Y-%m-%d")
        line = f"{now.strftime('%H:%M:%S')}  {speaker}: {t}"
        # Decide the day header by reading a tail slice — one small read,
        # then a single append write. No file locking needed: only the
        # audio-loop thread writes conversation turns.
        try:
            size = path.stat().st_size
            with io.open(path, "rb") as f:
                f.seek(max(0, size - 400))
                tail = f.read().decode("utf-8", errors="replace")
            need_header = day not in tail
        except Exception:
            need_header = True
        with io.open(path, "a", encoding="utf-8") as f:
            if need_header:
                f.write(f"\n\n{_DAY_HEADER} {day} {_DAY_HEADER}\n")
            f.write(line + "\n")
        _prune(path)
    except Exception:
        pass
