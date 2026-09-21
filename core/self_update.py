"""
core/self_update.py — "Jarvis, update yourself."

WHY THIS EXISTS
    The user's JARVIS patches live on GitHub (opmangodlove/Mark-LIII).
    Updates previously required Freebuff: pull, verify, restart by hand.
    This module lets JARVIS do it by voice, safely:

        1. git fetch + compare        (no-op when already current)
        2. git pull --ff-only         (never merge over local edits)
        3. py_compile gate            (a broken pull can never kill JARVIS —
                                       the old code keeps running on failure)
        4. detached one-shot restarter (launches main.py 4 s after exit —
                                       inside the launcher's 2-tick crash
                                       window, so revival stays silent)
"""

from __future__ import annotations

import json
import py_compile
import subprocess
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
GIT     = "git"
VENV_PY = APP_DIR / ".venv" / "Scripts" / "python.exe"
MAIN_PY = APP_DIR / "main.py"
# Written before exit; main.py consumes it on the next boot to confirm.
MARKER  = APP_DIR / "config" / "last_update.json"

# Log/state files that must never block an update.
_NOISE_FILES = {
    "jlog-out.txt", "jlog-err.txt", "vlog-out.txt", "vlog-err.txt",
    "clean_exit.flag", "diag-click-timing.py", "test_sandbox.py",
}


def _git(args: list[str], timeout: float = 60.0) -> tuple[bool, str]:
    """Run a git command in the app dir. Returns (ok, combined_output)."""
    try:
        r = subprocess.run(
            [GIT, *args], cwd=str(APP_DIR), capture_output=True, text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def _local_modifications() -> int:
    """Tracked files with real edits (untracked logs don't count)."""
    ok, out = _git(["status", "--porcelain"])
    if not ok:
        return -1
    count = 0
    for line in out.splitlines():
        if not line.strip() or line.startswith("?"):
            continue
        if any(n in line for n in _NOISE_FILES):
            continue
        count += 1
    return count


def _compile_gate() -> list[str]:
    """Compile every shipped .py; return human-readable failures."""
    bad: list[str] = []
    for py in APP_DIR.rglob("*.py"):
        s = str(py)
        if (".venv" in s or "__pycache__" in s
                or "voice-launcher" + "\\vosk" in s):
            continue
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{py.name}: {e.msg}")
        except Exception as e:
            bad.append(f"{py.name}: {e}")
    return bad


def update_now(logger=print) -> str:
    """Pull, verify, schedule restart. Returns a short status string."""
    logger("checking for updates…")

    ok, out = _git(["fetch", "origin"])
    if not ok:
        return f"UPDATE_FAIL: cannot reach GitHub — {out[:120]}"

    ok, out = _git(["rev-list", "--count", "HEAD..origin/main"])
    if not ok:
        return f"UPDATE_FAIL: git problem — {out[:120]}"
    try:
        behind = int(out.strip() or "0")
    except ValueError:
        behind = 0
    if behind == 0:
        logger("already up to date")
        return "UPDATE_OK: already running the latest version."

    mods = _local_modifications()
    if mods > 0:
        return (f"UPDATE_FAIL: {mods} unsaved local edit(s) — "
                f"they must be committed first.")

    logger(f"{behind} new commit(s) — pulling…")
    ok, out = _git(["pull", "--ff-only", "origin", "main"])
    if not ok:
        return f"UPDATE_FAIL: pull failed — {out[:120]}"

    bad = _compile_gate()
    if bad:
        logger("compile gate failed — rolling back")
        _git(["reset", "--hard", "HEAD@{1}"])
        return (f"UPDATE_FAIL: the new code has syntax errors — rolled back "
                f"safely. First error: {bad[0][:100]}")

    try:
        MARKER.parent.mkdir(parents=True, exist_ok=True)
        MARKER.write_text(json.dumps({
            "ts": time.time(), "commits": behind,
        }), encoding="utf-8")
    except Exception:
        pass

    logger(f"updated {behind} commit(s) — restarting")
    _schedule_restart()
    return f"UPDATE_OK: installed {behind} new commit(s). Restarting."


def _schedule_restart() -> None:
    """
    Relaunch path: a detached one-shot spawns main.py 4 s after this process
    exits. The launcher's crash-revival needs TWO consecutive empty polls
    (10 s apart) — the fresh process appears well inside that window, so the
    revival monitor never fires and never double-launches. If the launcher
    isn't running at all, the one-shot is the only path needed.
    """
    import os
    import threading

    if VENV_PY.exists() and MAIN_PY.exists():
        code = (
            "import time, subprocess, sys;"
            "time.sleep(4);"
            "subprocess.Popen([sys.executable, r'%s'], cwd=r'%s',"
            " creationflags=subprocess.CREATE_NO_WINDOW);"
        ) % (str(MAIN_PY), str(APP_DIR))
        try:
            subprocess.Popen(
                [str(VENV_PY), "-c", code],
                cwd=str(APP_DIR),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # launcher revival remains as the fallback path

    # Exit after the FunctionResponse is delivered (caller delays 1.5 s).
    def _die():
        time.sleep(1.2)
        os._exit(0)

    threading.Thread(target=_die, daemon=True).start()


def consume_boot_marker() -> dict | None:
    """Called by main.py on boot: returns the last self-update info (once)."""
    try:
        if MARKER.exists():
            data = json.loads(MARKER.read_text(encoding="utf-8"))
            MARKER.unlink(missing_ok=True)
            return data
    except Exception:
        pass
    return None
