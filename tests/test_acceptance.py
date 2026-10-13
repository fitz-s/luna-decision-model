import concurrent.futures, json, threading, time, unittest, urllib.request
from jev_luna import core as d
from test_http_service import Base, Up, OPEN, tool_reply

BODY = {"model":"gpt-6-luna", "state": "A refund was requested.", "questions": {"billing": {"type": "noul", "instructions": "Is this about billing?"}}}


class Acceptance(Base):
    def test_identical_requests_keep_independent_usage(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .9}})
        _, first = self.post("/v1/systemone", BODY)
        _, second = self.post("/v1/systemone", BODY)
        self.assertEqual(first["answers"], second["answers"])
        self.assertEqual(second["usage"], {"input_tokens": 100, "output_tokens": 20})
        self.assertEqual(len(Up.seen), 2)
        self.post("/v1/systemone", {**BODY, "state": "A parcel is missing."})
        self.assertEqual(len(Up.seen), 3)
        self.post("/v1/systemone", {**BODY, "questions": {"billing": {"type": "noul", "instructions": "Is a refund approved?"}}})
        self.assertEqual(len(Up.seen), 4)

    def test_identical_concurrent_requests_do_not_share_cancellation_or_usage(self):
        started, release = threading.Event(), threading.Event()
        def reply(b):
            started.set(); release.wait(2)
            return tool_reply({"q0": {"p_true": .9}})
        Up.reply = reply
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.post, "/v1/systemone", BODY)
            self.assertTrue(started.wait(1))
            b = pool.submit(self.post, "/v1/systemone", BODY)
            time.sleep(.05); release.set()
            responses = [a.result(), b.result()]
        self.assertEqual([x[0] for x in responses], [200, 200])
        self.assertEqual(len(Up.seen), 2)
        self.assertEqual(sorted(x[1]["usage"]["input_tokens"] for x in responses), [100, 100])

    def test_result_cache_configuration_is_rejected(self):
        import os, subprocess, sys
        result=subprocess.run([sys.executable,'-c','import jev_luna.core'],env={**os.environ,'DECIDE_D_CACHE_TTL':'1'},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('DECIDE_D_CACHE_TTL must be 0',result.stderr)

    def test_errors_not_retried_or_cached(self):
        for status in (401, 429, 503):
            Up.seen = []
            Up.reply = lambda b: (status, {"error": {"message": "private diagnostic must not be exposed"}})
            s, j = self.post("/v1/systemone", BODY)
            self.assertEqual(s, status if status in (401, 429) else 529)
            self.assertNotIn("private diagnostic", json.dumps(j))
            self.assertEqual(len(Up.seen), 1)
            self.post("/v1/systemone", BODY)
            self.assertEqual(len(Up.seen), 2)

    def test_wrong_model_cannot_pass(self):
        def reply(b):
            status, obj = tool_reply({"q0": {"p_true": .9}})
            obj["model"] = "gpt-6-sol"
            return status, obj
        Up.reply = reply
        s, j = self.post("/v1/systemone", BODY)
        self.assertEqual((s, j["error"]["code"]), (502, "model_mismatch"))
        self.assertEqual(len(Up.seen), 1)

    def test_malformed_decisions_get_json_errors(self):
        q = {"type": "choice", "instructions": "who", "choices": [{"value": "a"}, {"value": "b"}]}
        cases = [
            {"input": [{"role": "user", "content": 123}], "questions": [q]},
            {"input": [{"role": "user", "content": [{"type": "input_text", "text": 123}]}], "questions": [q]},
            {"input": "s", "questions": [{**q, "choices": [None, "x"]}]},
            {"input": "s", "questions": [{**q, "choices": [{"value": []}, {"value": "x"}]}]},
            {"input": "s", "questions": [{"type": "score", "instructions": "i", "levels": [None, {}]}]},
        ]
        for body in cases:
            status, result = self.post("/v1/decisions", body)
            self.assertEqual((status, result["error"]["code"]), (400, "invalid_request"), body)
        self.assertEqual(Up.seen, [])

    def test_sparse_and_extra_answers_rejected(self):
        crit = {f"c{i}": "chunk" for i in range(20)}
        body = {"state": "s", "questions": {"where": {"type": "choice", "instructions": "where", "criteria": crit}}}
        for raw in ({"top": [], "rest": 1}, {"top": [{"id": "c1", "p": .5}] * 2, "rest": 0}, {"top": [{"id": "c1", "p": 1}], "rest": 0, "extra": 1}):
            Up.reply = lambda b: tool_reply({"q0": raw})
            self.assertEqual(self.post("/v1/systemone", body)[0], 502)
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .9}, "q1": {"p_true": .1}})
        self.assertEqual(self.post("/v1/systemone", BODY)[0], 502)

    def test_health_does_not_claim_native_or_ready(self):
        with OPEN.open(self.url + "/health") as r: health = json.load(r)
        self.assertFalse(health["native_decisions"])
        self.assertEqual(health["backend"], "luna-json-readout")
        self.assertNotIn("ready", health)

    def test_response_headers_and_usage_are_observable(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .9}})
        req = urllib.request.Request(self.url + "/v1/systemone", json.dumps(BODY).encode(), {"Content-Type": "application/json"})
        for cache in ("disabled", "disabled"):
            with OPEN.open(req) as r:
                self.assertEqual(r.headers["X-Decide-D-Cache"], cache)
                self.assertEqual(r.headers["X-Decide-D-Backend"], "luna-json-readout")
                r.read()


if __name__ == "__main__": unittest.main()
