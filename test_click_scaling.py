# One-shot test: every coordinate path in computer_control._rescale_xy.
# Run: .venv/Scripts/python.exe test_click_scaling.py
import sys
sys.path.insert(0, ".")

from actions import computer_control as cc

# Simulated 1920x1200 physical screen (scale: x1.5 horizontally, x1.6667 vertically)
cc._screen_size = lambda: (1920, 1200)
cc._LAST_FIND = None

FAILS = []

def case(name, got, expect):
    ok = got == expect
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: expected={expect} got={got}")

# 1. viewport -> real (the '200px off' bug)
case("mid viewport", cc._rescale_xy(640, 360), (960, 600))
case("viewport corner", cc._rescale_xy(1280, 720), (1919, 1199))
case("origin", cc._rescale_xy(0, 0), (0, 0))
case("quarter", cc._rescale_xy(320, 180), (480, 300))

# 2. already-real pixels pass through
case("real large", cc._rescale_xy(1500, 900), (1500, 900))
case("real x big, y small", cc._rescale_xy(1500, 500), (1500, 500))
case("real x small, y big", cc._rescale_xy(640, 900), (640, 900))

# 3. screen_find replay whitelist (exact + tolerance)
cc._LAST_FIND = (400, 300)
case("find exact", cc._rescale_xy(400, 300), (400, 300))
case("find +tol", cc._rescale_xy(405, 303), (405, 303))
case("find -tol", cc._rescale_xy(392, 296), (392, 296))
case("find outside tol rescales", cc._rescale_xy(420, 300), (630, 500))
cc._LAST_FIND = None

# 4. junk inputs never crash
case("floats truncate", cc._rescale_xy(500.7, 300.2), (750, 500))
case("garbage", cc._rescale_xy("abc", None), (0, 0))

# 5. tolerance only active when a find exists
case("no find -> rescale", cc._rescale_xy(400, 300), (600, 500))

print("RESULT:", "ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(0 if not FAILS else 1)
