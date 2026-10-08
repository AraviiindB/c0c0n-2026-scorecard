import base64
import hashlib
import json
import re
import unittest

import guard
import service
from helpers import (ALL_Y, KEY, KEY_SHA, ORIGIN, ORIGIN2, SID, SID2, T0, Clock, FakeRelay, body, core, hdrs,
                     sub)
from store import Conflict, MemoryStore


def make(relay=True, **cfg):
    store, clock = MemoryStore(), Clock()
    rel = FakeRelay() if relay else None
    c = dict(origins=[ORIGIN, ORIGIN2], key_sha256=KEY_SHA, intake_from=T0 - 3600, intake_until=T0 + 86400)
    c.update(cfg)
    return service.Service(store, rel, service.Config(**c), clock=clock), store, rel, clock


def js(resp):
    return json.loads(resp[2]) if resp[2] else None


class Headers(unittest.TestCase):
    def test_security_headers_everywhere(self):
        svc, *_ = make()
        responses = [
            svc.handle("health", "GET", {}, b""), svc.handle("dash", "GET", {}, b""),
            svc.handle("stats", "GET", {}, b""), svc.handle("submit", "POST", hdrs(origin="https://evil.example"), b""),
            svc.handle("progress", "POST", hdrs(), body({"sid": SID, "s": 1})),
            svc.handle("submit", "OPTIONS", hdrs(**{"Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type"}), b""),
            svc.handle("nope", "GET", {}, b""),
        ]
        for st, h, b in responses:
            with self.subTest(st):
                for k, v in guard.SEC_COMMON.items():
                    self.assertEqual(h.get(k), v)
                if b:
                    self.assertIn(h["Content-Type"].split(";")[0], ("application/json", "text/html"))
                    self.assertIn("Content-Security-Policy", h)

    def test_dash_csp_matches_inline_blocks(self):
        svc, *_ = make()
        st, h, b = svc.handle("dash", "GET", {}, b"")
        self.assertEqual(st, 200)
        html = b.decode()
        csp = h["Content-Security-Policy"]
        for tag in ("script", "style"):
            blocks = re.findall(r"<%s>(.*?)</%s>" % (tag, tag), html, re.S)
            self.assertEqual(len(blocks), 1)
            digest = base64.b64encode(hashlib.sha256(blocks[0].encode()).digest()).decode()
            self.assertIn("%s-src 'sha256-%s'" % (tag, digest), csp)
        self.assertEqual(len(re.findall(r"<script\b", html)), 1)
        self.assertEqual(len(re.findall(r"<style\b", html)), 1)
        for frag in ("default-src 'none'", "connect-src 'self'", "frame-ancestors 'none'",
                     "require-trusted-types-for 'script'", "form-action 'none'", "base-uri 'none'"):
            self.assertIn(frag, csp)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "Function(",
                     "setTimeout(\"", "javascript:", " on" + "click=", "srcdoc"):
            self.assertNotIn(sink, html)

    def test_health(self):
        svc, *_ = make()
        st, h, b = svc.handle("health", "GET", {}, b"")
        self.assertEqual((st, js((st, h, b))), (200, {"ok": True, "v": core.VERSION, "open": True}))
        self.assertEqual(svc.handle("health", "POST", {}, b"")[0], 405)


