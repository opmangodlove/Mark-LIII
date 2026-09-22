"""diag-import-sweep.py — import every repo module; print only failures."""
import glob
import importlib
import sys

sys.path.insert(0, ".")

mods = set()
for pat in ("actions/*.py", "core/*.py", "memory/*.py", "dashboard/*.py"):
    for f in glob.glob(pat):
        parts = f.replace("\\", "/").split("/")
        parts = parts[:-1] + [parts[-1][:-3]]
        if parts[-1] == "__init__":
            continue
        mods.add(".".join(parts))
mods.update(["ui", "main"])

bad = 0
for m in sorted(mods):
    try:
        importlib.import_module(m)
    except Exception as e:
        bad += 1
        print(f"FAIL {m}: {type(e).__name__}: {e}")
print(f"import sweep: {len(mods) - bad}/{len(mods)} OK, {bad} failed")
sys.exit(1 if bad else 0)
