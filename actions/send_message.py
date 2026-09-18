import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.06
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

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

# ── Window handling ───────────────────────────────────────────────────────────
# Patch: the old flow always launched the app blindly through Start-Menu
# keystrokes (≈5 s) even when it was already open, then pasted straight into
# whatever had keyboard focus — which after a chat search is the SEARCH BOX,
# not the message box. Both problems are fixed below:
#   1. _ensure_window focuses an already-running app (sub-second) or launches
#      it directly (WhatsApp URI / battle-tested open_app launcher).
#   2. _focus_message_box moves focus into the composer before typing, using
#      AI screen-find with a keyboard-only fallback.

_PROC_HINTS = {
    "whatsapp": ("WhatsApp.exe",),
    "telegram": ("Telegram.exe",),
    "discord":  ("Discord.exe",),
    "signal":   ("Signal.exe",),
}

def _is_running(proc_names) -> bool:
    if not _PSUTIL:
        return False
    targets = {n.lower() for n in proc_names}
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in targets:
                return True
        except Exception:
            continue
    return False


def _focus_existing(title: str) -> bool:
    try:
        from actions.computer_control import _focus_window
        _focus_window(title)
        return True
    except Exception:
        return False


def _ensure_window(app_key: str) -> bool:
    """Focus the app if it is already running; otherwise launch it.

    Returns False only when we had no way to bring the app up at all.
    """
    procs = _PROC_HINTS.get(app_key, ())
    title = app_key.title()

    if procs and _is_running(procs):
        _focus_existing(title)
        time.sleep(0.8)
        return True

    if app_key == "whatsapp" and _get_os() == "windows":
        # Direct URI launch — instant, no Start-Menu round-trip. If the app is
        # registered, this either starts it or surfaces the existing window.
        try:
            os.startfile("whatsapp:")  # noqa: S606 — fixed, trusted URI
            time.sleep(2.0)
            return True
        except Exception as e:
            print(f"[SendMessage] whatsapp: URI failed ({e}) — falling back")

    # Generic launch through open_app's launcher (aliases, PATH, Start-Menu).
    try:
        from actions.open_app import open_app as _open_tool
        result = _open_tool({"app_name": title})
        ok = "opened" in result.lower() or "may still be loading" in result.lower()
        if ok:
            return True
    except Exception as e:
        print(f"[SendMessage] open_app launch failed: {e}")

    return _open_app(title)  # legacy blind Start-Menu fallback


def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else:
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0)
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.4)
    _clear_and_paste(query)
    time.sleep(1.0)


def _focus_message_box() -> None:
    """Move keyboard focus into the message input box after a chat opens.

    After Ctrl+F search + Enter selects a chat, keyboard focus stays in the
    chat list / search field in most desktop messengers — typing then goes
    nowhere (or into search). Click the composer via AI screen-find; if that
    is unavailable, fall back to the keyboard escape hatch.
    """
    _require_pyautogui()
    try:
        from actions.computer_control import _screen_find, _click
        coords = _screen_find(
            "the message input box at the bottom of the chat conversation, "
            "where text is typed before sending"
        )
        if coords:
            _click(x=coords[0], y=coords[1])
            time.sleep(0.3)
            return
        print("[SendMessage] screen_find: composer not located — keyboard fallback")
    except Exception as e:
        print(f"[SendMessage] screen_find unavailable ({e}) — keyboard fallback")

    # Keyboard fallback: leave the search field, then re-enter the composer.
    pyautogui.press("esc")
    time.sleep(0.3)
    pyautogui.press("esc")
    time.sleep(0.4)


def _desktop_send(app_key: str, receiver: str, message: str) -> str:
    if not _ensure_window(app_key):
        return f"Could not open {app_key.title()}."

    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _focus_message_box()

    _paste_text(message)
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.2)
    return f"Message sent to {receiver} via {app_key.title()}."

def _send_whatsapp(receiver: str, message: str) -> str:
    return _desktop_send("whatsapp", receiver, message)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return "Could not open Instagram in browser."

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Instagram."


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return "Could not open Messenger in browser."


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return f"Message sent to {receiver} via Messenger."

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                              _send_signal),
    ({"discord"},                             _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(key, r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return "Please specify a recipient."
    if not message_text:
        return "Please specify the message content."
    if not _PYAUTOGUI:
        return "PyAutoGUI is not installed — cannot control the desktop."

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")

    try:
        handler = _resolve_platform(platform)
        result  = handler(receiver, message_text)
    except Exception as e:
        result = f"Could not send message: {e}"

    print(f"[SendMessage] {'✅' if 'sent' in result.lower() else '❌'} {result}")
    if player:
        player.write_log(f"[msg] {result}")

    return result


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp, Telegram, etc."
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}
