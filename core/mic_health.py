"""
core/mic_health.py — prove a microphone actually carries audio before trusting it.

WHY THIS EXISTS
    Measured on this machine: the built-in 'Microphone Array (AMD Audio Device)'
    opens successfully on every host API, at every sample rate — and delivers
    nothing but digital zeros (peak amplitude 1 on int16) even during a loud
    test beep. It is muted or wedged at the driver level, and it is the OS
    *default* input device. Every "open a stream and hope" approach therefore
    silently produces a deaf assistant: the stream works, the frames arrive,
    the audio is empty.

    A stream that opens is not a stream that hears. The only test that means
    anything is: capture briefly and check that the signal actually moves.

WHAT IT DOES
    pick_input_device() returns the sounddevice index of a mic proven to carry
    audio right now:
      1. The user's saved choice (config) — trust it if it hears anything.
      2. The OS default — trust it only if its peak level shows real signal.
      3. Otherwise every other input endpoint, best peak level wins.
    Returns None (system default) only if nothing can be verified — never
    raises, never blocks for more than a few seconds total.
"""

from __future__ import annotations

import time

# A "probe" listens this long. Long enough to catch speech through a Bluetooth
# headset's latency, short enough not to delay startup noticeably.
_PROBE_SECONDS = 1.2

# int16 peak at or below this is digital silence (±1-2 LSB of rounding noise).
# Real speech or even room noise on a live mic clears 50+ instantly.
_SILENCE_PEAK = 6

# A candidate peaking above this is unquestionably live — take it immediately
# instead of auditing every remaining endpoint (saves several seconds of
# startup; measured headsets idle well above this, dead drivers sit at 0-2).
_STRONG_SIGNAL = 1000


def _peak_level(dev_idx: int, sr: int, seconds: float) -> int | None:
    """Max int16 amplitude seen on this device, or None if it cannot open."""
    try:
        import numpy as np
        import sounddevice as sd

        peak = 0
        got_frames = 0

        def _cb(indata, frames, _t, _status):
            nonlocal peak, got_frames
            got_frames += frames
            if len(indata):
                lvl = int(np.abs(indata).max())
                if lvl > peak:
                    peak = lvl

        with sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                            blocksize=max(sr // 10, 64), device=dev_idx,
                            callback=_cb):
            time.sleep(seconds)
        # A device that opened but delivered no frames is broken too.
        if got_frames <= 0:
            return None
        return peak
    except Exception:
        return None


def _probe_rate(dev) -> int:
    """Probe at the device's native rate when possible — avoids 'invalid
    sample rate' on endpoints (WASAPI) that do not resample."""
    try:
        rate = int(dev.get("default_samplerate") or 16000)
        return rate if 8000 <= rate <= 96000 else 16000
    except Exception:
        return 16000


def pick_input_device(logger=None) -> int | None:
    """Return a sounddevice input index proven to carry audio, else None.

    Order: saved config choice → OS default → every other input endpoint.
    The result of the *best* candidate wins; ties go to the earlier candidate.
    """
    def _log(msg: str) -> None:
        print(f"[MicHealth] {msg}")
        if logger is not None:
            try:
                logger(msg)
            except Exception:
                pass

    try:
        import sounddevice as sd
    except Exception as e:
        _log(f"sounddevice unavailable: {e}")
        return None

    devices = list(sd.query_devices())
    inputs = [(i, d) for i, d in enumerate(devices)
              if d.get("max_input_channels", 0) > 0]
    if not inputs:
        return None

    # Candidates in priority order: saved choice, then OS default, then rest.
    saved_name = ""
    try:
        from memory.config_manager import get_input_device
        saved_name = (get_input_device() or "").strip()
    except Exception:
        pass

    default_in = None
    try:
        default_in = sd.default.device[0]
    except Exception:
        pass

    def _name_matches(idx: int) -> bool:
        if not saved_name or saved_name == "System default":
            return False
        return saved_name.lower()[:24] in (devices[idx].get("name") or "").lower() \
            or (devices[idx].get("name") or "").lower()[:24] in saved_name.lower()

    priority = [i for i, _ in inputs if _name_matches(i)]
    priority += [i for i, _ in inputs if i == default_in and i not in priority]
    priority += [i for i, _ in inputs if i not in priority]

    best_idx, best_peak = None, -1
    deadline = time.monotonic() + 12.0   # overall budget
    for idx in priority:
        if time.monotonic() > deadline:
            break
        d = devices[idx]
        rate = _probe_rate(d)
        peak = _peak_level(idx, rate, _PROBE_SECONDS)
        name = (d.get("name") or "")[:44]
        if peak is None:
            _log(f"skip '{name}' (cannot open @{rate})")
            continue
        tag = ""
        if peak <= _SILENCE_PEAK:
            tag = "  (digital silence — driver muted/dead)"
        elif idx == best_idx:
            tag = ""
        _log(f"'{name}' @{rate}: peak={peak}{tag}")
        if peak > best_peak:
            best_idx, best_peak = idx, peak
        # A verified-hearing device in the priority slots wins immediately —
        # no need to audit every other endpoint.
        if peak > _SILENCE_PEAK and (idx == default_in or _name_matches(idx)):
            break
        # An unquestionably live signal anywhere also ends the audit —
        # nothing later can beat "actually hears speech" by enough to
        # justify the extra seconds.
        if best_peak > _STRONG_SIGNAL:
            break

    if best_idx is not None and best_peak > _SILENCE_PEAK:
        _log(f"selected input #{best_idx} "
             f"'{(devices[best_idx].get('name') or '')[:44]}' (peak {best_peak})")
        return best_idx

    _log("no microphone proven to carry audio — falling back to system default")
    return None
