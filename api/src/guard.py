"""Per-instance request guards: rate limits, client keys, CORS and response security headers. No I/O."""
import hashlib
import hmac
import ipaddress
import os
import threading

_SALT = os.urandom(32)  # per process: client keys cannot be linked across restarts and IPs are never stored


def client_key(headers):
    """Keyed hash of the caller's IP. The platform front end appends the real client IP as the last
    X-Forwarded-For entry; anything a client sends itself sits before it and is ignored."""
    xff = headers.get("x-forwarded-for", "")
    last = xff.split(",")[-1].strip() if xff else ""
    ip = ""
    if last:
        host = last
        if host.startswith("["):  # [v6]:port
            host = host[1:host.find("]")] if "]" in host else ""
        elif host.count(":") == 1:  # v4:port
            host = host.split(":")[0]
        try:
            addr = ipaddress.ip_address(host)
            # IPv6 clients are bucketed per /64 so one device cannot rotate addresses around the limits.
            ip = (str(ipaddress.ip_network(str(addr) + "/64", strict=False).network_address)
                  if addr.version == 6 else str(addr))
        except ValueError:
            ip = ""
    return hmac.new(_SALT, (ip or "unknown").encode("ascii"), hashlib.sha256).hexdigest()[:24]


class Limiter:
    """Fixed one-minute windows per key. Memory is bounded: past max_keys the table is reset (the global
    limit still applies)."""

    def __init__(self, per_min, max_keys=50000):
        self.per_min = per_min
        self.max_keys = max_keys
        self._win = None
        self._n = {}
        self._lock = threading.Lock()

    def hit(self, key, now):
        w = int(now // 60)
        with self._lock:
            if w != self._win or len(self._n) >= self.max_keys:
                self._win, self._n = w, {}
            c = self._n.get(key, 0) + 1
            self._n[key] = c
            return c <= self.per_min

    def retry_after(self, now):
        return max(1, 60 - int(now % 60))


class Limits:
    """Sized for a conference room behind one NAT address (300 devices) with headroom."""

    def __init__(self):
        self.glob = Limiter(6000)            # accepted progress and submissions on this instance; above the
                                             # per-address total (2100), so one address cannot exhaust it
        self.ip_progress = Limiter(1500)
        self.ip_submit = Limiter(600)
        self.sid_progress = Limiter(60)
        self.sid_submit = Limiter(20)
        self.ip_stats = Limiter(120)         # facilitator dashboards poll every 5 s
        self.ip_badkey = Limiter(10)         # failed facilitator key attempts


SEC_COMMON = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow",
}
JSON_HEADERS = dict(SEC_COMMON, **{
    "Content-Type": "application/json; charset=utf-8",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
})
PAGE_EXTRA = {
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
}


def cors(origin, allowed):
    if origin and origin in allowed:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {"Vary": "Origin"}


def preflight_ok(headers, allowed):
    origin = headers.get("origin", "")
    method = headers.get("access-control-request-method", "")
    req_h = [h.strip().lower() for h in headers.get("access-control-request-headers", "").split(",") if h.strip()]
    return origin in allowed and method == "POST" and all(h == "content-type" for h in req_h)


def preflight_headers(origin):
    return dict(SEC_COMMON, **{
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "7200",
        "Vary": "Origin",
    })


# The app posts its JSON as text/plain so browsers send a CORS "simple" request with no preflight (the
# Functions front end answers OPTIONS itself). Origin is still checked against the allow-list on every POST
# and the body must still be strict JSON; no cookies or other credentials are ever involved.
BODY_TYPES = ("text/plain", "application/json")


def body_ct_ok(v):
    parts = [p.strip().lower().replace(" ", "") for p in v.split(";")]
    if parts[0] not in BODY_TYPES:
        return False
    return all(p in ("charset=utf-8", 'charset="utf-8"') for p in parts[1:] if p)
