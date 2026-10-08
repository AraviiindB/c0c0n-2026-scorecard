"""Pure logic for the session results service: request validation, scoring and room aggregates. No I/O.

Scoring is a port of reference_score.py (integer maths, half-up rounding like Excel ROUND) and must match the
participant app exactly; tests/test_core.py checks this against the reference implementation.
"""
import hashlib
import json
import os
import re
import statistics

_HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_HERE, "catalog.json"), encoding="utf-8") as _f:
    CAT = json.load(_f)

VERSION = CAT["version"]
QUESTIONS = CAT["questions"]
CODES = [q["code"] for q in QUESTIONS]
NQ = len(CODES)
AREAS = CAT["areas"]
AREA_TOTAL = {a["code"]: sum(q["weight"] for q in QUESTIONS if q["area"] == a["code"]) for a in AREAS}
TOTAL_W = sum(q["weight"] for q in QUESTIONS)
BANDS = CAT["bands"]
PROFILE = CAT["profile"]
PROFILE_KEYS = ("sector", "size", "role")
SCENARIOS = CAT["scenarios"]
STAGES = len(SCENARIOS) + 2  # 0 = about you, 1..6 = scenarios, 7 = results
STAGE_NAMES = ["About you"] + ["%s %s" % (s["id"], s["title"]) for s in SCENARIOS] + ["Results (sending)"]

SID_RE = re.compile(r"[0-9a-f]{32}")
CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9 _-]{1,19}")
ANS_RE = re.compile("[YPNU]{%d}" % NQ)
VER_RE = re.compile(r"[0-9A-Za-z.+-]{1,16}")
MAX_BODY = 1024
MAX_DUR = 86400
CREDIT2 = {"Y": 2, "P": 1, "N": 0, "U": 0}
MIN_ROOM = 5   # room results are shown only once this many participants are included (same rule as the workbook)
MIN_GROUP = 5  # sector averages only for groups at least this large
ACTIVE_S = 900

assert NQ == 40 and len(set(CODES)) == NQ and STAGES == 8
assert set(PROFILE) == set(PROFILE_KEYS)
assert sum(a["weight"] for a in AREAS) == 100


class Invalid(ValueError):
    """Request rejected by validation. The reason is for tests only and is never sent to clients."""


