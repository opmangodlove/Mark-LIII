"""
core/net_check.py — one-shot internet speed probe, spoken back by JARVIS.

WHY THIS EXISTS
    "Why is my internet slow?" used to be a guess. This downloads a fixed
    payload from Cloudflare's speed endpoint and times it, plus a tiny
    latency probe, so JARVIS can answer with measured numbers instead of
    speculation. ~1 MB total — cheap enough for any connection, including
    mobile data.

DESIGN NOTES
    - urllib only (no new dependencies, lazy-imported on first use).
    - Runs in an executor thread (network I/O) — never blocks the voice loop.
    - Returns a 'SPEED_OK …' / 'SPEED_FAIL …' sentence the model can speak
      almost verbatim after trimming the marker.
"""

from __future__ import annotations

import time
import urllib.request

# ~1 MB: big enough to get past TCP slow-start on fast lines, small enough
# to stay polite on genuinely slow mobile data.
_TEST_URL = "https://speed.cloudflare.com/__down?bytes=1000000"
_LATENCY_URL = "https://speed.cloudflare.com/__down?bytes=0"
_TIMEOUT = 12  # seconds per request


def _fetch(url: str) -> tuple[float, int]:
    """Download `url`; return (elapsed_seconds, bytes_received)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "jarvis-net-check/1.0"}
    )
    start = time.monotonic()
    received = 0
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            received += len(chunk)
    return time.monotonic() - start, received


def _measure_latency_ms() -> float:
    """Best-of-2 round-trip time to Cloudflare's edge, in milliseconds."""
    best = float("inf")
    for _ in range(2):
        try:
            elapsed, _n = _fetch(_LATENCY_URL)
            best = min(best, elapsed * 1000.0)
        except Exception:
            pass
    return best if best != float("inf") else -1.0


def _fmt_speed(mbps: float) -> str:
    if mbps >= 100:
        return f"{mbps:.0f} megabits per second"
    if mbps >= 10:
        return f"{mbps:.1f} megabits per second"
    return f"{mbps:.2f} megabits per second"


def run_speed_test() -> str:
    """Run the probe; return a sentence JARVIS can speak (nearly) verbatim."""
    latency = _measure_latency_ms()
    try:
        elapsed, received = _fetch(_TEST_URL)
    except Exception as exc:
        return (
            "SPEED_FAIL — I could not reach the test server at all "
            f"({exc.__class__.__name__}). The internet looks down or heavily "
            "restricted right now."
        )

    if elapsed <= 0 or received == 0:
        return "SPEED_FAIL — the test downloaded nothing, the connection looks dead."

    mbps = (received * 8) / elapsed / 1_000_000.0

    if latency < 0:
        return f"SPEED_OK — your internet is running at about {_fmt_speed(mbps)} down."

    if mbps >= 25 and latency < 80:
        verdict = "That's decent."
    elif mbps < 10 or latency >= 150:
        verdict = "That's on the slow side."
    else:
        verdict = "That's okay for browsing, but not great."

    return (
        f"SPEED_OK — your internet is running at about {_fmt_speed(mbps)} "
        f"down, with a response delay of {latency:.0f} milliseconds. {verdict}"
    )
