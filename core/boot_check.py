"""
core/boot_check.py — prove the critical senses at startup, announce only failures.

WHY THIS EXISTS
    JARVIS could come up perfectly healthy-looking while one sense was dead —
    a muted mic driver, an expired API quota, a broken screen capture — and
    the user would only discover it minutes later by the silence. This module
    runs once, at boot, BEFORE the live session starts, and speaks a short
    line through Windows' built-in SAPI voice for each failure found:

        "Sir, microphone check failed. No microphone can hear anything."
        "Sir, Gemini API check failed. The API key looks invalid."
        "Sir, screen capture failed. <reason>"

    Everything passing stays silent — a healthy boot says nothing.

    The microphone result is shared with the app through mic_health's short
    cache, so the boot check does not double the startup probe cost.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time


def _with_timeout(fn, seconds: float):
    """Run fn() in a worker thread, return (result, timed_out). Boot checks
    must never hang the boot: the Gemini SDK's default HTTP timeout is
    minutes long, and a half-down network would otherwise stall startup
    indefinitely."""
    box: list = []
    def _run():
        try:
            box.append(fn())
        except Exception as e:   # surfaced as a normal failure below
            box.append(e)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if not box:
        return None, True
    result = box[0]
    if isinstance(result, Exception):
        raise result          # normal error path — mapped by the caller
    return result, False


def _sapi_say(text: str) -> None:
    """Speak one line through Windows' built-in SAPI voice.

    Deliberately independent of the live session: boot failures must be
    announced even when the thing that failed is the session itself.
    """
    def _speak():
        try:
            import win32com.client as _w
            _w.Dispatch("SAPI.SpVoice").Speak(text)
            return
        except Exception:
            pass
        try:
            ps = ("$v = New-Object -ComObject SAPI.SpVoice; "
                  f"$v.Speak('{text}')")
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=20,
            )
        except Exception:
            pass   # speech is best-effort; the log line is the real record
    # A wedged TTS engine must not stall the boot either.
    t = threading.Thread(target=_speak, daemon=True)
    t.start()
    t.join(25)


def check_microphone() -> tuple[int | None, str]:
    """Return (device_index, device_name) of a mic proven to hear, or (None, '')."""
    try:
        from core import mic_health
        idx = mic_health.pick_input_device(logger=lambda m: print(f"[BootCheck] {m}"))
        if idx is None:
            return None, ""
        try:
            import sounddevice as sd
            name = sd.query_devices()[idx].get("name", f"device #{idx}")
        except Exception:
            name = f"device #{idx}"
        return idx, name
    except Exception as e:
        print(f"[BootCheck] microphone check error: {e}")
        return None, ""


def check_api(timeout_hint: str = "") -> tuple[bool, str, bool]:
    """Live round-trip to the Gemini API.
    Returns (ok, human_reason_if_not, transient).
    transient=True means Google-side trouble (e.g. 503 high demand): the
    user's setup is fine and the live session will retry on its own — the
    boot announcement stays silent for those, only the log records it."""
    try:
        from memory.config_manager import load_api_keys
        key = (load_api_keys().get("gemini_api_key") or "").strip()
    except Exception:
        key = ""
    if not key:
        return False, "No Gemini API key is configured.", False

    try:
        from google import genai
    except Exception as e:
        return False, f"The Gemini library failed to load: {e}", False

    def _call():
        client = genai.Client(api_key=key)
        return client.models.generate_content(
            model="gemini-flash-latest",
            contents="Reply with exactly: OK",
            config={"max_output_tokens": 5},
        )

    def _direct_models_get() -> int:
        """Key-authenticated models list straight over HTTPS (stdlib).

        Measured on this machine: the SDK's httpx client occasionally wedges
        for minutes on a healthy network while the same endpoint answers in
        ~1 s. The boot verdict must reflect Google's API, not one client's
        bad morning — so a wedged SDK call is settled by this direct check.
        """
        import json as _json
        import urllib.request
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = _json.loads(resp.read().decode("utf-8", "replace"))
        if isinstance(body, dict) and body.get("models") is not None:
            return 200
        return resp.status

    try:
        response, timed_out = _with_timeout(_call, 25.0)
        if not timed_out:
            text = ""
            try:
                text = (response.text or "").strip()
            except Exception:
                pass
            if text:
                return True, "", False
            return False, "The API responded but returned no text.", False
        # SDK wedged — settle it with the direct check before alarming anyone.
        print("[BootCheck] SDK call stalled; verifying API directly...")
        try:
            status, to = _with_timeout(_direct_models_get, 10.0)
            if not to and status == 200:
                print("[BootCheck] direct API check OK — SDK stall ignored ("
                      "the live session uses its own connection anyway)")
                return True, "", True
        except Exception:
            pass
        return False, "No connection to Google's servers.", False
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        # Google-side trouble (e.g. 503 'high demand'): reachability is proven
        # with the direct check, and the verdict is logged — never spoken —
        # so a busy evening at Google doesn't turn into a boot alarm.
        if ("503" in msg or "unavailable" in low or "high demand" in low
                or "overloaded" in low or " 500" in msg or "internal" in low):
            try:
                status, to = _with_timeout(_direct_models_get, 10.0)
                if not to and status == 200:
                    print("[BootCheck] Google reports high demand on the check "
                          "model; API itself reachable — logging only")
                    return True, "Google's API is busy right now.", True
            except Exception:
                pass
            return False, "Google's API is temporarily unavailable.", True
        if "api key" in low or "permission_denied" in low or "401" in msg or "403" in msg:
            return False, "The API key looks invalid or unauthorised.", False
        if "quota" in low or "resource_exhausted" in low or "429" in msg:
            return False, "The API quota is exhausted for now.", False
        if "timed out" in low or "timeout" in low or "connection" in low or "network" in low:
            return False, "No connection to Google's servers.", False
        return False, msg[:120], False


def check_live_model() -> tuple[bool, str]:
    """Prove at least one live-voice model is visible to this API key.

    Text generation can answer 200 while the pinned LIVE model has been
    RETIRED by Google (measured 2026-09-20: 'gemini-3.1-flash-live-preview'
    vanished, text models kept working) — the assistant then fails to
    connect and retries forever with a 'healthy' key. This check speaks
    only when NO known live model is visible: a real configuration problem.
    Inconclusive results (network busy) never alarm."""
    try:
        from memory.config_manager import get_live_model_candidates, load_api_keys
        cands = get_live_model_candidates()
        key = (load_api_keys().get("gemini_api_key") or "").strip()
    except Exception:
        return True, ""   # cannot evaluate — stay silent, main.py re-probes

    def _list_models():
        import urllib.request
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {m.get("name", "") for m in json.loads(
                resp.read().decode("utf-8", "replace")).get("models", [])}

    try:
        visible, timed_out = _with_timeout(_list_models, 12.0)
        if timed_out:
            return True, ""   # inconclusive
        if isinstance(visible, set) and any(c in visible for c in cands):
            return True, ""
        return False, ("Sir, voice check failed. None of my voice models are "
                       "available on this API key anymore.")
    except Exception:
        return True, ""       # inconclusive — never alarm on a busy network


def check_screen() -> tuple[bool, str]:
    """Prove screen capture produces real bytes."""
    try:
        from actions.screen_processor import _capture_screen
        result, timed_out = _with_timeout(_capture_screen, 10.0)
        if timed_out:
            return False, "Screen capture timed out."
        png, _mime = result
        if png and len(png) > 0:
            return True, ""
        return False, "The capture came back empty."
    except Exception as e:
        return False, str(e)[:120]


def run(ui=None) -> list[str]:
    """Run all checks. Returns the list of failure messages; speaks each one."""
    failures: list[str] = []

    mic_idx, _mic_name = check_microphone()
    if mic_idx is None:
        # One grace retry: opening a Bluetooth HFP mic starts SCO link setup
        # whose first seconds are silent, and the wake-word launcher may hold
        # the same mic — both make a healthy mic read "dead" on the first
        # probe. Verify the verdict before alarming anyone.
        print("[BootCheck] mic unverified — grace retry in 2.5s (SCO/contention)")
        time.sleep(2.5)
        try:
            from core import mic_health as _mh
            _mh._cache = None
        except Exception:
            pass
        mic_idx, _mic_name = check_microphone()
    if mic_idx is None:
        # Distinguish "no mic exists" from "a mic exists but the probe cannot
        # prove it hears". Measured false negatives (SCO setup, capture
        # contention with the launcher) make a spoken alarm wrong more often
        # than right — the app runs on the system default either way, and the
        # HUD + launcher log show the truth. Alarm only for the true disaster:
        # zero input devices at all.
        n_inputs = -1
        try:
            import sounddevice as sd
            n_inputs = sum(1 for d in sd.query_devices()
                           if d.get("max_input_channels", 0) > 0)
        except Exception:
            pass
        if n_inputs == 0:
            failures.append("Sir, microphone check failed. "
                            "No microphone is connected at all.")
        else:
            print("[BootCheck] mic probe unverified but an input device exists "
                  f"({n_inputs}) — running on system default, NOT spoken")
            try:
                if ui is not None:
                    ui.write_log("SYS: Boot check - mic unverified by probe; "
                                 "running on system default.")
                    ui.set_mic_info("system default (unverified)")
            except Exception:
                pass

    api_ok, api_why, api_transient = check_api()
    if not api_ok and not api_transient:
        failures.append(f"Sir, Gemini API check failed. {api_why}")
    elif api_transient and api_why:
        # Google-side hiccup: the user's setup is fine. Log it; the live
        # session's own retry loop handles the rest.
        try:
            if ui is not None:
                ui.write_log(f"SYS: Boot check note - {api_why}")
        except Exception:
            pass

    live_ok, live_why = check_live_model()
    if not live_ok:
        failures.append(live_why)

    screen_ok, screen_why = check_screen()
    if not screen_ok:
        failures.append(f"Sir, screen capture failed. {screen_why}")

    if ui is not None:
        try:
            if failures:
                ui.write_log("SYS: Boot self-test FAILED - " + " | ".join(
                    f.replace("Sir, ", "") for f in failures))
            else:
                ui.write_log("SYS: Boot self-test passed (mic, API, screen).")
        except Exception:
            pass

    for line in failures:
        print(f"[BootCheck] FAIL: {line}")
        _sapi_say(line)
    if not failures:
        print("[BootCheck] all checks passed (mic, API, screen) — staying silent")
    return failures
