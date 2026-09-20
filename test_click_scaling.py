# One-shot test: every coordinate path in computer_control._rescale_xy.
# Run: .venv/Scripts/python.exe test_click_scaling.py
import sys
sys.path.insert(0, ".")

from actions import computer_control as cc
from actions import screen_processor as sp

# Simulated 1920x1200 physical screen, 100% DPI (measured on this machine)
cc._screen_size = lambda: (1920, 1200)
cc._LAST_FIND = None
sp._last_sent_size = None

FAILS = []

def case(name, got, expect):
    ok = got == expect
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: expected={expect} got={got}")

# ── THE BUG: real sent size 1152x720 (1920x1200 screen, aspect preserved) ──
sp._last_sent_size = (1152, 720)
case("bug case: mid of 1152-wide view", cc._rescale_xy(640, 360), (1067, 600))
case("bug case: right edge x=1152", cc._rescale_xy(1152, 720), (1919, 1199))
case("bug case: 960,540 in view", cc._rescale_xy(960, 540), (1600, 900))
case("bug case: origin", cc._rescale_xy(0, 0), (0, 0))

# beyond the model's own image = real pixels, pass through
case("real px beyond view", cc._rescale_xy(1500, 500), (1500, 500))
case("real px y beyond", cc._rescale_xy(640, 900), (640, 900))

# ── true 16:9 screen: thumbnail IS 1280x720 ─────────────────────────────
sp._last_sent_size = (1280, 720)
case("16:9 view mid", cc._rescale_xy(640, 360), (960, 600))
case("16:9 view corner", cc._rescale_xy(1280, 720), (1919, 1199))

# ── full-res image sent (small screen): coords already real ────────────
sp._last_sent_size = (1920, 1200)
case("full-res passthrough", cc._rescale_xy(1600, 900), (1600, 900))

# ── no recorded size (old cold-start path): 1280x720 assumption ────────
sp._last_sent_size = None
case("fallback mid", cc._rescale_xy(640, 360), (960, 600))
case("fallback beyond", cc._rescale_xy(1500, 900), (1500, 900))

# ── screen_find replay whitelist (exact + tolerance) ────────────────────
sp._last_sent_size = (1152, 720)
cc._LAST_FIND = (400, 300)
case("find exact", cc._rescale_xy(400, 300), (400, 300))
case("find +tol", cc._rescale_xy(405, 303), (405, 303))
case("find outside tol rescales", cc._rescale_xy(420, 300), (700, 500))
cc._LAST_FIND = None

# ── junk inputs never crash ─────────────────────────────────────────────
case("floats", cc._rescale_xy(500.7, 300.2), (833, 500))
case("garbage", cc._rescale_xy("abc", None), (0, 0))

print("RESULT:", "ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(0 if not FAILS else 1)
