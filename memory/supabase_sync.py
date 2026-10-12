"""
memory/supabase_sync.py — Optional cloud sync for the JARVIS command center.

Free-tier Supabase (supabase.com → new project → free) gives the dashboard
cross-device persistence: tasks created by voice on the PC appear on your
phone even when the PC is asleep, and tasks added from the phone dashboard
land back on the PC.

ACTIVATION (deliberately manual — the assistant stays offline-first):
    1. Create a free project at supabase.com
    2. SQL Editor → run the SQL in docs/supabase_schema.sql (creates the
       jarvis_tasks table + row-level-security policies)
    3. Project Settings → API → copy the Project URL and the SERVICE_ROLE key
    4. Save both to config/supabase.json:
           { "url": "https://xxxx.supabase.co",
             "service_key": "eyJhbGciOi…" }
    5. pip install supabase        (the only new dependency, and it's optional)
    6. Start JARVIS — the log line "[Supabase] sync active" confirms it.

WITHOUT that file nothing here runs: no import cost at runtime beyond this
module, no network calls, no behavior change. The local JSON store stays the
source of truth; Supabase is a mirror, not a replacement.

Design notes:
  - Push is last-writer-wins per task row (upsert on task id). The board is
    small (dozens of rows), so pushing the full board after each mutation is
    simpler and cheaper than diffing.
  - Pull happens once at startup: remote rows newer than the local file's
    newest `updated` stamp are applied locally. Live two-way merge is
    deliberately out of scope — two simultaneous editors are you and you.
  - The service key never leaves the machine; it lives in the gitignored
    config/ directory next to the Gemini key.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = _BASE_DIR / "config" / "supabase.json"

_client = None            # lazy supabase client
_enabled = False
_last_push = 0.0
_MIN_PUSH_INTERVAL = 2.0  # debounce: voice can mutate several times a second


def _load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        url = str(data.get("url", "")).strip().rstrip("/")
        key = str(data.get("service_key", "")).strip()
        if url.startswith("http") and len(key) > 20:
            return {"url": url, "key": key}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Supabase] config unreadable: {e}")
    return {}


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        from supabase import create_client          # optional dependency
    except ImportError:
        print("[Supabase] pip install supabase — sync disabled")
        return None
    cfg = _load_config()
    if not cfg:
        return None
    try:
        _client = create_client(cfg["url"], cfg["key"])
        return _client
    except Exception as e:
        print(f"[Supabase] client init failed: {e}")
        return None


# ── Push (called after every local mutation) ─────────────────────────────────

def _push_board_blocking(board: dict) -> None:
    global _last_push
    now = time.monotonic()
    if now - _last_push < _MIN_PUSH_INTERVAL:
        return
    _last_push = now

    sb = _get_client()
    if sb is None:
        return
    rows = []
    for t in board.get("tasks", []):
        rows.append({
            "id": str(t.get("id", "")),
            "title": t.get("title", ""),
            "status": t.get("status", "todo"),
            "priority": t.get("priority", "medium"),
            "tags": t.get("tags", []),
            "created": t.get("created", ""),
            "updated": t.get("updated", ""),
        })
    rows = [r for r in rows if r["id"]]
    if not rows:
        return
    try:
        sb.table("jarvis_tasks").upsert(rows).execute()
    except Exception as e:
        # Never let a network hiccup touch the assistant's flow — the local
        # store is already saved; the next mutation re-pushes everything.
        print(f"[Supabase] push failed: {e}")


def push_board(board: dict) -> None:
    """Sync entry point handed to manage_tasks.set_supabase_push().
    Runs the network call in a daemon thread so tool handlers never block."""
    if not _enabled:
        return
    try:
        threading.Thread(target=_push_board_blocking, args=(board,),
                         daemon=True).start()
    except Exception:
        pass


# ── Pull (once at startup) ────────────────────────────────────────────────────

def _pull_once_blocking() -> int:
    """Apply remote rows newer than the local file's newest stamp. Returns
    the number of tasks applied. Local-only tasks are kept (union merge)."""
    import sys as _sys
    if str(_BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(_BASE_DIR))

    from actions.manage_tasks import _load, _save, _lock

    sb = _get_client()
    if sb is None:
        return 0
    try:
        resp = sb.table("jarvis_tasks").select("*").execute()
    except Exception as e:
        print(f"[Supabase] pull failed: {e}")
        return 0

    remote = resp.data or []
    if not remote:
        return 0

    local_newest = ""
    with _lock:
        local = _load()
        for t in local:
            u = str(t.get("updated", ""))
            if u > local_newest:
                local_newest = u

        by_id = {str(t.get("id")): t for t in local}
        applied = 0
        for r in remote:
            rid = str(r.get("id", ""))
            ru  = str(r.get("updated", ""))
            if not rid or ru <= local_newest:
                continue
            by_id[rid] = {
                "id": rid,
                "title": str(r.get("title", ""))[:200],
                "status": r.get("status", "todo"),
                "priority": r.get("priority", "medium"),
                "tags": r.get("tags", []) if isinstance(r.get("tags"), list) else [],
                "created": str(r.get("created", "")),
                "updated": ru,
            }
            applied += 1
        if applied:
            _save(list(by_id.values()))
    if applied:
        print(f"[Supabase] pulled {applied} remote task(s) into the local board")
    return applied


# ── Startup ───────────────────────────────────────────────────────────────────

async def start_supabase_sync(dashboard=None) -> None:
    """Called once from main.py. Wires push into manage_tasks, pulls the
    remote board, and mirrors phone-dashboard mutations back up.

    Exits immediately (and silently) when config/supabase.json is absent —
    the offline-first default."""
    global _enabled

    if not _load_config():
        return                                    # not configured — stay local
    sb = _get_client()
    if sb is None:
        return

    try:
        sb.table("jarvis_tasks").select("id").limit(1).execute()
    except Exception as e:
        print(f"[Supabase] table check failed ({e}) — run docs/supabase_schema.sql")
        return

    _enabled = True

    try:
        from actions import manage_tasks as tasks_mod
        # _notify() inside dispatch() already fires push_board for every
        # mutation — voice, tool, and REST /api/tasks all funnel through
        # dispatch — so no extra wrapper is needed (and wrapping here would
        # double-push every change).
        tasks_mod.set_supabase_push(push_board)
    except Exception as e:
        print(f"[Supabase] wiring failed: {e}")
        return

    applied = await asyncio.get_running_loop().run_in_executor(
        None, _pull_once_blocking)
    print(f"[Supabase] sync active ({applied} remote task(s) newer than local)")
