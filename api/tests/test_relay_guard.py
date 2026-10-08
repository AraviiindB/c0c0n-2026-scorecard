import io
import json
import unittest
import urllib.error

import guard
import relay
from helpers import ALL_Y, core, sub

RATINGS = ["\u2713 Implemented, coverage confirmed, evidence available",
           "\u25d0 Partial, partially implemented or coverage incomplete", "\u2717 Not implemented", "? Unknown"]


def form_questions(ratings=RATINGS, drop=None):
    qs = [{"id": "r_p01", "title": "P-01 \u00b7 Participant code", "questionInfo": None}]
    for code, key in relay.PROFILE_Q:
        qs.append({"id": "r_" + code, "title": code + " \u00b7 x",
                   "questionInfo": json.dumps({"Choices": [{"Description": o} for o in core.PROFILE[key]]})})
    for q in core.QUESTIONS:
        if q["code"] != drop:
            qs.append({"id": "r_" + q["code"], "title": q["code"] + " \u00b7 " + q["short"],
                       "questionInfo": json.dumps({"Choices": [{"Description": r} for r in ratings]})})
    qs.append({"id": "r_section", "title": "S1 The poisoned package", "questionInfo": None})
    return qs


class Mapping(unittest.TestCase):
    def test_build(self):
        m = relay.build_mapping(form_questions())
        self.assertEqual(m["SD-01"]["id"], "r_SD-01")
        ans = relay.build_answers(m, sub(ans="YPNU" * 10, sector=core.PROFILE["sector"][0]))
        self.assertEqual(ans[0], {"questionId": "r_p01", "answer1": "K7M-Q4X"})
        self.assertEqual(ans[1], {"questionId": "r_P-02", "answer1": core.PROFILE["sector"][0]})
        self.assertEqual(len(ans), 2 + core.NQ)
        rated = ans[2:]
        self.assertEqual([a["questionId"] for a in rated], ["r_" + c for c in core.CODES])
        self.assertEqual([a["answer1"] for a in rated[:4]], RATINGS)

    def test_profile_value_not_on_form_is_omitted(self):
        m = relay.build_mapping(form_questions())
        m["P-03"]["choices"] = []
        ans = relay.build_answers(m, sub(size=core.PROFILE["size"][0]))
        self.assertNotIn("r_P-03", [a["questionId"] for a in ans])

    def test_rejects_unexpected_forms(self):
        with self.assertRaises(relay.RelayError):
            relay.build_mapping(form_questions(drop="IR-10"))
        with self.assertRaises(relay.RelayError):
            relay.build_mapping(form_questions(ratings=RATINGS[:3]))
        with self.assertRaises(relay.RelayError):
            relay.build_mapping(form_questions(ratings=RATINGS + ["\u2713 again"]))
        with self.assertRaises(relay.RelayError):
            relay.build_mapping([])

    def test_catalogue_ratings_match_symbols(self):
        try:
            import catalogue
        except ImportError:
            self.skipTest("catalogue.py not available")
        for i, L in enumerate("YPNU"):
            self.assertTrue(catalogue.RATING_OPTIONS[i].startswith(relay.SYMBOL[L]))


class Urls(unittest.TestCase):
    def test_check_url(self):
        relay._check_url("https://forms.office.com/formapi/x")
        relay._check_url("https://forms.cloud.microsoft/x")
        for bad in ("http://forms.office.com/x", "https://forms.office.com.evil.example/x",
                    "https://evil.example/forms.office.com", "https://user@forms.office.com/x",
                    "https://forms.office.com:8443/x", "ftp://forms.office.com/x", "https://office.com/x"):
            with self.assertRaises(relay.RelayError, msg=bad):
                relay._check_url(bad)

    def test_js_string(self):
        page = 'x {"prefetchFormUrl":"https:\\/\\/forms.office.com\\/formapi\\/a","antiForgeryToken":"t\\u0026k"}'
        self.assertEqual(relay._js_string(page, "prefetchFormUrl"), "https://forms.office.com/formapi/a")
        self.assertEqual(relay._js_string(page, "antiForgeryToken"), "t&k")
        with self.assertRaises(relay.RelayError):
            relay._js_string(page, "missing")

    def test_form_id_validation(self):
        with self.assertRaises(ValueError):
            relay.Relay("../../x")
        with self.assertRaises(ValueError):
            relay.Relay("")
        relay.Relay("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-AbCdEfGhIj")


class FakeResp:
    def __init__(self, status, url, data=b"{}"):
        self.status, self._url, self._data = status, url, io.BytesIO(data)

    def geturl(self):
        return self._url

    def read(self, n=-1):
        return self._data.read(n)


class FakeOpener:
    def __init__(self, script):
        self.script, self.requests = list(script), []

    def open(self, req, timeout=None):
        self.requests.append(req)
        st = self.script.pop(0)
        if st >= 400:
            raise urllib.error.HTTPError(req.full_url, st, "x", {}, io.BytesIO(b"err"))
        return FakeResp(st, req.full_url)


POST_URL = "https://forms.office.com/formapi/api/t/users/u/forms('F')/responses"


