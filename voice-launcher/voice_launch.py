"""
JARVIS voice launcher — starts JARVIS from a fully closed state.

A tiny always-running listener, completely separate from the JARVIS app. It
watches the microphone with an offline speech model and, when it hears the
launch phrase, starts JARVIS (if he is not already running).

Phrases it answers to:
  "Jarvis good morning"  — the launch phrase
  "Jarvis wake up"       — alias
  "Hey Jarvis"           — alias

Everything here is offline. Audio never leaves the machine; the only network
use in this process is none at all (the model was downloaded once at install).

Run:  pythonw voice_launch.py
Stop:  tray-style — it has no UI; kill it from Task Manager (voice_launch.py)
"""

import difflib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent          # Mark-LIII/voice-launcher
APP_DIR = BASE_DIR.parent                            # Mark-LIII/
PYTHONW = APP_DIR / ".venv" / "Scripts" / "pythonw.exe"
MAIN_PY = APP_DIR / "main.py"
MODEL_DIR = BASE_DIR / "vosk-model-small-en-us-0.15"
STATE_FILE = BASE_DIR / "launcher_state.json"
LOG_FILE  = BASE_DIR / "launcher_log.txt"

# Patch: crash auto-revival. A clean exit ("Jarvis goodnight", window closed,
# shutdown tool) leaves this flag behind; a crash leaves nothing. When the
# monitor finds JARVIS gone WITHOUT the flag, he died unexpectedly — relaunch.
CLEAN_EXIT_FLAG = APP_DIR / "clean_exit.flag"

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
# How long after a launch before this listener may launch again. JARVIS needs
# a few seconds to boot; without this, one long "jarvis good morninggg" could
# fire twice and open two copies.
LAUNCH_COOLDOWN_S = 30

# Crash auto-revival: started once per process (listen() can re-enter).
_monitor_started = False

# Phrases that launch JARVIS. Matched as substrings of the recognised text,
# lowercased, so "jarvis good morning to you too" still fires.
LAUNCH_PHRASES = [
    "jarvis good morning",
    "jarvis good evening",
    "jarvis wake up",
    "hey jarvis",
    "jarvis come online",
]

# Phrases that QUIT JARVIS (only acted on when he is running).
QUIT_PHRASES = [
    "jarvis goodnight",
    "jarvis good night",
    "jarvis shut down",
    "jarvis sleep now",
]

# A cooldown after quit too, so "goodnight" doesn't bounce with the mic still
# warm and relaunch him a second later via a stray "good morning".
QUIT_COOLDOWN_S = 10


def log(msg: str) -> None:
    print(f"[voice-launch] {msg}", flush=True)
    # Patch: pythonw discards stdout entirely, so the file is the only record
    # of what the launcher heard and did. 1 MB cap keeps it a text file, not
    # a disk filler; the previous run's copy survives as .old for post-mortems.
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".old.txt"))
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except Exception:
        pass


# Patch: wmic was removed from modern Windows builds, so the old
# process lookup raised FileNotFoundError the moment a phrase was heard and
# the launch never happened (the listener restarted, looking healthy, but
# deaf to action). PowerShell's Get-CimInstance is the supported equivalent.
_PS_PIDS = (
    "$p = Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
    "Where-Object { $_.CommandLine -like '*main.py*' }; "
    "if ($p) { ($p | ForEach-Object { $_.ProcessId }) -join ',' }"
)


