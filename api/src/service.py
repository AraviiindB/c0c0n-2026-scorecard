"""HTTP handlers of the session results service, independent of the Azure Functions runtime (function_app.py
adapts them). Every response is JSON or the facilitator dashboard; no request data or IP address is logged."""
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import core
import guard
from relay import RelayError
from store import Conflict, StoreError

LEASE_S = 90
MAX_TRIES = 20
SCAN_CAP = 20000
STATS_TTL = 5
log = logging.getLogger("scorecard")


def backoff(n):
    return min(15 * 2 ** max(n - 1, 0), 300)


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Config:
    def __init__(self, origins, key_sha256, intake_from, intake_until, relay_enabled=True):
        self.origins = frozenset(origins)
        self.key_sha256 = key_sha256  # bytes or None (stats disabled)
        self.intake_from, self.intake_until = intake_from, intake_until
        self.relay_enabled = relay_enabled

    @classmethod
    def from_env(cls, env=os.environ):
        origins = [o.strip() for o in env.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        for o in origins:
            if not re.fullmatch(r"https://[a-z0-9.-]+", o):
                raise ValueError("ALLOWED_ORIGINS")
        kh = env.get("FACILITATOR_KEY_SHA256", "").strip().lower()
        return cls(origins, bytes.fromhex(kh) if re.fullmatch(r"[0-9a-f]{64}", kh) else None,
                   _int(env.get("INTAKE_FROM")), _int(env.get("INTAKE_UNTIL")),
                   env.get("RELAY_ENABLED", "1") == "1")


def _dash_page():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "dash.html"), encoding="utf-8") as f:
        html = f.read()
    hashes = {"script": [], "style": []}
    for m in re.finditer(r"<(script|style)>(.*?)</\1>", html, re.S):
        hashes[m.group(1)].append("'sha256-%s'" % base64.b64encode(
            hashlib.sha256(m.group(2).encode("utf-8")).digest()).decode())
    assert len(hashes["script"]) == 1 and len(hashes["style"]) == 1
    csp = ("default-src 'none'; script-src %s; style-src %s; connect-src 'self'; img-src 'none'; font-src 'none'; "
           "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'; "
           "trusted-types 'none'") % (hashes["script"][0], hashes["style"][0])
    headers = dict(guard.SEC_COMMON, **guard.PAGE_EXTRA)
    headers.update({"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": csp})
    return html.encode("utf-8"), headers


