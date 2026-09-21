"""
test_find_memo.py — the screen_find 5-second memo, without any network.

The memo exists so screen_find → screen_click back-to-back with the same
description costs ONE vision round-trip instead of two. This test stubs the
Gemini client and asserts:
  1. the first call hits the (stubbed) network exactly once
  2. an identical call within 5 s is a memo hit (zero extra network calls)
  3. a different description goes back to the network
"""

import sys
import time

sys.path.insert(0, ".")

from actions import computer_control as cc


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, counter):
        self._counter = counter

    def generate_content(self, model=None, contents=None):
        self._counter["calls"] += 1
        # The description is the last element of `contents`.
        desc = contents[-1]
        if "start button" in desc.lower():
            return _FakeResponse("100,950")
        return _FakeResponse("500,500")


class _FakeClient:
    def __init__(self, counter):
        self.models = _FakeModels(counter)


def main():
    counter = {"calls": 0}
    cc._find_client = _FakeClient(counter)          # stub the network client
    cc._find_client_key = cc._get_api_key() or "stub"
    cc._find_memo, cc._find_memo_ts, cc._find_memo_result = None, 0.0, None

    # 1. first call → exactly one network call
    r1 = cc._screen_find("the start button")
    assert r1 is not None, "expected a coordinate result"
    assert counter["calls"] == 1, f"first call should hit network once, got {counter['calls']}"

    # 2. identical call within 5 s → memo hit, no extra network call
    r2 = cc._screen_find("the start button")
    assert r2 == r1, f"memo should return identical coords: {r2} != {r1}"
    assert counter["calls"] == 1, f"memo hit must not call network, calls={counter['calls']}"

    # 3. different description → back to the network
    r3 = cc._screen_find("the clock")
    assert r3 is not None
    assert counter["calls"] == 2, f"different description must hit network, calls={counter['calls']}"

    # 4. memo expiry: age the timestamp past 5 s → network again
    cc._find_memo_ts -= 6.0
    r4 = cc._screen_find("the start button")
    assert r4 == r1
    assert counter["calls"] == 3, f"expired memo must re-query, calls={counter['calls']}"

    print("RESULT: ALL PASS (memo hits, expiry, and network-call counts correct)")


if __name__ == "__main__":
    main()
