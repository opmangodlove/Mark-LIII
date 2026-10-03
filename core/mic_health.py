"""
core/mic_health.py — prove a microphone actually carries audio before trusting it.

WHY THIS EXISTS
    Measured on this machine: the built-in 'Microphone Array (AMD Audio Device)'
    opened successfully on every host API, at every sample rate — and delivered
    nothing but digital zeros (peak amplitude 1 on int16) even during a loud
    test beep. The cause turned out to be an endpoint MUTE that Windows keeps
    re-applying across reboots (unmuted by hand, muted again the next boot).
    Every "open a stream and hope" approach therefore silently produces a deaf
    assistant: the stream works, the frames arrive, the audio is empty.

    A stream that opens is not a stream that hears. The only test that means
    anything is: capture briefly and check that the signal actually moves.

WHAT IT DOES
    pick_input_device() returns the sounddevice index of a mic proven to carry
    audio right now:
      0. Heal the endpoints: unmute every input and lift zeroed levels once
         per boot (the measured root cause of "digital silence" mics).
      1. The user's saved choice (config) — trust it if it hears anything.
      2. A mic preference (config: bluetooth / builtin / auto) reorders the
         candidates; the default preference is Bluetooth-when-connected,
         built-in otherwise.
      3. The OS default — trust it only if its peak level shows real signal.
      4. Otherwise every other input endpoint, best peak level wins.
    Returns None (system default) only if nothing can be verified — never
    raises, never blocks for more than a few seconds total.

    Bluetooth microphones get a longer probe with one retry: opening the
    headset's HFP mic starts a SCO link whose first moments are silent, so a
    short probe misreads a healthy headset as dead.

SANDBOX (critical, measured)
    Opening/closing many short capture streams while a COM audio-endpoint
    session is active can SEGFAULT the process natively (reproduced 1-in-3
    on this machine during testing). The whole probe — endpoint healing
    (COM/pycaw) plus the capture streams (PortAudio) — therefore runs in a
    SHORT-LIVED CHILD PROCESS: if it ever crashes, the parent returns None
    (system default) and JARVIS keeps running. The assistant itself only
    ever opens its one long-lived mic stream in its own process.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# A "probe" listens this long. Long enough to catch speech through a Bluetooth
# headset's latency, short enough not to delay startup noticeably.
_PROBE_SECONDS = 1.2

# Bluetooth needs longer: opening the HFP mic triggers SCO link setup whose
# first ~1-2 s can deliver pure silence even on a perfectly healthy headset.
_PROBE_SECONDS_BT = 2.6

# int16 peak at or below this is digital silence (±1-2 LSB of rounding noise).
# Real speech or even room noise on a live mic clears 50+ instantly.
_SILENCE_PEAK = 6

# Very short cache: the boot self-test and main.py's own mic selection run
# seconds apart — probing every endpoint twice delays startup for nothing.
# Deliberately NOT a long cache: a headset can power off within a minute and
# change which device is the right one.
_cache: tuple[float, int | None] | None = None
_CACHE_TTL = 5.0

# The last mic that PROVED it hears. Checked first on every audit with a short
# live probe — if it still hears, the full endpoint sweep is skipped. This
# makes re-audits fast AND shields the selection from false negatives on the
# other endpoints (a device that heard yesterday almost certainly still works;
# the sweep is only re-run when it stops hearing).
_known_good: str | None = None

# Name of the mic chosen by the most recent probe (read by the sandbox child
# to report the selection back to the parent alongside the index).
_last_selected_name: str = ""

# Devices whose names contain any of these are treated as Bluetooth (the
# user's headset). A preferred device still has to PROVE it carries audio —
# preference never overrides deafness.
_BT_HINTS = ("headset", "headphone", "bluetooth", "rockerz", "airpod",
             "buds", "galaxy buds", "wh-", "jbl", "boat")

# Endpoint healing runs once per process (cheap, and its effect persists in
# the audio driver until Windows changes its mind again).
_endpoints_healed = False


# Driver-install artifacts / virtual loopbacks that sometimes leak small
# noise peaks — technically "signal", but not a real microphone. They are
# pushed to the very END of the audit order; still usable as a last resort.
# NOTE: "afdinstall" (AMDAfdInstall Wave Microphone) is spelled out because
# "afinstall" does NOT substring-match it — measured: that single missing
# letter let a loopback record itself as the shared mic for a whole day.
_JUNK_HINTS = ("afdinstall", "afinstall", "virtual", "stereo mix", "loopback")


def _is_junk_name(name: str) -> bool:
    """True for driver-install artifacts / virtual loopbacks.

    Measured live: 'AMDAfdInstall Wave Microphone - 1' got RECORDED as the
    shared mic during a mute episode (it always 'hears' — it loops system
    audio) and then queue-jumped every future audit via shared-mic adoption,
    poisoning device selection for a whole day. Junk must never be adopted
    or recorded as a proven mic."""
    n = (name or "").lower()
    return any(h in n for h in _JUNK_HINTS)


def _default_output_is_bt() -> bool:
    """True when the OS default OUTPUT device is a Bluetooth sink.

    Bluetooth headsets run A2DP (hi-fi, mic closed) or HFP (phone-call
    quality, mic open) — opening the headset's MICROPHONE forces the whole
    headset into call mode and every system sound degrades until it closes.
    While the user is LISTENING through the headset, capture should therefore
    prefer a non-Bluetooth mic; the BT mic is only preferred for pickup when
    it is not the active listening device."""
    try:
        import sounddevice as sd
        idx = sd.default.device[1]
        if idx is None or idx < 0:
            return False
        return _is_bluetooth(sd.query_devices()[idx].get("name") or "")
    except Exception:
        return False


def current_signature() -> str:
    """Fingerprint of the current input-device list (index, name, channels).

    Callers compare this over time: a change means a mic was plugged in,
    powered off or disconnected — the moment to re-audit and switch devices
    (Bluetooth headset connecting or dying is exactly such a change)."""
    try:
        import sounddevice as sd
        parts = []
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                parts.append(f"{i}:{d.get('name', '')[:40]}:"
                             f"{d.get('max_input_channels')}")
        return "|".join(parts)
    except Exception:
        return ""


def _is_bluetooth(name: str) -> bool:
    n = (name or "").lower()
    return any(h in n for h in _BT_HINTS)


# ── Endpoint healing ─────────────────────────────────────────────────────────
# Windows persists a per-endpoint mute across reboots. That single flag turned
# the built-in mic array into a digital-zero generator for days, and came back
# even after being cleared by hand. So the app now clears it itself: every
# CAPTURE endpoint that shares a name with a sounddevice input device is
# unmuted and, if its level sits at zero, lifted to 70%. Output endpoints are
# left completely alone ("Headphones" ≠ "Headset" — the name match cannot
# cross over).
def _ensure_endpoints_healthy(logger=None) -> None:
    global _endpoints_healed
    if _endpoints_healed:
        return
    _endpoints_healed = True

    def _log(msg: str) -> None:
        print(f"[MicHealth] {msg}")
        if logger is not None:
            try:
                logger(msg)
            except Exception:
                pass

    try:
        import sounddevice as sd
        in_names = []
        for d in sd.query_devices():
            if d.get("max_input_channels", 0) > 0:
                n = (d.get("name") or "").strip().lower()
                if len(n) >= 8:
                    in_names.append(n)
    except Exception:
        return
    if not in_names:
        return

    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        try:
            comtypes.CoInitialize()
        except Exception:
            pass
    except Exception as e:
        _log(f"endpoint heal skipped (pycaw unavailable: {e})")
        return

    healed = 0
    try:
        for dev in AudioUtilities.GetAllDevices():
            try:
                fname = (getattr(dev, "FriendlyName", "") or "").strip().lower()
                if len(fname) < 8:
                    continue
                # Input-endpoint test: substring match against a real input
                # device name, in either direction (MME truncates names to 31
                # chars, so the endpoint may be the LONGER of the two).
                if not any(fname in n or n in fname for n in in_names):
                    continue
                iface = dev._dev.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                vol = cast(iface, POINTER(IAudioEndpointVolume))
                if vol.GetMute():
                    vol.SetMute(False, None)
                    healed += 1
                    _log(f"unmuted input endpoint '{fname[:40]}' "
                         "(Windows had muted it)")
                if vol.GetMasterVolumeLevelScalar() < 0.05:
                    vol.SetMasterVolumeLevelScalar(0.7, None)
                    healed += 1
                    _log(f"raised zeroed input endpoint '{fname[:40]}' to 70%")
            except Exception:
                continue
    except Exception as e:
        _log(f"endpoint heal error (non-fatal): {e}")
    if healed:
        _log(f"endpoint heal: {healed} fix(es) applied — re-probing will see them")


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


def _probe_device(idx: int, devices, logger=None) -> int | None:
    """Peak level of one device. Bluetooth mics get the long probe and up to
    THREE attempts with settle gaps: SCO link setup, and driver contention
    with the other JARVIS process's live stream, can hold the first moments
    of a perfectly healthy headset silent."""
    d = devices[idx]
    rate = _probe_rate(d)
    bt = _is_bluetooth(d.get("name") or "")
    seconds = _PROBE_SECONDS_BT if bt else _PROBE_SECONDS
    peak = _peak_level(idx, rate, seconds)
    # Retries only make sense when the device OPENS but reads silence (slow
    # SCO start). Open failures (None) never improve by retrying.
    if bt and peak is not None and peak <= _SILENCE_PEAK:
        for _ in range(2):   # two retries → three attempts total
            if logger is not None:
                try:
                    logger(f"bluetooth probe silent @ {rate} — "
                           "waiting 1.0s and retrying")
                except Exception:
                    pass
            time.sleep(1.0)
            peak2 = _peak_level(idx, rate, seconds)
            if peak2 is not None and peak2 > peak:
                peak = peak2
            if peak is not None and peak > _SILENCE_PEAK:
                break
    return peak


def pick_input_device_inprocess(logger=None,
                                known_good: str | None = None) -> int | None:
    """Return a sounddevice input index proven to carry audio, else None.

    Order: endpoint heal → known-good mic → saved config choice → mic
    preference (bluetooth/builtin) → OS default → every other input endpoint.
    Proof of signal still decides: a connected-but-deaf headset is skipped and
    the built-in mic wins even though the headset ranks higher.

    Runs NATIVE code (COM endpoint healing + many short PortAudio capture
    streams) — call it through pick_input_device(), which sandboxes this in a
    child process; calling it directly risks a native crash taking the app
    down (reproduced 1-in-3 during testing).
    """
    global _cache, _last_selected_name
    _now = time.monotonic()
    if _cache is not None and (_now - _cache[0]) < _CACHE_TTL:
        return _cache[1]

    def _log(msg: str) -> None:
        print(f"[MicHealth] {msg}")
        if logger is not None:
            try:
                logger(msg)
            except Exception:
                pass

    # One-time-per-boot self-heal: a muted/zeroed endpoint reads as digital
    # silence no matter how good the probe is. Fix the cause, not the symptom.
    _ensure_endpoints_healthy(logger)

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

    def _rank(i: int) -> int:
        # Prefer WASAPI copies: measured, the MME/DirectSound twins of the
        # same headset hear far worse (peak 18000+ vs 170 on this machine).
        try:
            ha = sd.query_hostapis()[devices[i].get("hostapi")].get("name", "")
            return 2 if "wasapi" in ha.lower() else 1
        except Exception:
            return 1

    # The known-good mic is re-verified with a SHORT live probe every audit —
    # if it still hears, that beats a full sweep for both speed and accuracy.
    if known_good:
        for i, d in inputs:
            if (d.get("name") or "").strip() == known_good:
                rate = _probe_rate(d)
                peak = _peak_level(i, rate, 1.0)
                if peak is not None and peak > _SILENCE_PEAK:
                    _log(f"known-good '{known_good[:44]}' still hearing "
                         f"(peak {peak}) — kept without full audit")
                    _cache = (time.monotonic(), i)
                    return i
                _log(f"known-good '{known_good[:44]}' stopped hearing "
                     f"(peak {peak}) — full audit")
                known_good = None
                break
        else:
            known_good = None   # device vanished (headset powered off etc.)

    # User preference and saved device load FIRST — the shared-mic adoption
    # below must respect them.
    saved_name = ""
    pref = ""
    try:
        from memory.config_manager import get_input_device, get_mic_preference
        saved_name = (get_input_device() or "").strip()
        pref = (get_mic_preference() or "").strip().lower()
        if pref not in ("bluetooth", "builtin"):
            pref = ""
    except Exception:
        pass

    # The other process's PROVEN mic ranks next: it is hearing on it RIGHT
    # NOW, so a fresh probe of the same device — which can read pure silence
    # under Bluetooth driver contention — must not talk us out of it. If the
    # device has actually vanished, the caller's open will fail and the
    # normal fallbacks take over. An explicit preference beats the other
    # process's history: 'bluetooth' never adopts a non-Bluetooth shared mic
    # and vice versa.
    shared = read_shared_mic()
    if shared and not (pref == "bluetooth" and not _is_bluetooth(shared)) \
            and not (pref == "builtin" and _is_bluetooth(shared)):
        _shared_cands = [i for i, d in inputs
                         if (d.get("name") or "").strip() == shared]
        if _shared_cands:
            # Prefer the WASAPI copy when the same name appears under several
            # host APIs (measured: its MME twins hear far worse).
            try:
                _shared_cands.sort(key=lambda i: -_rank(i))
            except Exception:
                pass
            i = _shared_cands[0]
            # Verify before adopting. Measured live: the launcher adopted the
            # other process's mic WITHOUT a probe, and that name was the AMD
            # array — digitally dead — so it re-picked a deaf mic on every
            # boot while a healthy headset sat unused. A 1 s probe keeps the
            # BT-contention protection (the full audit re-probes Bluetooth
            # with settle retries) while refusing to adopt silence.
            _vp = _peak_level(i, _probe_rate(devices[i]), 1.0)
            if _vp is not None and _vp > _SILENCE_PEAK:
                _log(f"using mic already live in the other JARVIS process: "
                     f"'{shared[:44]}' (verified, peak {_vp})")
                _cache = (time.monotonic(), i)
                return i
            _log(f"shared mic '{shared[:44]}' reads silence now — full audit")
        _log(f"shared mic '{shared[:44]}' not in current device list — full audit")

    # Candidates in priority GROUPS: the first group with any member proven
    # to hear wins, and within a group the STRONGEST signal wins. Groups
    # matter because one physical mic appears under several host APIs —
    # measured live: the headset's WASAPI endpoint peaks 18000+ while its
    # MME twins barely reach 170, so stopping at the first hearing member
    # would select a weak copy of the right microphone.

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

    def _junk(i: int) -> int:
        return 1 if _is_junk_name(devices[i].get("name") or "") else 0

    bt_members    = sorted((i for i, _ in inputs
                            if _is_bluetooth(devices[i].get("name") or "")),
                           key=lambda i: (-_rank(i), -_junk(i)))
    nonbt_members = [i for i, _ in inputs
                     if not _is_bluetooth(devices[i].get("name") or "")]
    saved_matches = sorted((i for i, _ in inputs if _name_matches(i)),
                           key=_rank, reverse=True)

    default_group = [default_in] if default_in is not None and \
        any(i == default_in for i, _ in inputs) else []
    if pref == "builtin":
        groups = [saved_matches,
                  [i for i in nonbt_members if i == default_in],
                  [i for i in nonbt_members],
                  bt_members]
    elif pref == "bluetooth":
        groups = [saved_matches, bt_members, default_group]
    elif _default_output_is_bt():
        # Auto, headset actively listened through: built-in mic FIRST so the
        # headset stays in A2DP (hi-fi). Grabbing the BT mic here is what made
        # system audio flip to phone-call quality every few minutes.
        groups = [saved_matches,
                  [i for i in nonbt_members if i == default_in],
                  nonbt_members,
                  bt_members,
                  default_group]
    else:
        # Auto (default): Bluetooth headset when connected, built-in otherwise.
        groups = [saved_matches, bt_members, default_group]

    # Everything else, driver artifacts last, WASAPI copies preferred.
    rest = sorted((i for i, _ in inputs), key=lambda i: (_junk(i), -_rank(i)))
    groups.append(rest)

    # Dedupe across groups preserving order: each device probed once, in its
    # highest-priority group only.
    seen: set[int] = set()
    dedup: list[list[int]] = []
    for g in groups:
        g2 = [i for i in g if i is not None and i not in seen and not seen.add(i)]
        if g2:
            dedup.append(g2)

    chosen = None
    deadline = time.monotonic() + 25.0   # overall budget (BT retries add time)
    for group in dedup:
        best_idx, best_peak = None, -1
        for idx in group:
            if time.monotonic() > deadline:
                break
            d = devices[idx]
            peak = _probe_device(idx, devices, logger)
            name = (d.get("name") or "")[:44]
            if peak is None:
                _log(f"skip '{name}' (cannot open)")
                continue
            tag = ""
            if peak <= _SILENCE_PEAK:
                tag = "  (digital silence — muted/dead)"
            _log(f"'{name}': peak={peak}{tag}")
            if peak > best_peak:
                best_idx, best_peak = idx, peak
        if best_idx is not None and best_peak > _SILENCE_PEAK:
            # The strongest hearing member of the highest-priority group that
            # hears at all — the group order IS the user's semantics: saved
            # choice, then mic preference, then Bluetooth-when-connected,
            # then the OS default.
            chosen = best_idx
            break
        if time.monotonic() > deadline:
            break

    if chosen is not None:
        _sel_name = (devices[chosen].get("name") or "").strip()
        _last_selected_name = _sel_name
        _log(f"selected input #{chosen} '{_sel_name[:44]}'")
        _cache = (time.monotonic(), chosen)
        return chosen

    _last_selected_name = ""
    _log("no microphone proven to carry audio — falling back to system default")
    _cache = (time.monotonic(), None)
    return None


# ── Shared mic choice ────────────────────────────────────────────────────────
# The launcher and JARVIS both hold long-lived mic streams on this machine.
# Whichever process successfully OPENS a working mic records its name in a
# small state file; the other process takes that name as a TOP candidate
# without re-probing it. Why: probing a Bluetooth HFP mic that another
# process already holds can read digital silence (driver contention), which
# made a healthy headset look dead and pushed the launcher onto the wrong
# device (measured live).
_STATE_FILE = (Path(__file__).resolve().parent.parent / "voice-launcher"
               / "current_mic.txt")


def read_shared_mic() -> str:
    """Device name recorded by the other JARVIS-family process, or ''.

    Driver-artifact names are refused: a loopback once recorded itself as the
    shared mic and queue-jumped every audit for a day."""
    try:
        name = _STATE_FILE.read_text(encoding="utf-8").strip()
        return "" if _is_junk_name(name) else name
    except Exception:
        return ""


def write_shared_mic(name: str) -> None:
    """Record a mic that is OPEN and working in this process (best-effort).
    Junk/virtual artifacts are never recorded."""
    try:
        if name and not _is_junk_name(name):
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(name.strip(), encoding="utf-8")
    except Exception:
        pass


# ── Sandboxed entry point ────────────────────────────────────────────────────
_PROBE_RESULT = "PROBE_RESULT:"


def pick_input_device(logger=None) -> int | None:
    """Sandboxed wrapper — THE entry point for the app (main.py, boot_check,
    voice launcher).

    Spawns a short-lived child process that runs pick_input_device_inprocess()
    and reports back `PROBE_RESULT:<idx>|<name>` on stdout. Native crashes in
    the child (the 1-in-3 segfault measured during testing) cost nothing: the
    parent catches a non-zero exit and falls back to the system default. The
    parent process itself never opens a probe stream and never touches COM
    audio interfaces.
    """
    global _cache, _known_good
    _now = time.monotonic()
    if _cache is not None and (_now - _cache[0]) < _CACHE_TTL:
        return _cache[1]

    root = str(Path(__file__).resolve().parent.parent)
    code = (
        "import sys; sys.path.insert(0, r'" + root + "'); "
        "from core import mic_health as mh; "
        "kg = sys.argv[1] if len(sys.argv) > 1 else ''; "
        "idx = mh.pick_input_device_inprocess(known_good=kg or None); "
        "print('" + _PROBE_RESULT + "' + "
        "str(-1 if idx is None else idx) + '|' + "
        "str(getattr(mh, '_last_selected_name', '') or ''), flush=True)"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", code, _known_good or ""],
            capture_output=True, text=True, timeout=40,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"[MicHealth] probe child failed ({e}) — system default")
        _cache = (time.monotonic(), None)
        _known_good = None
        return None

    idx = None
    name = ""
    for line in (out.stdout or "").splitlines():
        if line.startswith(_PROBE_RESULT):
            try:
                payload = line[len(_PROBE_RESULT):].strip()
                parts = payload.split("|", 1)
                v = int(parts[0])
                idx = None if v < 0 else v
                if len(parts) > 1:
                    name = parts[1]
            except Exception:
                idx = None

    if idx is None and out.returncode != 0:
        # The measured failure mode: native crash inside the probe. Contained.
        print(f"[MicHealth] probe child exited {out.returncode} (crash "
              "contained) — falling back to system default")

    _known_good = (name or None) if idx is not None else None
    _cache = (time.monotonic(), idx)
    return idx
