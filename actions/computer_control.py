#computer_control.py
import io
import json
import platform
import re
import string
import subprocess
import sys

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}
import time
import random
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.01   # measured default 0.05 adds ~30% to every move/click
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

# Patch: the vision loop sees the screen as a downscaled image (max 1280x720,
# see screen_processor._IMG_MAX_W/_H). When the model answers 'click x=300
# y=200' it means 'the pixel at 300,200 in the image I was shown' — executing
# those numbers as real screen coordinates lands the click ~1.67x off on a
# 1920-wide display, which is exactly the 'it presses somewhere else' bug. Any
# raw coordinate pair at or below the viewport bounds is therefore treated as
# viewport pixels and scaled up to the real screen before touching the mouse.
# Real-pixel callers (screen_find scaling, drag endpoints from screen_find)
# are unaffected: they exceed the 720p viewport on any full-HD-or-larger
# display. An actual 720p screen would rescale by 1.0 and change nothing.
# Coordinates that exactly match the most recent screen_find result are also
# passed through untouched — those are known-real pixels being replayed.
_LAST_FIND: tuple[int, int] | None = None
_FIND_TOL = 8   # px tolerance when replaying a screen_find result
try:
    from actions import screen_processor as _sp
    _VIEW_MAX_W = int(getattr(_sp, "_IMG_MAX_W", 1280))
    _VIEW_MAX_H = int(getattr(_sp, "_IMG_MAX_H", 720))
except Exception:                                    # pragma: no cover
    _VIEW_MAX_W, _VIEW_MAX_H = 1280, 720


def _screen_size() -> tuple[int, int]:
    """Physical screen size, used to scale viewport coordinates up."""
    try:
        _require_pyautogui()
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        return 0, 0


def _rescale_xy(x, y) -> tuple[int, int]:
    """Map a coordinate pair from the model's view onto the real screen.

    The model answers in pixels of the LAST screen image it was shown. That
    image is thumbnail()-ed with aspect ratio preserved, so it is NOT
    necessarily 1280x720: on a 1920x1200 screen it is 1152x720, and assuming
    1280 put every x ~11% left (up to 192px off — the reported bug). The real
    sent size (recorded by screen_processor) is used when available; pairs
    beyond the model's own image must be real pixels (screen_find results)
    and pass through, as do pairs within a few px of the latest screen_find
    result."""
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError):
        return 0, 0
    if _LAST_FIND is not None:
        lx, ly = _LAST_FIND
        if abs(xi - lx) <= _FIND_TOL and abs(yi - ly) <= _FIND_TOL:
            return xi, yi
    sw, sh = _screen_size()
    if sw <= 0 or sh <= 0:
        return xi, yi
    sent_w = sent_h = 0
    try:
        sent = getattr(_sp, "_last_sent_size", None)
        if sent and sent[0] > 0 and sent[1] > 0:
            sent_w, sent_h = int(sent[0]), int(sent[1])
    except Exception:
        pass
    if sent_w <= 0 or sent_h <= 0:
        sent_w, sent_h = _VIEW_MAX_W, _VIEW_MAX_H
    if sent_w == sw and sent_h == sh:
        return xi, yi   # full-res image was sent — coords are already real
    if xi > sent_w or yi > sent_h:
        return xi, yi   # beyond the model's own image — must be real pixels
    return min(max(round(xi * sw / sent_w), 0), sw - 1), \
           min(max(round(yi * sh / sent_h), 0), sh - 1)

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _platform_os() -> str:
    return {"Windows": "windows", "Darwin": "mac", "Linux": "linux"}.get(
        platform.system(), "linux"
    )