def _no_duplicate_keys(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise Invalid("duplicate key")
        d[k] = v
    return d


def _no_constants(_name):
    raise Invalid("non-finite number")


def parse_json(body, allowed, required):
    if not isinstance(body, (bytes, bytearray)) or not 0 < len(body) <= MAX_BODY:
        raise Invalid("size")
    try:
        d = json.loads(bytes(body).decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                       parse_constant=_no_constants)
    except Invalid:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Invalid("json") from None
    if not isinstance(d, dict):
        raise Invalid("not an object")
    keys = set(d)
    if not keys <= allowed or not required <= keys:
        raise Invalid("fields")
    return d


def valid_sid(v):
    return isinstance(v, str) and SID_RE.fullmatch(v) is not None


def _sid(v):
    if not valid_sid(v):
        raise Invalid("sid")
    return v


def parse_progress(body):
    d = parse_json(body, {"sid", "s"}, {"sid", "s"})
    s = d["s"]
    if type(s) is not int or not 0 <= s < STAGES:  # type() also rejects booleans
        raise Invalid("stage")
    return _sid(d["sid"]), s


def norm_code(s):
    return " ".join(s.strip().upper().split())


def valid_code(code):
    return (isinstance(code, str) and code.isascii() and CODE_RE.fullmatch(code) is not None
            and code == norm_code(code))


def parse_submit(body):
    d = parse_json(body, {"sid", "code", "sector", "size", "role", "ans", "v", "dur"}, {"sid", "code", "ans"})
    if not valid_code(d["code"]):
        raise Invalid("code")
    out = {"sid": _sid(d["sid"]), "code": d["code"]}
    for k in PROFILE_KEYS:
        v = d.get(k, "")
        if not isinstance(v, str) or (v and v not in PROFILE[k]):
            raise Invalid(k)
        out[k] = v
    ans = d["ans"]
    if not (isinstance(ans, str) and ANS_RE.fullmatch(ans)):
        raise Invalid("ans")
    out["ans"] = ans
    v = d.get("v", "")
    if not (isinstance(v, str) and (v == "" or VER_RE.fullmatch(v))):
        raise Invalid("v")
    out["v"] = v
    dur = d.get("dur", 0)
    if type(dur) is not int or not 0 <= dur <= MAX_DUR:
        raise Invalid("dur")
    out["dur"] = dur
    return out


def valid_row(x):
    """Rows read back from storage are re-checked before they are counted."""
    return (valid_sid(x.get("sid")) and valid_code(x.get("code")) and isinstance(x.get("ans"), str)
            and ANS_RE.fullmatch(x["ans"]) is not None
            and all(x.get(k, "") == "" or x.get(k) in PROFILE[k] for k in PROFILE_KEYS))


def payload_hash(sub):
    """Identity of what a submission says (not when): unchanged re-sends are no-ops."""
    canon = json.dumps([sub["code"], sub["sector"], sub["size"], sub["role"], sub["ans"]], ensure_ascii=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("ascii")).hexdigest()[:32]


def is_test(code):
    return code.startswith("TEST")


def rhu(n, d):
    """Half-up rounding of n/d for non-negative integers (same as the app and Excel ROUND)."""
    return (2 * n + d) // (2 * d)


def band_index(s):
    i = 0
    for k, b in enumerate(BANDS):
        if s >= b["lo"]:
            i = k
    return i


def score(ans):
    a = dict(zip(CODES, ans))
    areas, total = {}, 0
    for ar in AREAS:
        e2 = sum(q["weight"] * CREDIT2[a[q["code"]]] for q in QUESTIONS if q["area"] == ar["code"])
        s = rhu(e2 * 100, 2 * AREA_TOTAL[ar["code"]])
        areas[ar["code"]] = s
        total += s * ar["weight"]
    unknown = sum(q["weight"] for q in QUESTIONS if a[q["code"]] == "U")
    overall = rhu(total, 100)
    return {"areas": areas, "overall": overall, "band": band_index(overall), "unc": rhu(unknown * 100, TOTAL_W),
            "crit": [q["code"] for q in QUESTIONS if q["critical"] and a[q["code"]] in ("N", "U")]}


def _pct(n, d):
    return rhu(100 * n, d) if d else 0


def _progress(subs, progs, now):
    done = {x["sid"] for x in subs}
    stages, recent = [0] * STAGES, 0
    for p in progs:
        if p["sid"] in done:
            continue
        stages[p["s"]] += 1
        if 0 <= now - p["ts"] <= ACTIVE_S:
            recent += 1
    return {"devices": len(done | {p["sid"] for p in progs}), "stages": stages, "completed": len(done),
            "inProgress": sum(stages), "activeRecent": recent}


def aggregate(subs, progs, now):
    """Room view. subs: [{sid, code, sector, size, role, ans, rs}], progs: [{sid, s, ts}] (validated rows)."""
    real = [x for x in subs if not is_test(x["code"])]
    use = real or subs
    n = len(use)
    res = {"v": VERSION, "now": now, "n": n, "minRoom": MIN_ROOM, "testOnly": bool(subs) and not real,
           "testHidden": len(subs) - len(real) if real else 0, "stageNames": STAGE_NAMES,
           "bandNames": [b["name"] for b in BANDS], "bandLo": [b["lo"] for b in BANDS],
           "progress": _progress(subs, progs, now)}
    relay = {}
    for x in subs:
        k = x.get("rs") or "pending"
        relay[k] = relay.get(k, 0) + 1
    res["relay"] = relay
    if n < MIN_ROOM:
        return res
    sc = [score(x["ans"]) for x in use]
    ov = [s["overall"] for s in sc]
    # No minimum or maximum: with few participants either one would expose a single person's exact score.
    res["overall"] = {"mean": rhu(sum(ov), n), "median": statistics.median(ov)}
    res["band"] = band_index(res["overall"]["mean"])
    res["bands"] = [sum(1 for s in sc if s["band"] == k) for k in range(len(BANDS))]
    res["hist"] = [sum(1 for v in ov if min(v // 10, 9) == k) for k in range(10)]
    res["areas"] = []
    for ar in AREAS:
        vals = [s["areas"][ar["code"]] for s in sc]
        mean = rhu(sum(vals), n)
        res["areas"].append({"code": ar["code"], "name": ar["name"], "weight": ar["weight"], "mean": mean,
                             "band": band_index(mean),
                             "bands": [sum(1 for v in vals if band_index(v) == k) for k in range(len(BANDS))]})
    res["unc"] = rhu(sum(s["unc"] for s in sc), n)
    mix = {L: sum(x["ans"].count(L) for x in use) for L in "YPNU"}
    res["mix"] = {L: _pct(v, n * NQ) for L, v in mix.items()}
    per = []
    for i, q in enumerate(QUESTIONS):
        c = {L: sum(1 for x in use if x["ans"][i] == L) for L in "YPNU"}
        per.append({"i": i, "q": q, "c": c, "miss2": 2 * (c["N"] + c["U"]) + c["P"]})
    top = sorted(per, key=lambda p: (-p["miss2"], -p["q"]["critical"], -p["q"]["weight"], p["i"]))[:6]
    res["topGaps"] = [{"code": p["q"]["code"], "short": p["q"]["short"], "area": p["q"]["area"],
                       "critical": p["q"]["critical"], "pct": {L: _pct(p["c"][L], n) for L in "YPNU"}} for p in top]
    crit = sorted((p for p in per if p["q"]["critical"]), key=lambda p: (-(p["c"]["N"] + p["c"]["U"]), p["i"]))
    res["critical"] = [{"code": p["q"]["code"], "gap": p["q"]["gap"], "pct": _pct(p["c"]["N"] + p["c"]["U"], n)}
                       for p in crit[:3]]
    best = sorted(per, key=lambda p: (-p["c"]["Y"], p["i"]))[:3]
    res["strongest"] = [{"code": p["q"]["code"], "short": p["q"]["short"], "pct": _pct(p["c"]["Y"], n)} for p in best]
    groups = {}
    for x, s in zip(use, sc):
        if x.get("sector"):
            groups.setdefault(x["sector"], []).append(s["overall"])
    shown = sorted(((k, v) for k, v in groups.items() if len(v) >= MIN_GROUP), key=lambda kv: (-len(kv[1]), kv[0]))
    res["sectors"] = [{"name": k, "n": len(v), "mean": rhu(sum(v), len(v)), "band": band_index(rhu(sum(v), len(v)))}
                      for k, v in shown]
    return res
