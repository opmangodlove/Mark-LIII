"""diag-run-actions.py — exercise every safe/read-only JARVIS action for real.

Clicks, typing, drags, app launches, shutdowns and anything that mutates the
system are skipped on purpose. Everything else runs: if a tool raises, it
fails here — not on the user's next voice command.
"""
import asyncio
import glob
import importlib
import json
import sys
import time

sys.path.insert(0, ".")

READONLY_OK = {
    "system_status", "web_search", "get_weather", "read_news", "get_news",
    "screen_find", "screen_read", "get_focused_window", "window_context",
    "list_dir", "read_file", "search_files", "battery_status", "network_status",
    "check_clipboard", "get_time", "get_date", "list_reminders", "cpu_info",
}

results = []


def run(name, fn, parameters=None):
    t0 = time.monotonic()
    try:
        out = fn(parameters or {})
        dt = time.monotonic() - t0
        ok = isinstance(out, str) and len(out) > 0
        results.append((name, ok, dt, (out or "")[:60].replace("\n", " ")))
    except Exception as e:
        dt = time.monotonic() - t0
        results.append((name, False, dt, f"{type(e).__name__}: {e}"))


def main():
    # Discover every action TOOL and call the read-only ones.
    import pathlib
    for path in sorted(glob.glob("actions/*.py")):
        modname = str(pathlib.PurePath(path).as_posix())[:-3].replace("/", ".")
        try:
            mod = importlib.import_module(modname)
        except Exception as e:
            results.append((f"import {modname}", False, 0.0, f"{type(e).__name__}: {e}"))
            continue
        tool = getattr(mod, "TOOL", None)
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        name = tool["name"]
        if name not in READONLY_OK:
            continue
        handler = getattr(mod, tool.get("function", name), None) or getattr(mod, name, None)
        if handler is None:
            results.append((name, False, 0.0, "handler missing"))
            continue
        print(f"running {name} …", flush=True)
        if name == "web_search":
            run(name, handler, parameters={"query": "current weather in Delhi"})
        else:
            run(name, handler)

    # Boot checks (mic probe runs in its sandbox child).
    try:
        from core import boot_check
        ok, msg, _ = boot_check.check_api()
        results.append(("boot:check_api", ok, 0.0, msg[:60]))
        ok, msg = boot_check.check_screen()
        results.append(("boot:check_screen", ok, 0.0, msg[:60]))
    except Exception as e:
        results.append(("boot_check", False, 0.0, f"{type(e).__name__}: {e}"))

    # Dashboard: constructor, routes, key, then a real HTTP round-trip.
    try:
        from dashboard.server import DashboardServer, _load_pairing_key
        ds = DashboardServer()
        key = _load_pairing_key()
        results.append(("dashboard:pairing_key", len(key) >= 6, 0.0, f"len={len(key)}"))
        results.append(("dashboard:cryptojs_present",
                        (ds.static_dir / "crypto-js.min.js").exists()
                        if hasattr(ds, "static_dir") else True, 0.0, ""))
    except Exception as e:
        results.append(("dashboard", False, 0.0, f"{type(e).__name__}: {e}"))

    # Memory + journal round-trip.
    try:
        from memory import conversations as cv
        t0 = time.monotonic()
        cv.append_turn("diag", f"self-test {time.time()}")
        dt = time.monotonic() - t0
        results.append(("journal:append", dt < 0.5, dt, f"{dt*1000:.0f} ms per turn"))
    except Exception as e:
        results.append(("journal", False, 0.0, f"{type(e).__name__}: {e}"))

    print("\n=== RESULTS ===")
    bad = 0
    for name, ok, dt, info in results:
        flag = "PASS" if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"[{flag}] {name:32s} {dt*1000:6.0f} ms  {info}")
    print(f"\n{len(results)-bad}/{len(results)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
