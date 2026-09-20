# Smoke test: the sandboxed pick_input_device wrapper must NEVER crash the
# parent, even when the probe child segfaults (reproduced 1-in-3 natively).
# Run: .venv/Scripts/python.exe test_sandbox.py
import sys
sys.path.insert(0, ".")
from core import mic_health

ok = True
for run in range(1, 5):
    mic_health._cache = None   # force a fresh child each run
    try:
        idx = mic_health.pick_input_device()
        print(f"run {run}: parent survived, idx={idx} "
              f"known_good={mic_health._known_good!r}", flush=True)
    except Exception as e:
        print(f"run {run}: parent raised {e!r}", flush=True)
        ok = False
print("SANDBOX:", "OK" if ok else "BROKEN")
sys.exit(0 if ok else 1)
