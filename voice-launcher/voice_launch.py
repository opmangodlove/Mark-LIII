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

import json
import queue
import subprocess
import sys
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

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
# How long after a launch before this listener may launch again. JARVIS needs
# a few seconds to boot; without this, one long "jarvis good morninggg" could
# fire twice and open two copies.
LAUNCH_COOLDOWN_S = 30

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


def launch_jarvis() -> None:
    log("launch phrase heard — starting JARVIS")
    if not PYTHONW.exists():
        log(f"ERROR: python not found at {PYTHONW}")
        return
    subprocess.Popen(
        [str(PYTHONW), str(MAIN_PY)],
        cwd=str(APP_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


# Patch: the old quit killed EVERY pythonw.exe — including this launcher,
# which is also a pythonw process. Kill only JARVIS's own PIDs.
def quit_jarvis(pids: list[str]) -> None:
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
# Audio + recognition
# ---------------------------------------------------------------------------

def listen() -> None:
    from vosk import Model, KaldiRecognizer
    import sounddevice as sd
    import numpy as np   # noqa: F401  (sounddevice imports it anyway)

    if not MODEL_DIR.exists():
        log(f"FATAL: model missing at {MODEL_DIR}")
        return

    log("loading speech model…")
    model = Model(str(MODEL_DIR))
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(False)

    frames_per_chunk = int(SAMPLE_RATE * CHUNK_SECONDS)
    last_launch = 0.0
    last_quit = 0.0

    # Patch: phrase handling moved OUT of the audio callback. The process
    # lookup can take a second or two (PowerShell spin-up); doing that inside
    # the callback stalls the real-time audio thread and drops mic frames.
    # The callback now only recognises and enqueues; the main loop acts.
    actions_q: queue.Queue = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        if rec.AcceptWaveform(indata.tobytes()):
            text = json.loads(rec.Result()).get("text", "")
            if not text:
                return
            if any(p in text for p in QUIT_PHRASES):
                actions_q.put(("quit", text))
                rec.Reset()
                return
            if any(p in text for p in LAUNCH_PHRASES):
                actions_q.put(("launch", text))
                rec.Reset()

    log("listening — say 'Jarvis good morning' to launch JARVIS")
    last_launch = 0.0
    last_quit = 0.0
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=frames_per_chunk,
        callback=on_audio,
    ):
        while True:
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
