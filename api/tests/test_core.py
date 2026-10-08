import json
import random
import unittest

from helpers import ALL_Y, SID, body, core, sub

try:
    import reference_score as rs
except ImportError:  # published repo: the oracle is not shipped
    rs = None


def letters(d):
    return "".join(d[c] for c in core.CODES)


@unittest.skipIf(rs is None, "reference_score.py not available")
class ParityWithReference(unittest.TestCase):
    def check(self, ans):
        got = core.score(letters(ans))
        ref = rs.score(ans)
        self.assertEqual(got["areas"], {k: v["score"] for k, v in ref["areas"].items()})
        self.assertEqual(got["overall"], ref["overall"])
        self.assertEqual(got["unc"], ref["uncertainty_pct"])
        self.assertEqual(core.BANDS[got["band"]]["name"], ref["overall_band"])

    def test_personas(self):
        for name, (_s, _z, _r, ans) in rs.PERSONAS.items():
            with self.subTest(name):
                self.check(ans)

    def test_workshop_example(self):
        got = core.score(letters(rs.PERSONAS["TEST-EX"][3]))
        self.assertEqual((got["areas"], got["overall"]), ({"SD": 80, "PC": 70, "DC": 58, "IR": 40}, 64))

    def test_random_vectors(self):
        rnd = random.Random(2026)
        for _ in range(3000):
            self.check({c: rnd.choice("YPNU") for c in core.CODES})

    def test_extremes(self):
        for L in "YPNU":
            self.check({c: L for c in core.CODES})


class Scoring(unittest.TestCase):
    def test_rhu(self):
        self.assertEqual([core.rhu(n, 2) for n in range(6)], [0, 1, 1, 2, 2, 3])
        self.assertEqual(core.rhu(0, 7), 0)

    def test_all_implemented(self):
        s = core.score(ALL_Y)
        self.assertEqual(s["overall"], 100)
        self.assertEqual(s["crit"], [])
        self.assertEqual(s["band"], len(core.BANDS) - 1)

    def test_unknown_is_uncertainty(self):
        s = core.score("U" * core.NQ)
        self.assertEqual((s["overall"], s["unc"]), (0, 100))
        self.assertEqual(len(s["crit"]), sum(1 for q in core.QUESTIONS if q["critical"]))


