"""
actions/manage_tasks.py — Kanban task management for JARVIS.

One self-describing TOOL (discovered by core/action_loader like every other
action), backed by a plain JSON file — no database, no network, no setup:

    memory/tasks.json   { "tasks": [ {id, title, status, priority, tags, …} ] }

"Status" is the kanban column: todo | in_progress | blocked | done.

Every mutation calls notify_tasks_changed() afterwards, which pushes the full
board to the dashboard over the existing WebSocket (task_changed broadcast)
and, when configured, to Supabase — so a voice command like
"add a task to fix the login bug" appears on the phone dashboard instantly.

Voice and the web dashboard share ONE dispatch: dispatch() returns
(ok, message) for the REST API; manage_tasks() unwraps it for Gemini.

Handlers never raise — the loader treats a raise as a tool crash, so
everything is caught and returned as a spoken string.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import Lock

# ── Storage ───────────────────────────────────────────────────────────────────

_BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_PATH = _BASE_DIR / "memory" / "tasks.json"

STATUSES = ("todo", "in_progress", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "critical")

_lock = Lock()
_change_notifier = None      # set by main.py → dashboard WebSocket broadcast
_supabase_push = None        # set by memory/supabase_sync.py when configured


def set_change_notifier(fn) -> None:
    """Register a callable(dict) fired with the full board after each mutation."""
    global _change_notifier
    _change_notifier = fn


def set_supabase_push(fn) -> None:
    """Register a callable(dict) for fire-and-forget Supabase sync."""
    global _supabase_push
    _supabase_push = fn


def _load() -> list[dict]:
    try:
        data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("tasks"), list):
            return data["tasks"]
        if isinstance(data, list):          # tolerate the plain-list shape
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[Tasks] ⚠️ load error: {e}")
    return []


def _save(tasks: list[dict]) -> None:
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASKS_PATH.write_text(
        json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def board_data() -> dict:
    """Full board for the dashboard REST endpoint (and WS replay)."""
    with _lock:
        return {"type": "task_changed", "tasks": _load()}


def _notify(tasks: list[dict]) -> None:
    """Push the board to the dashboard and (optionally) Supabase.

    Runs in a worker thread when a tool executes in the executor — the
    notifiers are responsible for their own thread-safety (the dashboard's
    push_task_change uses run_coroutine_threadsafe)."""
    board = {"type": "task_changed", "tasks": tasks}
    if _change_notifier:
        try:
            _change_notifier(board)
        except Exception as e:
            print(f"[Tasks] notify error: {e}")
    if _supabase_push:
        try:
            _supabase_push(board)
        except Exception as e:
            print(f"[Tasks] supabase push error: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_status(raw) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "to_do": "todo", "backlog": "todo",
        "inprogress": "in_progress", "doing": "in_progress", "active": "in_progress",
        "waiting": "blocked", "stuck": "blocked", "waiting_on": "blocked",
        "completed": "done", "complete": "done", "finished": "done",
    }
    s = aliases.get(s, s)
    return s if s in STATUSES else None


def _norm_priority(raw) -> str | None:
    if not raw:
        return None
    p = str(raw).strip().lower()
    aliases = {"urgent": "critical", "asap": "critical", "highest": "critical",
               "normal": "medium", "mid": "medium"}
    p = aliases.get(p, p)
    return p if p in PRIORITIES else None


def _find(tasks: list[dict], ref: str) -> dict | None:
    """Match a task by exact id, id prefix, or case-insensitive title substring."""
    ref = (ref or "").strip()
    if not ref:
        return None
    for t in tasks:
        if t.get("id") == ref:
            return t
    matches = [t for t in tasks if str(t.get("id", "")).startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    low = ref.lower()
    hits = [t for t in tasks if low in str(t.get("title", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    return None          # ambiguous or nothing — the caller reports it


def _mk_task(title: str, status: str, priority: str | None,
             tags: list[str] | None) -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "title": title[:200],
        "status": status,
        "priority": priority or "medium",
        "tags": tags or [],
        "created": time.strftime("%Y-%m-%d"),
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }


# ── One dispatch, two faces ───────────────────────────────────────────────────

def dispatch(p: dict) -> tuple[bool, str]:
    """Shared logic for voice and the web dashboard. Returns (ok, message)."""
    p = p or {}
    action = str(p.get("action", "")).strip().lower()

    with _lock:
        tasks = _load()

    if action == "add":
        title = str(p.get("title", "")).strip()
        if not title:
            return False, "I need a task title to add something."
        status = _norm_status(p.get("status")) or "todo"
        prio   = _norm_priority(p.get("priority"))
        tags_raw = p.get("tags")
        tags = ([str(t).strip().lower() for t in tags_raw if str(t).strip()]
                if isinstance(tags_raw, list) else [])
        task = _mk_task(title, status, prio, tags)
        tasks.append(task)
        _save(tasks)
        _notify(tasks)
        return True, f"Added to {status.replace('_', ' ')}: {title}."

    if action == "list":
        if not tasks:
            return True, "The task board is empty."
        open_tasks = [t for t in tasks if t.get("status") != "done"]
        done_tasks = [t for t in tasks if t.get("status") == "done"]
        if not open_tasks:
            lines = ["Everything on the board is done."]
        else:
            lines = ["Open tasks:"]
            for st in STATUSES[:-1]:
                col = [t for t in open_tasks if t.get("status") == st]
                if not col:
                    continue
                lines.append(f"  {st.replace('_', ' ')}:")
                for t in col:
                    pr = f" [{t['priority']}]" if t.get("priority") != "medium" else ""
                    lines.append(f"    - {t['title']}{pr}")
        if done_tasks:
            lines.append(f"  ({len(done_tasks)} completed)")
        return True, "\n".join(lines)

    if action == "move":
        ref    = str(p.get("task", "")).strip()
        status = _norm_status(p.get("status"))
        if not ref or not status:
            return False, "Tell me which task and which column to move it to."
        task = _find(tasks, ref)
        if task is None:
            return False, f"I couldn't find a task matching '{ref}'."
        old = task["status"]
        task["status"] = status
        task["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _save(tasks)
        _notify(tasks)
        return True, f"Moved '{task['title']}' to {status.replace('_', ' ')}."

    if action == "delete":
        ref = str(p.get("task", "")).strip()
        if not ref:
            return False, "Which task should I delete?"
        task = _find(tasks, ref)
        if task is None:
            return False, f"No task matching '{ref}' to delete."
        tasks.remove(task)
        _save(tasks)
        _notify(tasks)
        return True, f"Deleted: {task['title']}."

    if action == "update":
        ref = str(p.get("task", "")).strip()
        if not ref:
            return False, "Which task should I update?"
        task = _find(tasks, ref)
        if task is None:
            return False, f"No task matching '{ref}' to update."
        changed = []
        new_title = str(p.get("title", "")).strip()
        if new_title and new_title != task["title"]:
            task["title"] = new_title[:200]
            changed.append("title")
        prio = _norm_priority(p.get("priority"))
        if prio and prio != task.get("priority"):
            task["priority"] = prio
            changed.append("priority")
        tags_raw = p.get("tags")
        if isinstance(tags_raw, list) and tags_raw:
            task["tags"] = [str(t).strip().lower() for t in tags_raw if str(t).strip()]
            changed.append("tags")
        if not changed:
            return True, f"Nothing to change on '{task['title']}'."
        task["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _save(tasks)
        _notify(tasks)
        return True, f"Updated {', '.join(changed)} on '{task['title']}'."

    return False, ("Unknown action. Use add, list, move, update or delete — "
                   "e.g. action='add', title='fix the login bug', status='todo'.")


def manage_tasks(parameters=None, player=None, **_ctx) -> str:
    """TOOL handler — spoken face of dispatch()."""
    ok, msg = dispatch(parameters or {})
    if player:
        try:
            player.write_log(f"[Tasks] {'✓' if ok else '✗'} {msg.splitlines()[0][:120]}")
        except Exception:
            pass
    return msg


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────

TOOL = {
    "name": "manage_tasks",
    "description": (
        "Manage the user's task board (kanban: todo, in progress, blocked, done). "
        "Use 'add' when the user says 'add a task to X', 'I need to do X', 'put X on my list' "
        "(a task, not a timed reminder). Use 'list' when they ask what tasks are open, "
        "what's on the board, or what's left to do. Use 'move' when they say 'mark X as done', "
        "'move X to in progress', 'X is blocked now'. Use 'delete' only when the user clearly "
        "wants the task gone ('delete the X task'). Use 'update' to change a task's title, "
        "priority or tags. NOT for timed reminders (reminder), NOT for routines (calendar)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "add | list | move | update | delete",
            },
            "title": {
                "type": "STRING",
                "description": "Task title (add) or replacement title (update). Keep it in the user's own words.",
            },
            "task": {
                "type": "STRING",
                "description": "Which task to affect (move/update/delete): the task's id or its title, or part of the title.",
            },
            "status": {
                "type": "STRING",
                "description": "Column for add/move: todo | in_progress | blocked | done",
            },
            "priority": {
                "type": "STRING",
                "description": "low | medium | high | critical (optional, for add/update)",
            },
            "tags": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional short labels, e.g. ['work','api'] (add/update)",
            },
        },
        "required": ["action"],
    },
    "handler": manage_tasks,
}
