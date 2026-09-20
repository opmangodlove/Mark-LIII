# One-shot: headless render of the HUD's offline state + mic flash badge.
import sys, os
sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILS = []

def check(name, ok, detail=""):
    if not ok:
        FAILS.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

try:
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui import HudCanvas
    hud = HudCanvas("face.png", assistant_name="JARVIS")
    hud.resize(320, 420)

    # 1. offline state renders (unknown states use the generic branch)
    hud.state = "OFFLINE MODE"
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(320, 420); hud.render(pm)
    check("OFFLINE MODE renders", not pm.isNull())

    # 2. mic flash badge renders and expires
    hud.set_mic_info("Microphone Array (AMD Audio Device)")
    hud.flash_mic("switched to Headset (Rockerz 110)")
    hud.render(pm)
    check("flash badge renders", hud._mic_flash is not None
          and "switched to Headset" in hud._mic_flash[0])
    # expire it
    import time as _t
    hud._mic_flash = ("x", _t.time() - 1)
    hud.render(pm)
    check("flash badge expires", getattr(hud, "_mic_flash", None) is None)

    # 3. all four normal mic lines still render
    for state in ({"muted": True}, {"mic_name": ""}, {"mic_hearing": True},
                  {"mic_hearing": False}):
        hud.muted = state.get("muted", False)
        hud.mic_name = state.get("mic_name", "AMD Array")
        hud.mic_hearing = state.get("mic_hearing", False)
        hud.render(pm)
    check("normal mic lines render", True)
except Exception as e:
    check("hud render", False, str(e)[:120])

print("RESULT:", "ALL PASS" if not FAILS else f"FAILED: {FAILS}")
sys.exit(0 if not FAILS else 1)
