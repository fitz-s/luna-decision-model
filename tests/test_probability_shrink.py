"""LDM_PROBABILITY_SHRINK: optional uniform shrink p' = (1-a)p + a/K, applied once in core.typed."""
import json, math, os, random, subprocess, sys, unittest, urllib.request
from unittest.mock import patch
from jev_luna import core
from test_http_service import Base, OPEN, Up, tool_reply

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEMONE = {"model": "gpt-6-luna", "state": "s", "questions": {
    "n": {"type": "noul", "instructions": "is it?"},
    "c": {"type": "choice", "instructions": "which", "criteria": {"a": "A", "b": "B", "c": "C"}},
    "s": {"type": "score", "instructions": "how much", "criteria": ["lo", "mid", "hi"]}}}
DECISIONS = {"model": "gpt-6-luna", "input": "x", "questions": [
    {"type": "predicate", "name": "n", "instructions": "is it?"},
    {"type": "choice", "name": "c", "instructions": "which", "choices": [{"value": "a"}, {"value": "b"}, {"value": "c"}]},
    {"type": "score", "name": "s", "instructions": "how much", "levels": [{"label": "lo"}, {"label": "mid"}, {"label": "hi"}]}]}
READOUT = {"q0": {"p_true": 1.0}, "q1": {"a": 0.0, "b": 1.0, "c": 0.0}, "q2": {"0": 0.0, "1": 0.5, "2": 0.5}}


def question(kind, k):
    keys = [{"key": str(i), "tid": f"o{i}"} for i in range(k)]
    return {"id": "q0", "kind": kind, "opts": keys}