class ProgressValidation(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(core.parse_progress(body({"sid": SID, "s": 3})), (SID, 3))
        self.assertEqual(core.parse_progress(body({"s": 7, "sid": SID})), (SID, 7))

    def test_rejects(self):
        bad = [
            b"", b"x" * 1025, b"[]", b"null", b"\xff\xfe", b'{"sid":"%s","s":1' % SID.encode(),
            body({"sid": SID}), body({"s": 1}), body({"sid": SID, "s": 8}), body({"sid": SID, "s": -1}),
            body({"sid": SID, "s": True}), body({"sid": SID, "s": 1.0}), body({"sid": SID, "s": "1"}),
            body({"sid": SID.upper(), "s": 1}), body({"sid": SID[:-1], "s": 1}), body({"sid": SID + "0", "s": 1}),
            body({"sid": SID, "s": 1, "x": 1}), body({"sid": 1, "s": 1}),
            ('{"sid":"%s","s":1,"s":2}' % SID).encode(), ('{"sid":"%s","s":NaN}' % SID).encode(),
            ('{"sid":"%s","s":Infinity}' % SID).encode(), ('{"sid":"%s\\u0000","s":1}' % SID[:-1]).encode(),
            ("[" * 5000).encode()[:1024],
        ]
        for b in bad:
            with self.subTest(b[:60]):
                with self.assertRaises(core.Invalid):
                    core.parse_progress(b)


class SubmitValidation(unittest.TestCase):
    def test_ok(self):
        d = core.parse_submit(body(sub(sector=core.PROFILE["sector"][0], size=core.PROFILE["size"][1],
                                       role=core.PROFILE["role"][2])))
        self.assertEqual(d["ans"], ALL_Y)
        self.assertEqual(d["size"], core.PROFILE["size"][1])
        minimal = core.parse_submit(body({"sid": SID, "code": "AB", "ans": ALL_Y}))
        self.assertEqual((minimal["sector"], minimal["v"], minimal["dur"]), ("", "", 0))

    def test_codes(self):
        for good in ("K7M-Q4X", "AB", "TEST-EX", "A B_C-1", "Z" * 20):
            self.assertTrue(core.valid_code(good), good)
        for bad in ("", "A", "k7m", "-AB", " AB", "AB ", "A  B", "=1+1", "+AB", "@AB", "A,B", "A\tB", "Z" * 21,
                    "ÄB", "A\u00a0B", "A\nB", None, 12):
            self.assertFalse(core.valid_code(bad), repr(bad))

    def test_rejects(self):
        bad = [
            sub(code="=HYPERLINK(1)"), sub(code="k7m-q4x"), sub(ans=ALL_Y[:-1]), sub(ans=ALL_Y + "Y"),
            sub(ans="y" * core.NQ), sub(ans="X" * core.NQ), sub(sector="Banking"), sub(size=1), sub(role=None),
            sub(v="1.3.0;rm"), sub(v="x" * 17), sub(dur=-1), sub(dur=86401), sub(dur=True), sub(dur=1.5),
            sub(extra=1), {"sid": SID, "ans": ALL_Y}, {"code": "AB", "ans": ALL_Y},
        ]
        for d in bad:
            with self.subTest(d):
                with self.assertRaises(core.Invalid):
                    core.parse_submit(body(d))

    def test_size_limit(self):
        d = sub()
        d["v"] = "1"
        raw = body(d)
        self.assertLessEqual(len(raw), core.MAX_BODY)
        with self.assertRaises(core.Invalid):
            core.parse_submit(raw + b" " * (core.MAX_BODY - len(raw) + 1))

    def test_payload_hash_ignores_timing(self):
        a = core.parse_submit(body(sub(dur=10, v="1.3.0")))
        b = core.parse_submit(body(sub(dur=900, v="1.3.1")))
        c = core.parse_submit(body(sub(ans="N" + ALL_Y[1:])))
        self.assertEqual(core.payload_hash(a), core.payload_hash(b))
        self.assertNotEqual(core.payload_hash(a), core.payload_hash(c))

    def test_valid_row(self):
        row = dict(core.parse_submit(body(sub())))
        self.assertTrue(core.valid_row(row))
        self.assertFalse(core.valid_row(dict(row, code="=1")))
        self.assertFalse(core.valid_row(dict(row, sector="x")))
        self.assertFalse(core.valid_row(dict(row, ans="Y")))
        self.assertFalse(core.valid_row({}))


def row(i, ans=ALL_Y, code=None, sector=""):
    return {"sid": "%032x" % i, "code": code or "P%03d" % i, "ans": ans, "sector": sector, "size": "", "role": "",
            "rs": "sent"}


class Aggregate(unittest.TestCase):
    def test_hidden_below_minimum(self):
        r = core.aggregate([row(i) for i in range(core.MIN_ROOM - 1)], [], 1000)
        self.assertEqual(r["n"], core.MIN_ROOM - 1)
        self.assertNotIn("overall", r)
        json.dumps(r)

    def test_results(self):
        subs = [row(i) for i in range(4)] + [row(9, ans="N" * core.NQ)]
        r = core.aggregate(subs, [], 1000)
        self.assertEqual(r["n"], 5)
        self.assertEqual(r["overall"], {"mean": 80, "median": 100, "min": 0, "max": 100})
        self.assertEqual(r["bands"], [1, 0, 0, 4])
        self.assertEqual(sum(r["hist"]), 5)
        self.assertEqual((r["hist"][0], r["hist"][9]), (1, 4))
        self.assertEqual([a["mean"] for a in r["areas"]], [80, 80, 80, 80])
        self.assertEqual(r["mix"], {"Y": 80, "P": 0, "N": 20, "U": 0})
        self.assertEqual(len(r["topGaps"]), 6)
        self.assertTrue(all(g["pct"]["N"] == 20 for g in r["topGaps"]))
        self.assertTrue(r["topGaps"][0]["critical"])
        self.assertEqual(len(r["critical"]), 5)
        self.assertEqual(r["relay"], {"sent": 5})
        self.assertEqual(r["sectors"], [])
        json.dumps(r)

    def test_test_codes(self):
        tests = [row(i, code="TEST-%d" % i) for i in range(6)]
        r = core.aggregate(tests, [], 1000)
        self.assertTrue(r["testOnly"])
        self.assertEqual(r["n"], 6)
        r = core.aggregate(tests + [row(50)], [], 1000)
        self.assertFalse(r["testOnly"])
        self.assertEqual((r["n"], r["testHidden"]), (1, 6))

    def test_sectors_need_group_minimum(self):
        sec = core.PROFILE["sector"]
        subs = [row(i, sector=sec[0]) for i in range(5)] + [row(10 + i, sector=sec[1]) for i in range(4)]
        r = core.aggregate(subs, [], 1000)
        self.assertEqual([(s["name"], s["n"]) for s in r["sectors"]], [(sec[0], 5)])

    def test_progress(self):
        progs = [{"sid": "%032x" % i, "s": i % 8, "ts": 1000 - 60 * i} for i in range(20)]
        subs = [row(0), row(1)]
        p = core.aggregate(subs, progs, 1000)["progress"]
        self.assertEqual(p["devices"], 20)
        self.assertEqual(p["completed"], 2)
        self.assertEqual(p["inProgress"], 18)
        self.assertEqual(sum(p["stages"]), 18)
        self.assertEqual(p["stages"][0], 2)  # sid 8 and 16 (0 is finished)
        self.assertEqual(p["activeRecent"], sum(1 for i in range(2, 20) if 60 * i <= core.ACTIVE_S))


if __name__ == "__main__":
    unittest.main()