class Intake(unittest.TestCase):
    def test_progress(self):
        svc, store, _, _ = make()
        st, h, b = svc.handle("progress", "POST", hdrs(), body({"sid": SID, "s": 2}))
        self.assertEqual((st, b), (204, b""))
        self.assertEqual(h["Access-Control-Allow-Origin"], ORIGIN)
        self.assertNotIn("Content-Type", h)
        self.assertEqual(store.prog[SID], {"s": 2, "ts": T0})
        st, h, _ = svc.handle("progress", "POST", hdrs(origin=ORIGIN2), body({"sid": SID, "s": 3}))
        self.assertEqual((st, h["Access-Control-Allow-Origin"]), (204, ORIGIN2))
        st, _, _ = svc.handle("progress", "POST", hdrs(ct="application/json"), body({"sid": SID, "s": 4}))
        self.assertEqual((st, store.prog[SID]["s"]), (204, 4))

    def test_rejections(self):
        svc, store, _, _ = make()
        ok = body({"sid": SID, "s": 1})
        cases = [
            ("POST", hdrs(origin=None), ok, 403, "forbidden"),
            ("POST", hdrs(origin="https://evil.example"), ok, 403, "forbidden"),
            ("POST", hdrs(origin="null"), ok, 403, "forbidden"),
            ("POST", hdrs(origin=ORIGIN + "/"), ok, 403, "forbidden"),
            ("POST", hdrs(ct="text/html"), ok, 415, "unsupported_media_type"),
            ("POST", hdrs(ct="text/plain; charset=iso-8859-1"), ok, 415, "unsupported_media_type"),
            ("POST", hdrs(ct=None), ok, 415, "unsupported_media_type"),
            ("POST", hdrs(ct="application/x-www-form-urlencoded"), ok, 415, "unsupported_media_type"),
            ("POST", hdrs(ct="multipart/form-data; boundary=x"), ok, 415, "unsupported_media_type"),
            ("POST", hdrs(), b"{}", 400, "bad_request"),
            ("POST", hdrs(), b"sid=1", 400, "bad_request"),
            ("GET", hdrs(), b"", 405, "method_not_allowed"),
            ("PUT", hdrs(), ok, 405, "method_not_allowed"),
        ]
        for method, h, b, want, code in cases:
            with self.subTest((method, h.get("Origin"), h.get("Content-Type"))):
                st, rh, rb = svc.handle("progress", method, h, b)
                self.assertEqual((st, json.loads(rb)["error"]), (want, code))
                if want == 403:
                    self.assertNotIn("Access-Control-Allow-Origin", rh)
        self.assertEqual(store.prog, {})

    def test_preflight(self):
        svc, *_ = make()
        good = hdrs(ct=None, **{"Access-Control-Request-Method": "POST",
                                "Access-Control-Request-Headers": "Content-Type"})
        st, h, _ = svc.handle("submit", "OPTIONS", good, b"")
        self.assertEqual(st, 204)
        self.assertEqual((h["Access-Control-Allow-Origin"], h["Access-Control-Allow-Methods"],
                          h["Access-Control-Allow-Headers"]), (ORIGIN, "POST", "Content-Type"))
        self.assertNotIn("Access-Control-Allow-Credentials", h)
        for bad in (dict(good, Origin="https://evil.example"),
                    dict(good, **{"Access-Control-Request-Method": "PUT"}),
                    dict(good, **{"Access-Control-Request-Headers": "content-type, authorization"})):
            st, h, _ = svc.handle("submit", "OPTIONS", bad, b"")
            self.assertEqual(st, 403)
            self.assertNotIn("Access-Control-Allow-Origin", h)

    def test_window(self):
        svc, store, _, clock = make()
        clock.t = T0 + 86400
        st, h, b = svc.handle("submit", "POST", hdrs(), body(sub()))
        self.assertEqual((st, json.loads(b)["error"]), (403, "closed"))
        self.assertEqual(h["Access-Control-Allow-Origin"], ORIGIN)  # the app can read why and show the fallback
        clock.t = T0 - 7200
        self.assertEqual(svc.handle("progress", "POST", hdrs(), body({"sid": SID, "s": 1}))[0], 403)
        self.assertEqual(store.subs, {})
        self.assertFalse(js(svc.handle("health", "GET", {}, b""))["open"])

    def test_rate_limits(self):
        svc, *_ = make()
        for i in range(60):
            self.assertEqual(svc.handle("progress", "POST", hdrs(), body({"sid": SID, "s": i % 8}))[0], 204)
        st, h, _ = svc.handle("progress", "POST", hdrs(), body({"sid": SID, "s": 1}))
        self.assertEqual(st, 429)
        self.assertTrue(1 <= int(h["Retry-After"]) <= 60)
        self.assertEqual(h["Access-Control-Allow-Origin"], ORIGIN)
        self.assertEqual(svc.handle("progress", "POST", hdrs(), body({"sid": SID2, "s": 1}))[0], 204)

    def test_room_behind_one_nat(self):
        svc, store, relay, _ = make()
        for i in range(300):
            sid = "%032x" % (i + 1)
            self.assertEqual(svc.handle("progress", "POST", hdrs(), body({"sid": sid, "s": 1}))[0], 204)
            st, _, b = svc.handle("submit", "POST", hdrs(), body(sub(sid=sid, code="P%03d" % i)))
            self.assertEqual((st, json.loads(b)), (200, {"ok": True, "forms": "sent"}))
        self.assertEqual(len(store.subs), 300)
        self.assertEqual(len(relay.posts), 300)

    def test_ip_limit_uses_last_forwarded_entry(self):
        svc, *_ = make()
        for i in range(600):
            sid = "%032x" % (i + 1)
            svc.handle("submit", "POST", hdrs(xff="10.0.0.%d, 198.51.100.9" % (i % 250)), body(sub(sid=sid)))
        st, *_ = svc.handle("submit", "POST", hdrs(xff="1.2.3.4, 198.51.100.9"), body(sub(sid="%032x" % 999)))
        self.assertEqual(st, 429)
        st, *_ = svc.handle("submit", "POST", hdrs(xff="198.51.100.10"), body(sub(sid="%032x" % 998)))
        self.assertEqual(st, 200)

    def test_store_outage(self):
        svc, store, _, _ = make()
        store.fail = True
        st, h, b = svc.handle("submit", "POST", hdrs(), body(sub()))
        self.assertEqual((st, json.loads(b)["error"], h["Retry-After"]), (503, "unavailable", "5"))
        self.assertEqual(h["Access-Control-Allow-Origin"], ORIGIN)