class Posting(unittest.TestCase):
    def relay_with(self, *scripts):
        r = relay.Relay("A" * 40)
        sessions = [relay._Session(FakeOpener(s), "tok", POST_URL, relay.build_mapping(form_questions()))
                    for s in scripts]
        made = []

        def new_session():
            made.append(1)
            return sessions[len(made) - 1]

        r._new_session = new_session
        return r, sessions, made

    def test_success(self):
        r, sessions, _ = self.relay_with([201])
        r.post(dict(sub(), dur=99999), 2000000000)
        req = sessions[0].opener.requests[0]
        self.assertEqual((req.get_method(), req.full_url), ("POST", POST_URL))
        self.assertEqual(req.get_header("__requestverificationtoken"), "tok")
        b = json.loads(req.data)
        self.assertEqual(b["submitDate"], "2033-05-18T03:33:20.000Z")
        self.assertEqual(b["startDate"], "2033-05-18T01:33:20.000Z")  # duration capped at 2 hours
        self.assertEqual(len(json.loads(b["answers"])), 1 + core.NQ)
        self.assertTrue(r.available())

    def test_stale_session_retried_once(self):
        r, sessions, made = self.relay_with([403], [201])
        r.post(sub(), 2000000000)
        self.assertEqual(len(made), 2)
        r2, _, made2 = self.relay_with([403], [403])
        with self.assertRaises(relay.RelayError):
            r2.post(sub(), 2000000000)
        r3, _, made3 = self.relay_with([500])
        with self.assertRaises(relay.RelayError):
            r3.post(sub(), 2000000000)
        self.assertEqual(len(made3), 1)

    def test_session_reused(self):
        r, sessions, made = self.relay_with([201, 201, 201])
        for _ in range(3):
            r.post(sub(), 2000000000)
        self.assertEqual(len(made), 1)

    def test_circuit_breaker(self):
        r, _, _ = self.relay_with([500] * 5)
        for _ in range(5):
            with self.assertRaises(relay.RelayError):
                r.post(sub(), 2000000000)
        self.assertFalse(r.available())

    def test_unexpected_exceptions_become_relay_errors(self):
        import http.client
        r, sessions, _ = self.relay_with([201])

        def broken(req, timeout=None):
            raise http.client.IncompleteRead(b"")

        sessions[0].opener.open = broken
        with self.assertRaises(relay.RelayError):
            r.post(sub(), 2000000000)

    def test_network_errors_become_relay_errors(self):
        r = relay.Relay("A" * 40)

        def boom():
            raise urllib.error.URLError("down")

        r._new_session = boom
        with self.assertRaises(relay.RelayError):
            r.post(sub(), 2000000000)


class Guards(unittest.TestCase):
    def test_client_key(self):
        k = guard.client_key
        self.assertEqual(k({"x-forwarded-for": "203.0.113.7:5000"}), k({"x-forwarded-for": "203.0.113.7"}))
        self.assertEqual(k({"x-forwarded-for": "1.1.1.1, 203.0.113.7"}), k({"x-forwarded-for": "203.0.113.7"}))
        self.assertNotEqual(k({"x-forwarded-for": "203.0.113.7"}), k({"x-forwarded-for": "203.0.113.8"}))
        self.assertEqual(k({"x-forwarded-for": "[2001:db8:1:2:3::1]:443"}), k({"x-forwarded-for": "2001:db8:1:2::9"}))
        self.assertNotEqual(k({"x-forwarded-for": "2001:db8:1:2::1"}), k({"x-forwarded-for": "2001:db8:1:3::1"}))
        self.assertEqual(k({}), k({"x-forwarded-for": "garbage"}))
        self.assertNotIn("203", k({"x-forwarded-for": "203.0.113.7"}))

    def test_limiter_window(self):
        lim = guard.Limiter(2)
        self.assertEqual([lim.hit("a", 60), lim.hit("a", 61), lim.hit("a", 62), lim.hit("b", 62)],
                         [True, True, False, True])
        self.assertEqual(lim.retry_after(62), 58)
        self.assertTrue(lim.hit("a", 120))

    def test_limiter_memory_bound(self):
        lim = guard.Limiter(1, max_keys=10)
        for i in range(25):
            lim.hit(str(i), 0)
        self.assertLessEqual(len(lim._n), 10)

    def test_json_ct(self):
        for good in ("application/json", "Application/JSON; charset=utf-8", "text/plain", "text/plain;charset=UTF-8",
                     'text/plain; charset="utf-8"'):
            self.assertTrue(guard.body_ct_ok(good), good)
        for bad in ("", "text/html", "text/plain; charset=iso-8859-1", "text/plain;charset=utf-8;x=1", "application/jsonx",
                    "multipart/form-data", "application/json-patch+json", "application/x-www-form-urlencoded",
                    "text/plainx", "text/plain, application/json"):
            self.assertFalse(guard.body_ct_ok(bad), bad)

    def test_cors(self):
        allowed = frozenset({"https://a.example"})
        self.assertEqual(guard.cors("https://a.example", allowed),
                         {"Access-Control-Allow-Origin": "https://a.example", "Vary": "Origin"})
        self.assertEqual(guard.cors("https://b.example", allowed), {"Vary": "Origin"})
        self.assertEqual(guard.cors("", allowed), {"Vary": "Origin"})


if __name__ == "__main__":
    unittest.main()