class Service:
    def __init__(self, store, relay, cfg, clock=time.time):
        self.store, self.relay, self.cfg, self.clock = store, relay, cfg, clock
        self.limits = guard.Limits()
        self._inline = threading.BoundedSemaphore(8)
        self._stats_lock = threading.Lock()
        self._stats = (0.0, None)
        self._dash = _dash_page()

    # ---- helpers ----
    def _json(self, status, obj, extra=None):
        h = dict(guard.JSON_HEADERS)
        if extra:
            h.update(extra)
        return status, h, json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")

    def _err(self, status, code, extra=None):
        return self._json(status, {"error": code}, extra)

    def _open(self, now):
        return self.cfg.intake_from <= now < self.cfg.intake_until

    def handle(self, route, method, headers, body):
        now = self.clock()
        h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        if not self.limits.glob.hit("all", now):
            return self._err(429, "rate_limited", {"Retry-After": str(self.limits.glob.retry_after(now))})
        try:
            if route in ("progress", "submit"):
                return self._intake(route, method, h, body, now)
            if route == "stats":
                return self._stats_route(method, h, now)
            if route == "dash":
                if method not in ("GET", "HEAD"):
                    return self._err(405, "method_not_allowed", {"Allow": "GET"})
                return 200, dict(self._dash[1]), self._dash[0]
            if route == "health":
                if method not in ("GET", "HEAD"):
                    return self._err(405, "method_not_allowed", {"Allow": "GET"})
                return self._json(200, {"ok": True, "v": core.VERSION, "open": self._open(now)})
            return self._err(404, "not_found")
        except StoreError:
            log.warning("storage unavailable")
            return self._err(503, "unavailable", dict(guard.cors(h.get("origin", ""), self.cfg.origins),
                                                       **{"Retry-After": "5"}))
        except Exception:  # never leak details
            log.exception("unhandled error")
            return self._err(500, "internal", guard.cors(h.get("origin", ""), self.cfg.origins))

    # ---- participant routes ----
    def _intake(self, route, method, h, body, now):
        origin = h.get("origin", "")
        if method == "OPTIONS":
            if guard.preflight_ok(h, self.cfg.origins):
                return 204, guard.preflight_headers(origin), b""
            return self._err(403, "forbidden")
        if method != "POST":
            return self._err(405, "method_not_allowed", {"Allow": "POST, OPTIONS"})
        if origin not in self.cfg.origins:
            return self._err(403, "forbidden")
        cors = guard.cors(origin, self.cfg.origins)
        if not guard.body_ct_ok(h.get("content-type", "")):
            return self._err(415, "unsupported_media_type", cors)
        if not self._open(now):
            return self._err(403, "closed", cors)
        ipk = guard.client_key(h)
        lim_ip = self.limits.ip_progress if route == "progress" else self.limits.ip_submit
        if not lim_ip.hit(ipk, now):
            return self._err(429, "rate_limited", dict(cors, **{"Retry-After": str(lim_ip.retry_after(now))}))
        try:
            if route == "progress":
                sid, stage = core.parse_progress(body)
            else:
                sub = core.parse_submit(body)
                sid = sub["sid"]
        except core.Invalid:
            return self._err(400, "bad_request", cors)
        lim_sid = self.limits.sid_progress if route == "progress" else self.limits.sid_submit
        if not lim_sid.hit(sid, now):
            return self._err(429, "rate_limited", dict(cors, **{"Retry-After": str(lim_sid.retry_after(now))}))
        if route == "progress":
            self.store.put_progress(sid, stage, now)
            return 204, dict(guard.SEC_COMMON, **cors), b""
        return self._json(200, {"ok": True, "forms": self._submit(sub, now)}, cors)

    def _submit(self, sub, now):
        ph = core.payload_hash(sub)
        cur = self.store.get_sub(sub["sid"])
        if cur and cur.get("ph") == ph:
            if cur.get("rs") == "sent":
                return "sent"
            ent = dict(cur, sid=sub["sid"])
            if ent.get("rs") == "failed":  # the participant's device is still trying: give the relay a new chance
                try:
                    ent["_etag"] = self.store.put_sub(sub["sid"], {"rs": "pending", "rn": 0, "rt": 0}, ent.get("_etag"))
                    ent.update(rs="pending", rn=0)
                except Conflict:
                    return "queued"
        else:
            props = {"code": sub["code"], "sector": sub["sector"], "size": sub["size"], "role": sub["role"],
                     "ans": sub["ans"], "v": sub["v"], "dur": sub["dur"], "ph": ph, "ts": int(now),
                     "rs": "pending", "rn": 0, "rt": 0, "rl": 0}
            if not cur:
                props["t0"] = int(now)
            etag = self.store.put_sub(sub["sid"], props)
            ent = dict(props, sid=sub["sid"], _etag=etag)
        if ent.get("rs") == "pending" and self._can_relay() and self._inline.acquire(blocking=False):
            try:
                if self._relay_one(ent, now):
                    return "sent"
            finally:
                self._inline.release()
        return "queued"

    # ---- relay to Microsoft Forms ----
    def _can_relay(self):
        return self.cfg.relay_enabled and self.relay is not None and self.relay.available()

    def _relay_one(self, ent, now):
        sid = ent["sid"]
        try:
            etag = self.store.put_sub(sid, {"rs": "sending", "rl": int(now) + LEASE_S}, ent.get("_etag"))
        except Conflict:
            return False  # changed or claimed by another worker
        try:
            self.relay.post(ent, now)
        except Exception as e:  # any relay failure is retried with backoff; details never reach clients
            rn = _int(ent.get("rn")) + 1
            log.warning("relay attempt %d failed: %s", rn, e if isinstance(e, RelayError) else type(e).__name__)
            try:
                self.store.put_sub(sid, {"rs": "failed" if rn >= MAX_TRIES else "pending", "rn": rn,
                                         "rt": int(now) + backoff(rn), "rl": 0}, etag)
            except Conflict:
                pass  # resubmitted meanwhile: the new payload is pending anyway
            return False
        try:
            self.store.put_sub(sid, {"rs": "sent", "rh": ent.get("ph", ""), "rl": 0, "sentAt": int(now)}, etag)
        except Conflict:
            pass  # resubmitted while sending: the newer payload stays pending and is sent next
        return True

    def drain(self, budget_s=40, workers=4):
        """Timer: send pending submissions whose retry time has come and reclaim expired leases."""
        if not self._can_relay():
            return 0
        now = self.clock()
        if now >= self.cfg.intake_until + 86400:
            return 0
        rows = self.store.list_relay_work(2000)
        due = [r for r in rows if core.valid_row(r) and (
            (r.get("rs") == "pending" and _int(r.get("rt")) <= now) or
            (r.get("rs") == "sending" and _int(r.get("rl")) < now))]
        due.sort(key=lambda r: _int(r.get("ts")))
        deadline = time.monotonic() + budget_s
        sent = [0]
        lock = threading.Lock()

        def work(r):
            if time.monotonic() > deadline or not self.relay.available():
                return
            if self._relay_one(r, self.clock()):
                with lock:
                    sent[0] += 1

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, due))
        return sent[0]

    # ---- facilitator routes ----
    def _authorised(self, h):
        auth = h.get("authorization", "")
        if not self.cfg.key_sha256 or not auth.startswith("Bearer "):
            return False
        key = auth[7:].strip()
        if not 16 <= len(key) <= 128:
            return False
        return hmac.compare_digest(hashlib.sha256(key.encode("utf-8", "replace")).digest(), self.cfg.key_sha256)

    def _stats_route(self, method, h, now):
        if method != "GET":
            return self._err(405, "method_not_allowed", {"Allow": "GET"})
        if not self._authorised(h):
            # Unauthenticated attempts have their own budget, so they can never lock out the facilitators.
            ipk = guard.client_key(h)
            if not self.limits.ip_badkey.hit(ipk, now):
                return self._err(429, "rate_limited", {"Retry-After": str(self.limits.ip_badkey.retry_after(now))})
            return self._err(401, "unauthorized", {"WWW-Authenticate": 'Bearer realm="facilitators"'})
        if not self.limits.ip_stats.hit("key", now):
            return self._err(429, "rate_limited", {"Retry-After": str(self.limits.ip_stats.retry_after(now))})
        with self._stats_lock:
            at, res = self._stats
            if res is None or now - at >= STATS_TTL:
                res = self._compute(now)
                self._stats = (now, res)
        return self._json(200, res)

    def _compute(self, now):
        subs = [s for s in self.store.list_subs(SCAN_CAP) if core.valid_row(s)]
        progs = [{"sid": p["sid"], "s": _int(p.get("s"), -1), "ts": _int(p.get("ts"))}
                 for p in self.store.list_progress(SCAN_CAP)]
        progs = [p for p in progs if core.valid_sid(p["sid"]) and 0 <= p["s"] < core.STAGES]
        res = core.aggregate(subs, progs, int(now))
        res["open"] = self._open(now)
        res["truncated"] = len(subs) >= SCAN_CAP or len(progs) >= SCAN_CAP
        return res


def from_env():
    from relay import Relay
    from store import ManagedIdentityToken, TableStore
    env = os.environ
    store = TableStore(env["TABLE_ACCOUNT"], env.get("TABLE_PREFIX", "ev1"), ManagedIdentityToken())
    relay = Relay(env["FORM_ID"]) if env.get("FORM_ID") else None
    return Service(store, relay, Config.from_env(env))