class Relaying(unittest.TestCase):
    def submit(self, svc, **kw):
        st, _, b = svc.handle("submit", "POST", hdrs(), body(sub(**kw)))
        self.assertEqual(st, 200)
        return json.loads(b)["forms"]

    def test_sent_once(self):
        svc, store, relay, _ = make()
        self.assertEqual(self.submit(svc), "sent")
        self.assertEqual(self.submit(svc), "sent")
        self.assertEqual(self.submit(svc, dur=1234, v="1.3.1"), "sent")  # same answers: no new Forms response
        self.assertEqual(len(relay.posts), 1)
        row = store.subs[SID]
        self.assertEqual((row["rs"], row["code"], row["ans"], row["t0"]), ("sent", "K7M-Q4X", ALL_Y, T0))
        self.assertEqual(self.submit(svc, ans="N" + ALL_Y[1:]), "sent")  # changed answers are sent again
        self.assertEqual(len(relay.posts), 2)
        self.assertEqual(store.subs[SID]["t0"], T0)

    def test_retry_by_timer(self):
        svc, store, relay, clock = make()
        relay.fail = 1
        self.assertEqual(self.submit(svc), "queued")
        row = store.subs[SID]
        self.assertEqual((row["rs"], row["rn"], row["rt"]), ("pending", 1, T0 + 15))
        self.assertEqual(svc.drain(), 0)  # not due yet
        clock.t += 16
        self.assertEqual(svc.drain(), 1)
        self.assertEqual(store.subs[SID]["rs"], "sent")
        self.assertEqual(self.submit(svc), "sent")
        self.assertEqual(len(relay.posts), 1)

    def test_gives_up_then_rearmed_by_device(self):
        svc, store, relay, clock = make()
        relay.fail = 10 ** 6
        self.submit(svc)
        for _ in range(service.MAX_TRIES + 2):
            clock.t += 301
            svc.drain()
        self.assertEqual(store.subs[SID]["rs"], "failed")
        self.assertEqual(store.subs[SID]["rn"], service.MAX_TRIES)
        relay.fail = 0
        self.assertEqual(self.submit(svc), "sent")
        self.assertEqual(len(relay.posts), 1)

    def test_backoff(self):
        self.assertEqual([service.backoff(n) for n in (1, 2, 3, 4, 5, 6, 30)], [15, 30, 60, 120, 240, 300, 300])

    def test_unexpected_relay_exception_is_retried(self):
        svc, store, relay, clock = make()

        def boom(s, now):
            raise ValueError("unexpected")

        relay.post = boom
        self.assertEqual(self.submit(svc), "queued")
        self.assertEqual((store.subs[SID]["rs"], store.subs[SID]["rn"]), ("pending", 1))

    def test_relay_disabled_or_down_queues(self):
        svc, store, relay, clock = make(relay_enabled=False)
        self.assertEqual(self.submit(svc), "queued")
        self.assertEqual(svc.drain(), 0)
        self.assertEqual(store.subs[SID]["rs"], "pending")
        svc2, store2, relay2, _ = make()
        relay2.up = False
        self.assertEqual(self.submit(svc2), "queued")
        relay2.up = True
        self.assertEqual(svc2.drain(), 1)

    def test_expired_lease_is_reclaimed(self):
        svc, store, relay, clock = make()
        relay.up = False
        self.submit(svc)
        store.put_sub(SID, {"rs": "sending", "rl": int(clock.t) + service.LEASE_S})
        relay.up = True
        self.assertEqual(svc.drain(), 0)  # lease still held by another worker
        clock.t += service.LEASE_S + 1
        self.assertEqual(svc.drain(), 1)
        self.assertEqual(store.subs[SID]["rs"], "sent")

    def test_claim_conflict(self):
        svc, store, relay, clock = make()
        relay.up = False
        self.submit(svc)
        ent = dict(store.get_sub(SID), sid=SID)
        store.put_sub(SID, {"ts": 1})  # someone else changed the row after it was read
        relay.up = True
        self.assertFalse(svc._relay_one(ent, clock.t))
        self.assertEqual(relay.posts, [])

    def test_resubmitted_while_sending(self):
        svc, store, relay, clock = make()
        relay.up = False
        self.submit(svc)
        ent = dict(store.get_sub(SID), sid=SID)
        orig_post = relay.post

        def post_and_resubmit(s, now):
            orig_post(s, now)
            store.put_sub(SID, {"ans": "N" * core.NQ, "ph": "changed", "rs": "pending"})

        relay.post, relay.up = post_and_resubmit, True
        self.assertTrue(svc._relay_one(ent, clock.t))
        self.assertEqual(store.subs[SID]["rs"], "pending")  # newer payload still to be sent

    def test_drain_stops_after_window(self):
        svc, store, relay, clock = make()
        relay.fail = 1
        self.submit(svc)
        clock.t = T0 + 86400 * 3
        self.assertEqual(svc.drain(), 0)

    def test_drain_ignores_invalid_rows(self):
        svc, store, relay, clock = make()
        store.put_sub(SID2, {"code": "=cmd", "ans": ALL_Y, "rs": "pending", "rt": 0})
        self.assertEqual(svc.drain(), 0)
        self.assertEqual(relay.posts, [])

    def test_conflict_type(self):
        store = MemoryStore()
        store.put_sub(SID, {"a": 1})
        with self.assertRaises(Conflict):
            store.put_sub(SID, {"a": 2}, 'W/"999"')