class Shrink(unittest.TestCase):
    def typed(self, kind, v, a):
        with patch.object(core, "PROBABILITY_SHRINK", a): return core.typed(question(kind, len(v)), list(v))

    def test_default_is_exact_noop(self):
        self.assertEqual(core.PROBABILITY_SHRINK, 0)
        v = [0.1, 0.7, 0.2]
        self.assertIs(core.typed(question("choice", 3), v)["v"], v)
        self.assertEqual(core.typed(question("noul", 2), [0.3, 0.7]), {"p": 0.3})
        self.assertEqual(self.typed("choice", v, 0.0), core.typed(question("choice", 3), v))

    def test_known_vectors(self):
        for k, top in ((2, 0.95), (5, 0.92), (255, 0.9 + 0.1 / 255)):
            v = [1.0] + [0.0] * (k - 1)
            out = self.typed("choice", v, 0.1)["v"]
            self.assertAlmostEqual(out[0], top, places=12)
            for p in out[1:]: self.assertAlmostEqual(p, 0.1 / k, places=12)
            self.assertAlmostEqual(sum(out), 1.0, places=12)

    def test_predicate_shrinks_p_true_toward_half(self):
        self.assertAlmostEqual(self.typed("noul", [1.0, 0.0], 0.1)["p"], 0.95, places=12)
        self.assertAlmostEqual(self.typed("noul", [0.0, 1.0], 0.1)["p"], 0.05, places=12)
        self.assertAlmostEqual(self.typed("noul", [0.5, 0.5], 0.1)["p"], 0.5, places=12)
        self.assertAlmostEqual(self.typed("noul", [0.8, 0.2], 0.5)["p"], 0.65, places=12)

    def test_argmax_unchanged_including_ties_and_fuzz(self):
        cases = [[0.5, 0.5], [0.3, 0.4, 0.4], [0.2, 0.2, 0.2, 0.2, 0.2], [0.5, math.nextafter(0.5, 1)], [math.nextafter(0.5, 1), 0.5]]
        rng = random.Random(0)
        for _ in range(300):
            w = [rng.choice([0, 0, 1, rng.random()]) for _ in range(rng.randint(2, 9))]
            if sum(w): cases.append([x / sum(w) for x in w])
        for v in cases:
            for a in (0.01, 0.1, 0.3, 0.5):
                with self.subTest(v=v, a=a):
                    self.assertEqual(self.typed("choice", v, a)["top"], core.typed(question("choice", len(v)), v)["top"])

    def test_sums_to_one(self):
        rng = random.Random(1)
        for k in (2, 3, 7, 40):
            w = [rng.random() for _ in range(k)]; v = [x / sum(w) for x in w]
            for a in (0.05, 0.2, 0.5): self.assertAlmostEqual(sum(self.typed("choice", v, a)["v"]), 1.0, places=12)

    def test_score_and_confidence_derive_from_shrunk_distribution(self):
        v = [0.0, 0.5, 0.5]; a = 0.1
        out = self.typed("score", v, a)
        self.assertAlmostEqual(out["score"], sum(i * p for i, p in enumerate(out["v"])), places=12)
        self.assertAlmostEqual(out["score"], (1 - a) * 1.5 + a * 1.0, places=12)  # (1-a)·raw score + a·(K-1)/2
        self.assertEqual(out["conf"], core.conf_score(out["v"]))
        self.assertEqual(self.typed("choice", v, a)["conf"], core.conf_choice(self.typed("choice", v, a)["v"]))

    def test_invalid_env_values_stop_startup(self):
        for value, ok in (("0", True), ("0.1", True), ("0.5", True), ("0.5001", False), ("-0.1", False), ("nan", False),
                          ("inf", False), ("", False), ("abc", False), ("1", False)):
            with self.subTest(value=value):
                run = subprocess.run([sys.executable, "-c", "import jev_luna.core as c; print(c.PROBABILITY_SHRINK)"],
                                     env={**os.environ, "LDM_PROBABILITY_SHRINK": value}, capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(run.returncode == 0, ok, run.stderr)
                if ok: self.assertEqual(float(run.stdout), float(value))
                else: self.assertIn("LDM_PROBABILITY_SHRINK must be a number from 0 to 0.5", run.stderr)


class ShrinkHttp(Base):
    def health(self):
        with OPEN.open(self.url + "/health") as r: return json.load(r)

    def test_off_by_default_and_health_shows_it(self):
        self.assertEqual(self.health()["probability_shrink"], 0)
        Up.reply = lambda b: tool_reply(READOUT)
        s, j = self.post("/v1/systemone", SYSTEMONE); self.assertEqual(s, 200)
        a = j["answers"]
        self.assertEqual(a["n"], {"type": "noul", "noul": 1.0})
        self.assertEqual(a["c"]["probabilities"], {"a": 0.0, "b": 1.0, "c": 0.0})
        self.assertEqual(a["s"]["score"], 1.5)

    def test_systemone_and_decisions_agree_when_enabled(self):
        Up.reply = lambda b: tool_reply({**READOUT, "q0": {"p_true": 1.0}})
        with patch.object(core, "PROBABILITY_SHRINK", 0.1):
            self.assertEqual(self.health()["probability_shrink"], 0.1)
            s1, j1 = self.post("/v1/systemone", SYSTEMONE); s2, j2 = self.post("/v1/decisions", DECISIONS)
        self.assertEqual((s1, s2), (200, 200))
        a, b = j1["answers"], j2["answers"]
        self.assertAlmostEqual(a["n"]["noul"], 0.95); self.assertAlmostEqual(b[0]["probability"], 0.95)
        self.assertEqual(a["c"]["choice"], "b"); self.assertEqual(b[1]["choice"], "b")
        want = {"a": 0.1 / 3, "b": 0.9 + 0.1 / 3, "c": 0.1 / 3}
        for key, p in want.items(): self.assertAlmostEqual(a["c"]["probabilities"][key], p)
        for item, key in zip(b[1]["probabilities"], "abc"): self.assertAlmostEqual(item["probability"], want[key])
        self.assertAlmostEqual(a["c"]["confidence"], b[1]["confidence"])
        self.assertAlmostEqual(a["s"]["score"], 1.45); self.assertAlmostEqual(b[2]["score"], 1.45)
        self.assertAlmostEqual(a["s"]["score"], sum(i * p for i, p in enumerate(a["s"]["probabilities"].values())))

    def test_refusal_is_not_shrunk(self):
        Up.reply = lambda b: tool_reply({"q0": {"refused": True}, "q1": {"a": 0.0, "b": 1.0, "c": 0.0}})
        body = {"model": "gpt-6-luna", "input": "x", "questions": DECISIONS["questions"][:2]}
        with patch.object(core, "PROBABILITY_SHRINK", 0.1): s, j = self.post("/v1/decisions", body)
        self.assertEqual(s, 200); self.assertEqual(j["answers"][0], {"type": "refusal", "name": "n"})
        self.assertEqual(j["answers"][1]["choice"], "b")

    def test_trace_event_records_the_value(self):
        events = []
        class Ctx(core.RequestContext):
            def event(self, name, **fields): events.append((name, fields)); return super().event(name, **fields)
        qs = core.parse_systemone(SYSTEMONE)[1]
        Up.reply = lambda b: tool_reply(READOUT)
        with patch.object(core, "PROBABILITY_SHRINK", 0.1):
            ctx = Ctx(core.time.monotonic() + 10); ctx.observer = lambda *a, **k: None
            core.decide("s", qs, context=ctx)
        batch = next(f for n, f in events if n == "compiled_batch")
        self.assertEqual(batch["probability_shrink"], 0.1)


if __name__ == "__main__": unittest.main()
