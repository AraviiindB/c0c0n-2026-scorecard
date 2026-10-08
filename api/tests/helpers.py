"""Shared test setup: import paths, a fake clock, a fake relay and request helpers."""
import json
import logging
import os
import sys

logging.disable(logging.CRITICAL)  # expected warnings (simulated outages) would only clutter test output

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
# The scoring oracle (reference_score.py, catalogue.py) lives outside the published API folder.
ORACLE = os.environ.get("SCORECARD_ORACLE_DIR", os.path.join(os.path.dirname(os.path.dirname(HERE))))
if os.path.isfile(os.path.join(ORACLE, "reference_score.py")) and ORACLE not in sys.path:
    sys.path.append(ORACLE)

import core  # noqa: E402
from relay import RelayError  # noqa: E402

ORIGIN = "https://araviiindb.github.io"
ORIGIN2 = "https://happy-desert-06f32c200.4.azurestaticapps.net"
T0 = 1791500000  # inside the intake window used by the tests
SID = "0123456789abcdef0123456789abcdef"
SID2 = "fedcba9876543210fedcba9876543210"
ALL_Y = "Y" * core.NQ
KEY = "k" * 43
KEY_SHA = __import__("hashlib").sha256(KEY.encode()).digest()


class Clock:
    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        return self.t


class FakeRelay:
    def __init__(self):
        self.posts = []
        self.fail = 0  # number of next posts that fail
        self.up = True

    def available(self):
        return self.up

    def post(self, sub, now):
        if self.fail:
            self.fail -= 1
            raise RelayError("simulated")
        self.posts.append(dict(sub))


def hdrs(origin=ORIGIN, ct="text/plain;charset=UTF-8", xff="203.0.113.7:51234", **extra):
    h = {}
    if origin is not None:
        h["Origin"] = origin
    if ct is not None:
        h["Content-Type"] = ct
    if xff is not None:
        h["X-Forwarded-For"] = xff
    h.update(extra)
    return h


def body(obj):
    return json.dumps(obj).encode()


def sub(sid=SID, code="K7M-Q4X", ans=ALL_Y, **kw):
    d = {"sid": sid, "code": code, "ans": ans, "sector": "", "size": "", "role": "", "v": "1.3.0", "dur": 600}
    d.update(kw)
    return d
