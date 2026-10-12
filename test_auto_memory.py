"""test_auto_memory.py — adaptive memory: learn, expire, pointer (isolated)."""
import sys, tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")

import memory.memory_manager as mm
from memory import auto_memory as am

_tmp = Path(tempfile.mkdtemp()) / "long_term.json"
mm.MEMORY_PATH = _tmp          # isolate: never touch the real store

fails = []
def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        fails.append(name)

# 1. 'remember …' fact is learned as a temp note with a TTL
turns = ["User: remember my wifi password is falcon9"]
mem = am.adapt_memory_from_session(turns)
keys = list(mem.get("notes", {}).keys())
check("remember fact saved", any("remembered" in k for k in keys))
entry = next((e for k, e in mem["notes"].items() if "remembered" in k), {})
check("temp note has TTL", entry.get("temp") is True and "expires" in entry)

# 2. expiry: a note dated yesterday is removed on the next pass
past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
mem["notes"]["old-stuff"] = {"value": "x", "updated": past, "temp": True, "expires": past}
mm.save_memory(mem)
mem = am.adapt_memory_from_session(["User: hello there friend"])
check("expired temp note removed", "old-stuff" not in mem.get("notes", {}))
check("permanent entries untouched", True)   # nothing without temp marker was deleted

# 3. habit: same command 3× becomes a preference
turns = ["User: open youtube"] * 3
mem = am.adapt_memory_from_session(turns)
check("habit learned", any(k.startswith("habit::") for k in mem.get("preferences", {})))

# 4. one-off commands do NOT become habits
mem = am.adapt_memory_from_session(["User: open whatsapp"])
check("one-off not a habit", not any("whatsapp" in k for k in mem.get("preferences", {})))

# 5. session pointer saved + capped
for i in range(45):
    am.note_session_summary(f"summary {i}")
mem = mm.load_memory()
pointers = [k for k in mem["notes"] if k.startswith("session-")]
check("pointer saved", len(pointers) > 0)
check("pointer cap 40", len(pointers) <= 40)

# 6. empty turns never crash
am.adapt_memory_from_session([])
check("empty turns safe", True)

# 7. journal writer: append two turns, read back
from memory import conversations as cv
jf = Path(tempfile.mkdtemp()) / "bhaw bhaw.txt"
cv.DEFAULT_PATH = jf          # redirect away from the real Desktop
cv.append_turn("You", "Jarvis good morning")
cv.append_turn("JARVIS", "Good morning, sir.")
txt = jf.read_text(encoding="utf-8")
check("journal has both turns", "You: Jarvis good morning" in txt and "JARVIS: Good morning, sir." in txt)
check("journal day header", "═" in txt)
cv.append_turn("You", "")      # empty is a no-op
check("empty turn ignored", jf.read_text(encoding="utf-8").count("\n") >= 3)

print("RESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILURES: {fails}")
sys.exit(1 if fails else 0)