def jarvis_pids() -> list[str]:
    """PIDs of running JARVIS (main.py) processes; empty if none."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PIDS],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15,
        ).stdout.strip()
    except Exception as e:
        log(f"process lookup failed: {e}")
        return []
    return [p for p in out.replace(",", " ").split() if p.strip()]


def pick_input_device():
    """Pick a microphone that actually hears sound.

    Measured on this machine: the OS default (the built-in AMD mic array)
    opens fine and delivers pure digital zeros — a perfectly healthy-looking
    stream that is deaf. The launcher listened to that void for days, which
    is why 'Jarvis good morning' never worked. core/mic_health probes each
    candidate for real signal and returns one that provably carries audio,
    preferring the device the JARVIS app itself uses (config)."""
    try:
        sys.path.insert(0, str(APP_DIR))
        from core import mic_health
        idx = mic_health.pick_input_device(logger=log)
        if idx is not None:
            return idx
    except Exception as e:
        log(f"mic_health failed ({e}) — falling back to app config")
    # Fallback: whatever the JARVIS app resolves (system default if unset).
    try:
        from core import audio_devices
        from memory.config_manager import get_input_device
        name = get_input_device()
        idx  = audio_devices.resolve(name, "input")
        log(f"fallback device: {name!r} -> {idx if idx is not None else 'system default'}")
        return idx
    except Exception as e:
        log(f"device pick failed ({e}) — using system default")
        return None


def _fuzzy_hit(tokens: list[str], phrase_tokens: list[str]) -> bool:
    """Windowed token match that forgives one misheard word in three-plus.

    Vosk's small offline model mishears through a Bluetooth headset
    ('jervis goo morning'); a strict substring match then never fires and
    nobody can tell why. A sliding window over the recognised words needs
    ~all but one of the phrase's words to match approximately.

    The phrase's FIRST word ('jarvis' / 'hey') must also match approximately
    somewhere in the window — measured live: without an anchor, 'the order to
    MAKE UP your a yeah' matched launch phrase 'jarvis WAKE UP' (make≈wake,
    up=up) and spuriously triggered a launch."""
    n = len(phrase_tokens)
    if len(tokens) < n:
        return False
    needed = n if n <= 2 else n - 1
    anchor = phrase_tokens[0]
    for i in range(len(tokens) - n + 1):
        window = tokens[i:i + n]
        if not any(
            a == anchor or difflib.SequenceMatcher(None, a, anchor).ratio() >= 0.6
            for a in window
        ):
            continue
        hits = sum(
            1 for a, b in zip(window, phrase_tokens)
            if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75
        )
        if hits >= needed:
            return True
    return False


def match_any(text: str, phrases: list[str]) -> bool:
    toks = text.lower().split()
    return any(_fuzzy_hit(toks, p.lower().split()) for p in phrases)


def launch_jarvis() -> None:
    log("launch phrase heard — starting JARVIS")
    if not PYTHONW.exists():
        log(f"ERROR: python not found at {PYTHONW}")
        return
    # Any older flag is now meaningless — the coming run's exit is the one
    # that counts.
    _clear_clean_exit()
    subprocess.Popen(
        [str(PYTHONW), str(MAIN_PY)],
        cwd=str(APP_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# Patch: the old quit killed EVERY pythonw.exe — including this launcher,
# which is also a pythonw process. Kill only JARVIS's own PIDs.
def quit_jarvis(pids: list[str]) -> None:
    # The launcher is doing the killing, so JARVIS cannot mark his own exit —
    # the flag must be written here or the monitor would read this deliberate
    # quit as a crash and revive him mid-goodnight.
    _mark_clean_exit()
    log(f"quit phrase heard — closing JARVIS (pids: {', '.join(pids)})")
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", pid, "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def write_state(trigger: str) -> None:
    try:
        STATE_FILE.write_text(json.dumps({
            "last_trigger": trigger,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Crash auto-revival
# ---------------------------------------------------------------------------

def _mark_clean_exit() -> None:
    try:
        CLEAN_EXIT_FLAG.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    except Exception:
        pass


def _clear_clean_exit() -> None:
    try:
        CLEAN_EXIT_FLAG.unlink(missing_ok=True)
    except Exception:
        pass


def _has_clean_exit() -> bool:
    try:
        return CLEAN_EXIT_FLAG.exists()
    except Exception:
        return False


def _announce(text: str) -> None:
    """Speak one line via Windows' built-in SAPI voice. Best-effort: the
    launcher must work even if speech fails; the log is the real record."""
    log(f"saying: {text!r}")
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
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=20,
        )
    except Exception:
        pass


# Only announce a crash for a JARVIS that was actually seen running — the
# monitor must stay silent before the first launch (he simply isn't up yet).
# Two-tick hysteresis (10 s each): a running JARVIS only clears the flag
# after being seen twice in a row, and a disappearance only counts after two
# empty polls. This closes both races: a process-kill's dying gap cannot
# erase the quit flag, and a one-poll process hiccup cannot fake a crash.
_revival_seen_running = False
_revival_run_ticks = 0
_revival_gone_ticks = 0


def _revival_monitor() -> None:
    global _revival_seen_running, _revival_run_ticks, _revival_gone_ticks
    while True:
        time.sleep(10)
        try:
            pids = jarvis_pids()
        except Exception:
            continue
        if pids:
            _revival_gone_ticks = 0
            _revival_run_ticks += 1
            if _revival_run_ticks >= 2:
                # Stably running: any flag left over is stale — the next
                # death of THIS run is the one that counts.
                _revival_seen_running = True
                _clear_clean_exit()
            continue
        _revival_run_ticks = 0
        _revival_gone_ticks += 1
        if _revival_gone_ticks < 2 or not _revival_seen_running:
            continue
        _revival_seen_running = False
        _revival_gone_ticks = 0
        if _has_clean_exit():
            # Deliberate exit — stay quiet until the next launch.
            continue
        log("JARVIS vanished without a clean-exit flag — treating as a crash")
        _clear_clean_exit()
        _announce("Pardon the interruption, sir. JARVIS restarted after a fault.")
        launch_jarvis()


# ---------------------------------------------------------------------------
# Audio + recognition
# ---------------------------------------------------------------------------

def listen() -> None:
    # Patch: crash auto-revival monitor — one per launcher process, not one
    # per listen() restart (the mic watchdog re-enters this function).
    global _monitor_started
    if not _monitor_started:
        _monitor_started = True
        threading.Thread(target=_revival_monitor, daemon=True,
                         name="jarvis-revival").start()

    from vosk import Model, KaldiRecognizer
    import sounddevice as sd
    import numpy as np   # noqa: F401  (sounddevice imports it anyway)

    if not MODEL_DIR.exists():
        # Fresh clone: the model is too big for git, so fetch it on first
        # boot (~40 MB, one time). If that fails, wake word is dead but the
        # error now tells the user exactly why instead of silently exiting.
        log("wake-word model missing — downloading once (about 40 MB)…")
        try:
            import io
            import zipfile
            import urllib.request
            _zip_url = (
                "https://alphacephei.com/vosk/models/"
                "vosk-model-small-en-us-0.15.zip"
            )
            with urllib.request.urlopen(_zip_url, timeout=60) as resp:
                _buf = io.BytesIO(resp.read())
            with zipfile.ZipFile(_buf) as zf:
                zf.extractall(BASE_DIR)
            log(f"model downloaded -> {MODEL_DIR}")
        except Exception as exc:
            log(f"FATAL: model missing at {MODEL_DIR} and download failed: {exc}")
            return

    log("loading speech model…")
    model = Model(str(MODEL_DIR))
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(False)

    frames_per_chunk = int(SAMPLE_RATE * CHUNK_SECONDS)

    # Patch: phrase handling moved OUT of the audio callback. The process
    # lookup can take a second or two (PowerShell spin-up); doing that inside
    # the callback stalls the real-time audio thread and drops mic frames.
    # The callback now only recognises and enqueues; the main loop acts.
    actions_q: queue.Queue = queue.Queue()

    mic_state = {"last_data": time.time(), "max_level": 0}

    def on_audio(indata, frames, time_info, status):
        # Patch: any exception inside a PortAudio callback silently kills the
        # stream — the process keeps 'running' but delivers no audio ever
        # again. That is exactly how this launcher went deaf for days. Nothing
        # in here is allowed to raise.
        try:
            mic_state["last_data"] = time.time()
            lvl = int(abs(indata).max()) if len(indata) else 0
            if lvl > mic_state["max_level"]:
                mic_state["max_level"] = lvl
            if rec.AcceptWaveform(indata.tobytes()):
                text = json.loads(rec.Result()).get("text", "")
                if not text:
                    return
                log(f"heard: {text!r}")
                if match_any(text, QUIT_PHRASES):
                    actions_q.put(("quit", text))
                    rec.Reset()
                    return
                if match_any(text, LAUNCH_PHRASES):
                    actions_q.put(("launch", text))
                    rec.Reset()
        except Exception as e:
            log(f"audio callback error: {e}")

    log("listening — say 'Jarvis good morning' to launch JARVIS")
    last_launch = 0.0
    last_quit = 0.0
    dev = pick_input_device()
    # Record the mic this process actually opened, so the JARVIS app (and
    # vice versa) can adopt the PROVEN device instead of mis-probing it
    # under Bluetooth driver contention (mic_health.read_shared_mic).
    try:
        from core import mic_health as _mh_share
        if dev is not None:
            _mh_share.write_shared_mic(
                sd.query_devices()[dev].get("name", "") or f"#{dev}")
    except Exception:
        pass
    # Device-switch watcher state: when the input-device list changes
    # (headset powers on/off, mic plugged/unplugged), re-audit and hot-swap
    # onto the best mic without losing the wake-word listener.
    try:
        from core import mic_health as _mh
        cur_sig = _mh.current_signature()
    except Exception:
        _mh = None
        cur_sig = ""
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=frames_per_chunk,
        device=dev,
        callback=on_audio,
    ):
        last_beat = time.time()
        while True:
            # Patch: mic watchdog — if PortAudio stops delivering frames
            # (driver hiccup, unplugged device, dead callback), restarting
            # the stream is the only cure, and without this nobody would
            # ever know. Raising reuses the service-style restart loop below.
            silent_for = time.time() - mic_state["last_data"]
            if silent_for > 30:
                log(f"mic silent {silent_for:.0f}s (max level seen: "
                    f"{mic_state['max_level']}) — restarting audio stream")
                raise RuntimeError("mic watchdog: no audio for 30s")
            # Patch: device-switch watcher (the user's request): bluetooth
            # headset connects → its mic becomes the ear; it powers off →
            # fall back to the laptop's built-in mic. The signature only
            # changes when endpoints appear/disappear, so this is one cheap
            # list call per loop iteration.
            if _mh is not None and cur_sig:
                try:
                    new_sig = _mh.current_signature()
                    if new_sig and new_sig != cur_sig:
                        cur_sig = new_sig
                        new_dev = _mh.pick_input_device()
                        if new_dev is not None and new_dev != dev:
                            new_name = ""
                            try:
                                new_name = sd.query_devices()[new_dev].get("name", "") or f"#{new_dev}"
                            except Exception:
                                pass
                            log(f"audio devices changed — switching mic "
                                f"#{dev} -> #{new_dev} {new_name!r}")
                            if _mh._is_bluetooth(new_name):
                                _announce("Switching to your headset microphone, sir.")
                            else:
                                _announce("Switching to the built-in microphone, sir.")
                            dev = new_dev
                            return   # restart listen() on the new device
                        elif new_dev == dev:
                            log("audio devices changed — current mic still the best")
                        else:
                            log("audio devices changed but nothing hears — keeping current mic")
                except Exception as e:
                    log(f"device-switch check failed: {e}")
            if time.time() - last_beat > 60:
                last_beat = time.time()
                log(f"alive; mic max level last minute: {mic_state['max_level']}")
                mic_state["max_level"] = 0
            try:
                kind, text = actions_q.get(timeout=1.0)
            except queue.Empty:
                continue
            now = time.time()

            if kind == "quit":
                if (now - last_quit) > QUIT_COOLDOWN_S:
                    last_quit = now
                    last_launch = now        # block launch for the same window
                    write_state(f"quit via: {text}")
                    pids = jarvis_pids()
                    if pids:
                        quit_jarvis(pids)
                    else:
                        log("quit heard, but JARVIS is not running")
                continue

            if (now - last_launch) > LAUNCH_COOLDOWN_S:
                last_launch = now
                write_state(f"launch via: {text}")
                if not jarvis_pids():
                    launch_jarvis()
                else:
                    log(f"already running — ignored ({text})")


if __name__ == "__main__":
    # Single-instance guard: Windows auto-start plus a manual launch (or a
    # boot race between two startup entries) can silently run two launchers.
    # Then ONE wake word spawns TWO JARVISes that fight over the dashboard
    # port. The lock file holds the first instance's PID; a late second
    # instance exits quietly. Stale lock (dead PID) is claimed automatically.
    _lock = BASE_DIR / "launcher.lock"
    try:
        _existing = int(_lock.read_text().strip() or 0)
    except Exception:
        _existing = 0
    if _existing:
        _alive = subprocess.run(
            ["tasklist", "/FI", f"PID eq {_existing}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if str(_existing) in _alive.stdout:
            print(f"[voice-launch] another launcher already runs (pid {_existing}) — exiting")
            sys.exit(0)
    _lock.write_text(str(os.getpid()))
    # Race verification: two instances started in the same second both see
    # no lock, both write. The LAST writer wins — everyone else re-reads,
    # sees a foreign PID, and exits. Exactly one survivor, every time.
    time.sleep(0.7)
    try:
        if int(_lock.read_text().strip() or 0) != os.getpid():
            print("[voice-launch] lost the startup race — another launcher owns the lock — exiting")
            sys.exit(0)
    except Exception:
        pass

    # A flag surviving from a previous launcher run says nothing about the
    # JARVIS run that follows — crashes since then must be revivable.
    _clear_clean_exit()
    while True:
        try:
            listen()
        except KeyboardInterrupt:
            log("stopped by user")
            break
        except Exception as e:
            # A dead mic or a driver hiccup should not end the launcher — log
            # and retry, the way a service would.
            log(f"listener error: {e} — restarting in 5s")
            time.sleep(5)