class Stats(unittest.TestCase):
    def auth(self, key=KEY):
        return {"Authorization": "Bearer " + key, "X-Forwarded-For": "198.51.100.1"}

    def test_requires_key(self):
        svc, *_ = make()
        for h in ({}, {"Authorization": "Basic Zm9vOmJhcg=="}, self.auth("x" * 43), self.auth(KEY + "x"),
                  self.auth(""), {"Authorization": "bearer " + KEY}):
            st, rh, b = svc.handle("stats", "GET", h, b"")
            self.assertEqual((st, json.loads(b)["error"]), (401, "unauthorized"))
            self.assertTrue(rh["WWW-Authenticate"].startswith("Bearer "))
        self.assertEqual(svc.handle("stats", "POST", self.auth(), b"")[0], 405)

    def test_guessing_is_limited_but_facilitators_are_not_locked_out(self):
        svc, *_ = make()
        codes = [svc.handle("stats", "GET", self.auth("g" * 43), b"")[0] for _ in range(15)]
        self.assertEqual(codes[:10], [401] * 10)
        self.assertEqual(set(codes[10:]), {429})
        self.assertEqual(svc.handle("stats", "GET", self.auth(), b"")[0], 200)

    def test_no_key_configured(self):
        svc, *_ = make(key_sha256=None)
        self.assertEqual(svc.handle("stats", "GET", self.auth(), b"")[0], 401)

    def test_content(self):
        svc, store, relay, clock = make()
        for i in range(6):
            sid = "%032x" % (i + 1)
            svc.handle("progress", "POST", hdrs(), body({"sid": sid, "s": 1 + i % 6}))
            if i < 5:
                svc.handle("submit", "POST", hdrs(), body(sub(sid=sid, code="P%d" % (10 + i))))
        st, h, b = svc.handle("stats", "GET", self.auth(), b"")
        self.assertEqual(st, 200)
        self.assertNotIn("Access-Control-Allow-Origin", h)
        d = json.loads(b)
        self.assertEqual((d["n"], d["open"], d["truncated"], d["relay"]), (5, True, False, {"sent": 5}))
        self.assertEqual((d["progress"]["completed"], d["progress"]["inProgress"], d["progress"]["devices"]),
                         (5, 1, 6))
        self.assertEqual(d["overall"]["mean"], 100)
        text = b.decode()
        for secret in ("203.0.113.7", "K7M", "P10", "000000000000000000000001"):
            self.assertNotIn(secret, text)  # no IPs, participant codes or device ids leave the API

    def test_cached_for_ttl(self):
        svc, store, relay, clock = make()
        a = json.loads(svc.handle("stats", "GET", self.auth(), b"")[2])
        svc.handle("submit", "POST", hdrs(), body(sub()))
        b = json.loads(svc.handle("stats", "GET", self.auth(), b"")[2])
        self.assertEqual(a["progress"]["completed"], b["progress"]["completed"])
        clock.t += service.STATS_TTL
        c = json.loads(svc.handle("stats", "GET", self.auth(), b"")[2])
        self.assertEqual(c["progress"]["completed"], 1)


class ConfigTests(unittest.TestCase):
    def test_from_env(self):
        cfg = service.Config.from_env({"ALLOWED_ORIGINS": ORIGIN + ", " + ORIGIN2,
                                       "FACILITATOR_KEY_SHA256": KEY_SHA.hex().upper(), "INTAKE_FROM": "1",
                                       "INTAKE_UNTIL": "2", "RELAY_ENABLED": "0"})
        self.assertEqual(cfg.origins, frozenset({ORIGIN, ORIGIN2}))
        self.assertEqual((cfg.key_sha256, cfg.intake_from, cfg.intake_until, cfg.relay_enabled),
                         (KEY_SHA, 1, 2, False))
        empty = service.Config.from_env({})
        self.assertEqual((empty.origins, empty.key_sha256, empty.intake_until), (frozenset(), None, 0))
        for bad in ("http://araviiindb.github.io", "https://a.example/", "*", "https://a.example:8443"):
            with self.assertRaises(ValueError):
                service.Config.from_env({"ALLOWED_ORIGINS": bad})
        self.assertIsNone(service.Config.from_env({"FACILITATOR_KEY_SHA256": "abc"}).key_sha256)


if __name__ == "__main__":
    unittest.main()
