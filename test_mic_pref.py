# One-shot test: mic_health preference logic with mocked devices.
# Not part of the app; run manually: .venv/Scripts/python.exe test_mic_pref.py
import sys, types
sys.path.insert(0, ".")

# ── fake config_manager (deterministic preference, no real config file) ────
PREF = {"v": ""}
_cfg = types.ModuleType("memory.config_manager")
_cfg.get_input_device = lambda: ""
_cfg.get_mic_preference = lambda: PREF["v"]
sys.modules["memory.config_manager"] = _cfg

# ── fake sounddevice: #0 = built-in AMD array (OS default), #1 = BT headset ─
DEVS = [
    {"name": "Microphone Array (AMD Audio Device)", "max_input_channels": 2,
     "default_samplerate": 44100.0, "index": 0},
    {"name": "Headset (Rockerz 110)", "max_input_channels": 1,
     "default_samplerate": 16000.0, "index": 1},
]
_fake_sd = types.ModuleType("sounddevice")
_fake_sd.query_devices = lambda: [dict(d) for d in DEVS]
_fake_sd.default = types.SimpleNamespace(device=(0, -1))
sys.modules["sounddevice"] = _fake_sd

# No-op time.sleep (BT retry paths must not stall the test)
import time as _t
_t.sleep = lambda s: None

from core import mic_health
mic_health._ensure_endpoints_healthy = lambda logger=None: None
# Isolate from the REAL shared-mic state file (the live JARVIS process may
# have written a device name that would hijack every case below).
import tempfile, os as _os
_fh, _tmp = tempfile.mkstemp(suffix=".txt")
_os.close(_fh)
_os.remove(_tmp)
mic_health._STATE_FILE = __import__("pathlib").Path(_tmp)

FAILS = []

def run_case(name, pref, peaks, expect):
    PREF["v"] = pref
    mic_health._peak_level = lambda idx, sr, sec: peaks[idx]
    mic_health._cache = None
    got = mic_health.pick_input_device_inprocess()
    status = "PASS" if got == expect else "FAIL"
    if got != expect:
        FAILS.append(name)
    print(f"[{status}] {name}: expected={expect} got={got}")

# 1 auto: headset hears → headset wins (BT when connected, per user spec)
run_case("auto: BT hears, AMD silent", "", {0: 0, 1: 9000}, 1)
# 2 auto: headset silent → system default (built-in array)
run_case("auto: BT silent, AMD hears", "", {0: 8500, 1: 0}, 0)
# 3 auto: everything silent → None (system default fallback)
run_case("auto: all silent", "", {0: 0, 1: 0}, None)
# 4 pref builtin: user said laptop mic → AMD wins even though BT also hears
run_case("builtin pref overrides BT", "builtin", {0: 8500, 1: 9000}, 0)
# 5 pref bluetooth: BT first even if AMD is louder
run_case("bluetooth pref wins", "bluetooth", {0: 30000, 1: 500}, 1)
# 6 pref builtin but AMD truly dead → falls through to BT (proof decides)
run_case("builtin pref, AMD dead", "builtin", {0: 0, 1: 7000}, 1)

# 7 known-good short-circuit: last verified mic still hears → kept instantly
PREF["v"] = ""
mic_health._peak_level = lambda idx, sr, sec: {0: 0, 1: 9000}[idx]
mic_health._cache = None
got = mic_health.pick_input_device_inprocess(known_good="Headset (Rockerz 110)")
print(f"[{'PASS' if got == 1 else 'FAIL'}] known-good kept: expected=1 got={got}")
if got != 1:
    FAILS.append("known-good kept")

# 8 known-good stops hearing → full audit re-picks AMD
mic_health._cache = None
mic_health._peak_level = lambda idx, sr, sec: {0: 8500, 1: 0}[idx]
got = mic_health.pick_input_device_inprocess(known_good="Headset (Rockerz 110)")
print(f"[{'PASS' if got == 0 else 'FAIL'}] known-good stale: expected=0 got={got}")
if got != 0:
    FAILS.append("known-good stale")

# 9 shared-mic adoption (auto pref): the other process's proven mic wins
# without probing — contention makes probes unreliable.
def set_shared(name):
    with open(_tmp, "w", encoding="utf-8") as f:
        f.write(name)

set_shared("Microphone Array (AMD Audio Device)")
mic_health._cache = None
PREF["v"] = ""
mic_health._peak_level = lambda idx, sr, sec: {0: 0, 1: 9000}[idx]  # AMD silent
got = mic_health.pick_input_device_inprocess()
print(f"[{'PASS' if got == 0 else 'FAIL'}] shared adopted (auto): expected=0 got={got}")
if got != 0:
    FAILS.append("shared adopted (auto)")

# 10 shared mic NOT adopted when it contradicts an explicit preference
set_shared("Microphone Array (AMD Audio Device)")
mic_health._cache = None
PREF["v"] = "bluetooth"
mic_health._peak_level = lambda idx, sr, sec: {0: 0, 1: 9000}[idx]
got = mic_health.pick_input_device_inprocess()
print(f"[{'PASS' if got == 1 else 'FAIL'}] pref beats stale shared: expected=1 got={got}")
if got != 1:
    FAILS.append("pref beats stale shared")

# 11 shared mic of a vanished device → full audit still works
set_shared("Vanished Ghost Mic 3000")
mic_health._cache = None
PREF["v"] = ""
mic_health._peak_level = lambda idx, sr, sec: {0: 0, 1: 9000}[idx]
got = mic_health.pick_input_device_inprocess()
print(f"[{'PASS' if got == 1 else 'FAIL'}] vanished shared ignored: expected=1 got={got}")
if got != 1:
    FAILS.append("vanished shared ignored")

try:
    _os.remove(_tmp)
except Exception:
    pass

print("RESULT:", "ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(0 if not FAILS else 1)