def _get_os() -> str:
    return _load_config().get("os_system", _platform_os()).lower()


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "")

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        rx, ry = _rescale_xy(x, y)
        pyautogui.click(rx, ry, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({rx}, {ry}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    rx, ry = _rescale_xy(x, y)
    pyautogui.moveTo(rx, ry, duration=duration)
    return f"Mouse → ({rx}, {ry})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    rx1, ry1 = _rescale_xy(x1, y1)
    rx2, ry2 = _rescale_xy(x2, y2)
    pyautogui.moveTo(rx1, ry1, duration=0.2)
    pyautogui.dragTo(rx2, ry2, duration=duration, button="left")
    return f"Dragged ({rx1},{ry1}) → ({rx2},{ry2})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        paste_key = "command" if _get_os() == "mac" else "ctrl"
        pyautogui.hotkey(paste_key, "v")
        return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    select_key = "command" if _get_os() == "mac" else "ctrl"
    pyautogui.hotkey(select_key, "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5, **_WIN_HIDE,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                return f"Focused window: {title}"
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"

# Cached client: building a fresh genai.Client on every screen_find added
# latency to every single click task. The key rarely changes — rebuild only
# when it does.
_find_client = None
_find_client_key = ""


def _get_find_client():
    global _find_client, _find_client_key, _find_memo, _find_memo_ts, _find_memo_result
    key = _get_api_key()
    if _find_client is None or _find_client_key != key:
        from google import genai
        _find_client = genai.Client(api_key=key)
        _find_client_key = key
    return _find_client


# Memo cache for identical screen_find calls within 5 s (see _screen_find).
_find_memo: str | None        = None
_find_memo_ts: float          = 0.0
_find_memo_result: tuple | None = None


def _screen_find(description: str) -> tuple[int, int] | None:
    # 5-second memo: identical descriptions back-to-back (model calls
    # screen_find then screen_click with the same text) reuse the result
    # instead of a second 1.5-2 s vision round-trip. Screen changes clear it.
    global _find_memo, _find_memo_ts, _find_memo_result
    now = time.monotonic()
    if (description == _find_memo
            and (now - _find_memo_ts) < 5.0):
        print("[ComputerControl] screen_find memo hit")
        return _find_memo_result

    api_key = _get_api_key()
    if not api_key:
        print("[ComputerControl] ⚠️ No API key for screen_find")
        return None

    try:
        from google.genai import types as gtypes

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        # Downscale + JPEG: the old full-res PNG was ~2.4 MB and dominated
        # every screen_find — measured 6-8 s per call, which made single
        # click tasks take ~30 s. A 1024 px JPEG is ~70 KB (33x smaller) and
        # far more than enough for 0-1000 normalized coordinates.
        try:
            small = img.convert("RGB")
            small.thumbnail((1024, 1024))
            buf = io.BytesIO()
            small.save(buf, format="JPEG", quality=80)
            image_bytes = buf.getvalue()
            mime = "image/jpeg"
        except Exception:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            mime = "image/png"

        client = _get_find_client()
        # Patch: ask for NORMALIZED 0-1000 coordinates explicitly, then scale
        # deterministically. Previously the model chose its own units — some
        # replies were raw pixels, some 0-1000 normalized — and the ambiguity
        # made clicks land ~200px off on 1920-wide screens (the >1920 heuristic
        # never fired there). A fixed output unit removes the guesswork.
        prompt = (
            "Locate the UI element described as: '" + description + "'. "
            "Reply with ONLY the center coordinates of the element as: x,y "
            "where x and y are integers between 0 and 1000, normalized to the "
            "image size (top-left corner is 0,0, the bottom-right is 1000,1000; "
            "the exact center of the image is 500,500). "
            "If the element is not visible, reply: NOT_FOUND"
        )

        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=[
                gtypes.Part.from_bytes(data=image_bytes, mime_type=mime),
                prompt,
            ],
        )

        text = (response.text or "").strip()
        if "NOT_FOUND" in text.upper():
            return None

        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            x, y = int(match.group(1)), int(match.group(2))
            if x > 1000 or y > 1000:
                # Model ignored the normalized format but may have replied
                # REAL pixels (its other measured habit). Accept them when
                # they fit on the screen — an exact real-pixel click beats
                # refusing — and reject only what would land off-screen.
                if x < w and y < h:
                    px, py = x, y
                else:
                    print(f"[ComputerControl] screen_find ignored "
                          f"out-of-range reply: {text[:40]!r}")
                    return None
            else:
                px = min(max(round(x * w / 1000), 0), w - 1)
                py = min(max(round(y * h / 1000), 0), h - 1)
            # Remember the real-pixel result so a raw click replaying these
            # exact numbers is never misread as viewport pixels.
            global _LAST_FIND
            _LAST_FIND = (px, py)
            _find_memo, _find_memo_ts = description, now
            _find_memo_result = (px, py)
            return px, py

    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")

    return None

def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      random_data   — generate fake form data
      user_data     — pull real data from memory
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            # Phrase it as real pixels so the model treats the numbers as
            # screen coordinates, not viewport coordinates.
            return (f"{coords[0]},{coords[1]} (real screen pixels — pass these "
                    f"exact numbers to click)") if coords else "NOT_FOUND"

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if coords:
                time.sleep(0.2)
                # Real pixels straight from screen_find — skip viewport
                # rescaling by clicking through pyautogui directly.
                pyautogui.click(coords[0], coords[1])
                return f"Clicked '{desc}' at {coords}"
            return f"Element not found on screen: '{desc}'"

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "computer_control",
    "description": "See the screen and act on what is visible: look at the screen (screenshot), find a described element and get its exact position (screen_find), click it (screen_click), click raw coordinates, double/right-click, drag, scroll, move the mouse, hotkeys, press keys, copy/paste, type into a focused field, wait, clear a field, focus a window. Use when a task needs the CURRENT screen state — clicking a specific button, filling a specific field, anything inside an app window after it is open. One screen look per step: use screen_find → screen_click with the SAME element description. NOT for simple one-step commands (computer_settings), NOT for browsers (browser_control), NOT for launching apps (open_app).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"
            },
            "text": {
                "type": "STRING",
                "description": "Text to type or paste"
            },
            "x": {
                "type": "INTEGER",
                "description": "X coordinate"
            },
            "y": {
                "type": "INTEGER",
                "description": "Y coordinate"
            },
            "keys": {
                "type": "STRING",
                "description": "Key combination e.g. 'ctrl+c'"
            },
            "key": {
                "type": "STRING",
                "description": "Single key e.g. 'enter'"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down | left | right"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount (default: 3)"
            },
            "seconds": {
                "type": "NUMBER",
                "description": "Seconds to wait"
            },
            "title": {
                "type": "STRING",
                "description": "Window title for focus_window"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for screen_find/screen_click"
            },
            "type": {
                "type": "STRING",
                "description": "Data type for random_data"
            },
            "field": {
                "type": "STRING",
                "description": "Field for user_data: name|email|city"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": computer_control,
}
