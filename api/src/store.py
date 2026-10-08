"""Azure Table storage over REST with the Function App's managed identity (no account keys exist), plus an
in-memory store with the same interface and ETag semantics for tests."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate

API_VERSION = "2020-12-06"
MAX_RESP = 8 * 1024 * 1024
PK_SUB, PK_PROG = "s", "p"


class StoreError(Exception):
    pass


class Conflict(StoreError):
    """Precondition failed: the row changed since it was read."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class ManagedIdentityToken:
    """App Service / Functions managed identity endpoint (IDENTITY_ENDPOINT + IDENTITY_HEADER)."""

    def __init__(self, resource="https://storage.azure.com/"):
        self.resource = resource
        self._tok, self._exp = None, 0
        self._lock = threading.Lock()

    def __call__(self):
        now = time.time()
        with self._lock:
            if self._tok and now < self._exp - 300:
                return self._tok
            ep, hdr = os.environ["IDENTITY_ENDPOINT"], os.environ["IDENTITY_HEADER"]
            url = ep + "?" + urllib.parse.urlencode({"resource": self.resource, "api-version": "2019-08-01"})
            req = urllib.request.Request(url, headers={"X-IDENTITY-HEADER": hdr})
            with _OPENER.open(req, timeout=10) as r:
                d = json.loads(r.read(65536))
            self._tok, self._exp = d["access_token"], int(d["expires_on"])
            return self._tok


def _key(s):
    return s.replace("'", "''")


class TableStore:
    def __init__(self, account, prefix, token):
        assert account.isalnum() and account.islower() and 3 <= len(account) <= 24
        assert prefix.isalnum() and prefix[:1].isalpha() and len(prefix) <= 20
        self.base = "https://%s.table.core.windows.net/" % account
        self.t_sub, self.t_prog = prefix + "subs", prefix + "prog"
        self.token = token
        self._ready = False
        self._lock = threading.Lock()

    def _req(self, method, path, body=None, extra=None, ok=(200, 201, 204)):
        h = {
            "Authorization": "Bearer " + self.token(),
            "x-ms-version": API_VERSION,
            "x-ms-date": formatdate(usegmt=True),
            "Accept": "application/json;odata=minimalmetadata",
            "DataServiceVersion": "3.0;NetFx",
            "MaxDataServiceVersion": "3.0;NetFx",
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            h["Content-Type"] = "application/json"
        if extra:
            h.update(extra)
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with _OPENER.open(req, timeout=15) as r:
                raw = r.read(MAX_RESP)
                return r.status, r.headers, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            e.read(65536)
            if e.code in ok:
                return e.code, e.headers, None
            if e.code == 412:
                raise Conflict() from None
            raise StoreError("table %s %s -> %d" % (method, path.split("(")[0].split("?")[0], e.code)) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise StoreError("table %s: %s" % (method, type(e).__name__)) from None

    def ensure(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            for t in (self.t_sub, self.t_prog):
                self._req("POST", "Tables", {"TableName": t}, {"Prefer": "return-no-content"}, ok=(201, 204, 409))
            self._ready = True

    def _ent(self, table, pk, rk):
        return "%s(PartitionKey='%s',RowKey='%s')" % (table, _key(pk), _key(rk))

    def _merge(self, table, pk, rk, props, etag):
        st, hd, _ = self._req("MERGE", self._ent(table, pk, rk), props, {"If-Match": etag} if etag else None)
        return hd.get("ETag")

    def _get(self, table, pk, rk):
        st, hd, d = self._req("GET", self._ent(table, pk, rk), ok=(200, 404))
        if st == 404:
            return None
        d = dict(d or {})
        d["_etag"] = d.pop("odata.etag", None) or hd.get("ETag")
        return d

    def _query(self, table, flt, select, cap):
        out, nxt = [], None
        while len(out) < cap:
            qs = "$filter=" + urllib.parse.quote(flt, safe="") + "&$select=" + urllib.parse.quote(select, safe=",")
            if nxt:
                qs += "&" + urllib.parse.urlencode(nxt)
            st, hd, d = self._req("GET", table + "()?" + qs)
            for e in (d or {}).get("value", []):
                e["_etag"] = e.pop("odata.etag", None)
                out.append(e)
            npk, nrk = hd.get("x-ms-continuation-NextPartitionKey"), hd.get("x-ms-continuation-NextRowKey")
            if not npk:
                break
            nxt = {"NextPartitionKey": npk, "NextRowKey": nrk or ""}
        return out[:cap]

    # ---- interface used by the service ----
    def put_progress(self, sid, stage, now):
        self.ensure()
        self._merge(self.t_prog, PK_PROG, sid, {"s": stage, "ts": int(now)}, None)

    def get_sub(self, sid):
        self.ensure()
        return self._get(self.t_sub, PK_SUB, sid)

    def put_sub(self, sid, props, etag=None):
        """Upsert (etag None) or conditional update. Returns the new ETag; raises Conflict on 412."""
        self.ensure()
        return self._merge(self.t_sub, PK_SUB, sid, props, etag)

    def list_subs(self, cap):
        self.ensure()
        return [dict(e, sid=e.get("RowKey")) for e in self._query(
            self.t_sub, "PartitionKey eq 's'", "RowKey,code,sector,size,role,ans,rs,ts", cap)]

    def list_progress(self, cap):
        self.ensure()
        return [dict(e, sid=e.get("RowKey")) for e in self._query(self.t_prog, "PartitionKey eq 'p'", "RowKey,s,ts", cap)]

    def list_relay_work(self, cap):
        self.ensure()
        return [dict(e, sid=e.get("RowKey")) for e in self._query(
            self.t_sub, "PartitionKey eq 's' and (rs eq 'pending' or rs eq 'sending')",
            "RowKey,code,sector,size,role,ans,dur,ts,ph,rs,rn,rt,rl", cap)]


class MemoryStore:
    """Same interface and ETag behaviour as TableStore, for tests."""

    def __init__(self):
        self.subs, self.prog = {}, {}
        self._n = 0
        self._lock = threading.Lock()
        self.fail = False

    def _check(self):
        if self.fail:
            raise StoreError("simulated outage")

    def _etag(self):
        self._n += 1
        return 'W/"%d"' % self._n

    def put_progress(self, sid, stage, now):
        self._check()
        with self._lock:
            self.prog[sid] = {"s": stage, "ts": int(now)}

    def get_sub(self, sid):
        self._check()
        with self._lock:
            e = self.subs.get(sid)
            return dict(e) if e else None

    def put_sub(self, sid, props, etag=None):
        self._check()
        with self._lock:
            cur = self.subs.get(sid)
            if etag is not None and (cur is None or cur["_etag"] != etag):
                raise Conflict()
            new = dict(cur or {})
            new.update(props)
            new["_etag"] = self._etag()
            self.subs[sid] = new
            return new["_etag"]

    def list_subs(self, cap):
        self._check()
        with self._lock:
            return [dict(v, sid=k) for k, v in list(self.subs.items())[:cap]]

    def list_progress(self, cap):
        self._check()
        with self._lock:
            return [dict(v, sid=k) for k, v in list(self.prog.items())[:cap]]

    def list_relay_work(self, cap):
        self._check()
        with self._lock:
            return [dict(v, sid=k) for k, v in self.subs.items() if v.get("rs") in ("pending", "sending")][:cap]
