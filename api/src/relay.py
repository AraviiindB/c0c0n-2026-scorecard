"""Relay of stored submissions to the session's Microsoft Form, as an anonymous responder (the same requests a
browser makes when someone fills in the form). Only the configured form on Microsoft's Forms hosts is ever
contacted; redirects elsewhere, unexpected form IDs and oversized responses are refused."""
import http.cookiejar
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

import core

HOSTS = frozenset({"forms.office.com", "forms.cloud.microsoft"})
PAGE = "https://forms.office.com/Pages/ResponsePage.aspx?id="
UA = "Mozilla/5.0 (compatible; c0c0n-scorecard-relay/%s; +https://github.com/AraviiindB/c0c0n-2026-scorecard)" % (
    core.VERSION)
SYMBOL = {"Y": "\u2713", "P": "\u25d0", "N": "\u2717", "U": "?"}
TITLE_CODE = re.compile(r"([A-Z]{1,2}-\d{2}) \u00b7 ")
FORM_ID_RE = re.compile(r"[A-Za-z0-9_-]{20,200}")
GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
API_PATH = re.compile(r"(/formapi/api/%s/users/%s)/light/runtimeForms\('([A-Za-z0-9_-]+)'\)" % (GUID, GUID))
PROFILE_Q = (("P-02", "sector"), ("P-03", "size"), ("P-04", "role"))
MAX_PAGE, MAX_FORM, MAX_POST = 4 << 20, 4 << 20, 1 << 16


class RelayError(Exception):
    pass


class _Redirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() != "GET":
            raise RelayError("redirect on %s" % req.get_method())
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in HOSTS or u.port not in (None, 443) or u.username or u.password:
        raise RelayError("unexpected host")
    return u


def _read(resp, cap):
    data = resp.read(cap + 1)
    if len(data) > cap:
        raise RelayError("response too large")
    return data


def _js_string(page, name):
    m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.){1,4000})"' % name, page)
    if not m:
        raise RelayError("%s not found" % name)
    return json.loads('"%s"' % m.group(1))


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def build_mapping(questions):
    """Question IDs and choices of the live form, keyed by the code at the start of each title."""
    m = {}
    for q in questions or []:
        mm = TITLE_CODE.match(q.get("title") or "")
        if not mm or not isinstance(q.get("id"), str):
            continue
        try:
            info = json.loads(q.get("questionInfo") or "{}")
        except ValueError:
            info = {}
        ch = [c.get("Description") for c in (info.get("Choices") or []) if isinstance(c, dict)]
        m[mm.group(1)] = {"id": q["id"], "choices": [c for c in ch if isinstance(c, str)]}
    missing = ({"P-01"} | set(core.CODES)) - set(m)
    if missing:
        raise RelayError("form is missing %d expected questions" % len(missing))
    for code in core.CODES:
        for sym in SYMBOL.values():
            if sum(1 for c in m[code]["choices"] if c.startswith(sym)) != 1:
                raise RelayError("rating choices of %s do not match" % code)
    return m


def build_answers(mapping, sub):
    out = [{"questionId": mapping["P-01"]["id"], "answer1": sub["code"]}]
    for code, key in PROFILE_Q:
        v = sub.get(key) or ""
        if v and code in mapping and v in mapping[code]["choices"]:
            out.append({"questionId": mapping[code]["id"], "answer1": v})
    for code, letter in zip(core.CODES, sub["ans"]):
        choice = next(c for c in mapping[code]["choices"] if c.startswith(SYMBOL[letter]))
        out.append({"questionId": mapping[code]["id"], "answer1": choice})
    return out


class _Session:
    def __init__(self, opener, token, post_url, mapping):
        self.opener, self.token, self.post_url, self.mapping = opener, token, post_url, mapping
        self.created = time.monotonic()


class Relay:
    def __init__(self, form_id, ttl=600):
        if not FORM_ID_RE.fullmatch(form_id or ""):
            raise ValueError("FORM_ID")
        self.form_id = form_id
        self.ttl = ttl
        self._s = None
        self._lock = threading.Lock()
        self._fails = 0
        self._open_until = 0.0

    # ---- circuit breaker: after repeated failures, pause relaying for a minute ----
    def available(self):
        return time.monotonic() >= self._open_until

    def _result(self, ok):
        with self._lock:
            if ok:
                self._fails = 0
            else:
                self._fails += 1
                if self._fails >= 5:
                    self._open_until = time.monotonic() + 60
                    self._fails = 0

    def _new_session(self):
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                         urllib.request.HTTPSHandler(context=ssl.create_default_context()),
                                         _Redirect())
        r = op.open(urllib.request.Request(PAGE + urllib.parse.quote(self.form_id, safe=""),
                                           headers={"User-Agent": UA, "Accept": "text/html"}), timeout=15)
        _check_url(r.geturl())
        page = _read(r, MAX_PAGE).decode("utf-8", "replace")
        api, tok = _js_string(page, "prefetchFormUrl"), _js_string(page, "antiForgeryToken")
        u = _check_url(api)
        m = API_PATH.match(u.path)
        if not m or m.group(2) != self.form_id:
            raise RelayError("unexpected form API path")
        r = op.open(urllib.request.Request(api, headers={"User-Agent": UA, "Accept": "application/json",
                                                         "__requestverificationtoken": tok}), timeout=15)
        _check_url(r.geturl())
        form = json.loads(_read(r, MAX_FORM))
        if form.get("status") not in (None, "Active"):
            raise RelayError("form is not accepting responses")
        mapping = build_mapping(form.get("questions"))
        post_url = "https://%s%s/forms('%s')/responses" % (u.hostname, m.group(1), self.form_id)
        return _Session(op, tok, post_url, mapping)

    def _session(self, fresh=False):
        with self._lock:
            s = self._s
            if fresh or s is None or time.monotonic() - s.created > self.ttl:
                s = self._s = None
        if s is None:
            s = self._new_session()
            with self._lock:
                self._s = s
        return s

    def post(self, sub, now):
        """Send one submission. Returns normally on success, raises RelayError otherwise."""
        try:
            for attempt in (0, 1):
                s = self._session(fresh=attempt == 1)
                dur = min(max(int(sub.get("dur") or 0), 60), 7200)
                body = {"startDate": _iso(now - dur), "submitDate": _iso(now),
                        "answers": json.dumps(build_answers(s.mapping, sub), ensure_ascii=False)}
                hdr = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json",
                       "__requestverificationtoken": s.token, "x-ms-form-request-source": "ms-formweb",
                       "x-ms-form-request-ring": "msa", "odata-version": "4.0", "odata-maxversion": "4.0",
                       "x-correlationid": str(uuid.uuid4()), "x-usersessionid": str(uuid.uuid4())}
                req = urllib.request.Request(s.post_url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                             headers=hdr, method="POST")
                try:
                    r = s.opener.open(req, timeout=20)
                    _read(r, MAX_POST)
                    if r.status in (200, 201) and r.geturl() == s.post_url:
                        self._result(True)
                        return
                    raise RelayError("unexpected status %d" % r.status)
                except urllib.error.HTTPError as e:
                    e.read(MAX_POST)
                    if attempt == 0 and e.code in (400, 401, 403, 419, 440):
                        continue  # stale session or token: retry once with a fresh one
                    raise RelayError("forms status %d" % e.code) from None
            raise RelayError("forms rejected the response")
        except RelayError:
            self._result(False)
            raise
        except Exception as e:  # network, TLS, HTTP parsing or unexpected page content: all retried later
            self._result(False)
            raise RelayError(type(e).__name__) from None
