import json, math, sys, threading, unittest, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from jev_luna.transport import Route
sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from jev_luna import core as d


OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # the shell exports HTTP_PROXY; loopback must bypass it


class Up(BaseHTTPRequestHandler):
    reply = None; seen = []
    def log_message(self, *a): pass
    def do_POST(self):
        Up.request_parts = {"path":self.path,"authorization":self.headers.get("Authorization")}
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Up.seen.append(body)
        status, obj, *extra = Up.reply(body)
        raw = (obj.encode() if isinstance(obj, str) else ("data: " + json.dumps({"type":"response.completed","response":obj}) + "\n\n").encode() if status == 200 else json.dumps(obj).encode())
        self.send_response(status)
        if not getattr(Up,'omit_content_type',False): self.send_header("Content-Type", "text/event-stream" if status == 200 else "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key,value in (extra[0] if extra else {}).items(): self.send_header(key,value)
        self.end_headers(); self.wfile.write(raw)


def tool_reply(payload, usage=None):
    payload = {k: list(v.values()) if isinstance(v, dict) and len(v) <= 16 and not (set(v) & {"p_true", "refused", "top", "rest"}) else v for k,v in payload.items()}
    payload = {k: {"refused": v == {"refused": True}, "answer": None if v == {"refused": True} else v} for k,v in payload.items()}
    return 200, {"model": "gpt-6-luna", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}], "usage": usage or {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}, "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens":120}}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(c):
        c.up = HTTPServer(("127.0.0.1", 0), Up); threading.Thread(target=c.up.serve_forever, daemon=True).start()
        c.fixture_route = patch.object(d, 'prepare_auth', return_value=Route(f"http://127.0.0.1:{c.up.server_port}/responses", {}))
        c.fixture_route.start()
        c.srv = d.ThreadingHTTPServer(("127.0.0.1", 0), d.H); threading.Thread(target=c.srv.serve_forever, daemon=True).start()
        c.url = f"http://127.0.0.1:{c.srv.server_port}"
    @classmethod
    def tearDownClass(c): c.up.shutdown(); c.srv.shutdown(); c.up.server_close(); c.srv.server_close();c.fixture_route.stop()
    def setUp(self): Up.seen = []; Up.omit_content_type=False; d.clear_cache()
    def post(self, path, body):
        if isinstance(body, dict): body = {"model": "gpt-6-luna", **body}
        r = urllib.request.Request(self.url + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with OPEN.open(r) as x: return x.status, json.load(x)
        except urllib.error.HTTPError as e: return e.code, json.load(e)


class TestSystemOne(Base):
    def test_three_types_and_confidence(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": 0.9},
                                         "q1": {"a": 0.6, "b": 0.3, "c": 0.1},
                                         "q2": {"0": 0.0, "1": 0.5, "2": 0.5}})
        s, j = self.post("/v1/systemone", {"model": "jev-1.13", "state": {"x": 1}, "questions": {
            "n": {"type": "noul", "instructions": "is it?"},
            "c": {"type": "choice", "instructions": "which", "criteria": {"a": "A", "b": "B", "c": "C"}},
            "s": {"type": "score", "instructions": "how much", "criteria": ["lo", "mid", "hi"]}}})
        self.assertEqual(s, 200); a = j["answers"]
        self.assertEqual(a["n"], {"type": "noul", "noul": 0.9})
        self.assertEqual(a["c"]["choice"], "a"); self.assertAlmostEqual(a["c"]["confidence"], 0.4, 4)   # doc example (0.6,0.3,0.1) -> 0.4
        self.assertAlmostEqual(a["s"]["score"], 1.5); self.assertAlmostEqual(a["s"]["confidence"], 0.25, 4)  # doc example (0,.5,.5) -> 0.25
        self.assertEqual(a["s"]["legend"], {"0": "lo", "1": "mid", "2": "hi"})
        self.assertEqual(j["usage"], {"input_tokens": 100, "output_tokens": 20})

    def test_one_upstream_call_state_once(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": 0.5}, "q1": {"p_true": 0.5}})
        self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "STATEMARK", "questions": {"a": {"type": "noul", "instructions": "x"}, "b": {"type": "noul", "instructions": "y"}}})
        self.assertEqual(len(Up.seen), 1); self.assertEqual(json.dumps(Up.seen[0]).count("STATEMARK"), 1)

    def test_unsafe_option_keys_are_aliased_and_restored(self):
        crit = {"high priority!": "x", "c/d e": "y"}
        Up.reply = lambda b: tool_reply({"q0": {"o0": 0.2, "o1": 0.8}})
        s, j = self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "s", "questions": {"c": {"type": "choice", "instructions": "i", "criteria": crit}}})
        self.assertEqual(j["answers"]["c"]["choice"], "c/d e"); self.assertEqual(set(j["answers"]["c"]["probabilities"]), set(crit))

    def test_large_choice_full_vector(self):
        crit = {f"c{i}": f"lines {i}" for i in range(200)}
        Up.reply = lambda b: tool_reply({"q0": {f"c{i}": (.7 if i == 7 else .2 if i == 8 else .1/198) for i in range(200)}})
        s, j = self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "s", "questions": {"w": {"type": "choice", "instructions": "i", "criteria": crit}}})
        self.assertEqual(s, 200); w = j["answers"]["w"]; self.assertEqual(w["choice"], "c7")
        self.assertAlmostEqual(sum(w["probabilities"].values()), 1.0, 2)
        self.assertAlmostEqual(w["confidence"], (200 * 0.7 - 1) / 199, 3)
        schema = Up.seen[0]["text"]["format"]["schema"]["properties"]["q0"]
        self.assertEqual(set(schema["properties"]["answer"]["required"]), set(crit))

    def test_zero_weights_retried_then_502(self):
        Up.reply = lambda b: tool_reply({"q0": {"a": 0, "b": 0}})
        s, j = self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "s", "questions": {"c": {"type": "choice", "instructions": "i", "criteria": {"a": "", "b": ""}}}})
        self.assertEqual(s, 502); self.assertEqual(j["error"]["code"], "invalid_output"); self.assertEqual(len(Up.seen), 3)

    def test_second_attempt_can_recover(self):
        calls = []
        def reply(b):
            calls.append(1); return tool_reply({"q0": {"p_true": 7}}) if len(calls) == 1 else tool_reply({"q0": {"p_true": 0.4}})
        Up.reply = reply
        s, j = self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "s", "questions": {"n": {"type": "noul", "instructions": "i"}}})
        self.assertEqual((s, j["answers"]["n"]["noul"]), (200, 0.4))

    def test_422s(self):
        for body in ({"state": "s"}, {"state": "s", "questions": {"a": {"type": "choice", "instructions": "i", "criteria": {}}}},
                     {"state": "s", "questions": {"a": {"type": "score", "instructions": "i", "criteria": []}}},
                     {"state": "s", "questions": {"a": {"type": "wat", "instructions": "i"}}}):
            s, j = self.post("/v1/systemone", body); self.assertEqual(s, 422, body)
        self.assertEqual(Up.seen, [])

    def test_upstream_down_is_502(self):
        with patch.object(d, 'prepare_auth', return_value=Route('http://127.0.0.1:1/responses', {})):
            s, j = self.post("/v1/systemone", {"model": "gpt-6-luna", "state": "s", "questions": {"n": {"type": "noul", "instructions": "i"}}})
        self.assertEqual((s, j["error"]["code"]), (502, "upstream_failure"))


class TestDecisions(Base):
    def test_openai_shapes(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": 0.95}, "q1": {"o0": 0.05, "o1": 0.95}, "q2": {"0": 0.1, "1": 0.7, "2": 0.2}})
        s, j = self.post("/v1/decisions", {"model": "gpt-6-luna", "input": "The package arrived with a broken screen.", "questions": [
            {"type": "predicate", "name": "damaged", "instructions": "Is it damaged?"},
            {"type": "choice", "name": "dept", "instructions": "who", "choices": [{"value": "other"}, {"value": "billing", "description": "money"}]},
            {"type": "score", "name": "sev", "instructions": "how bad", "levels": [{"label": "Cosmetic"}, {"label": "Workaround"}, {"label": "Blocked"}]}]})
        self.assertEqual(s, 200); a = j["answers"]
        self.assertEqual([x["type"] for x in a], ["predicate", "choice", "score"]); self.assertEqual([x["name"] for x in a], ["damaged", "dept", "sev"])
        self.assertEqual(a[0]["probability"], 0.95); self.assertEqual(a[1]["choice"], "billing")
        self.assertEqual(a[1]["probabilities"], [{"value": "other", "probability": 0.05}, {"value": "billing", "probability": 0.95}])
        self.assertAlmostEqual(a[2]["score"], 1.1); self.assertEqual(a[2]["probabilities"][1], {"value": 1, "label": "Workaround", "probability": 0.7})
        self.assertEqual(set(j), {"model", "answers", "usage"}); self.assertEqual(j["usage"]["total_tokens"], 120)
        self.assertEqual(j["usage"]["output_tokens_details"], {"reasoning_tokens":0})

    def test_bool_choice_values_and_uniqueness(self):
        Up.reply = lambda b: tool_reply({"q0": {"o0": 0.9, "o1": 0.1}})
        s, j = self.post("/v1/decisions", {"model": "gpt-6-luna", "input": "x", "questions": [{"type": "choice", "instructions": "i", "choices": [{"value": True}, {"value": "true"}]}]})
        self.assertEqual(j["answers"][0]["choice"], True)
        s, j = self.post("/v1/decisions", {"model": "gpt-6-luna", "input": "x", "questions": [{"type": "choice", "instructions": "i", "choices": [{"value": "a"}, {"value": "a"}]}]})
        self.assertEqual(s, 400)

    def test_images_and_limits(self):
        s, _ = self.post("/v1/decisions", {"model": "gpt-6-luna", "input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA"}]}], "questions": [{"type": "predicate", "instructions": "i"}]})
        self.assertEqual(s, 400)
        s, _ = self.post("/v1/decisions", {"model": "gpt-6-luna", "input": "x", "questions": []}); self.assertEqual(s, 400)


class TestMath(unittest.TestCase):
    def test_confidence_formulas(self):
        self.assertAlmostEqual(d.conf_choice([0.6, 0.2, 0.2]), 0.4); self.assertEqual(d.conf_choice([1.0]), 1.0)
        self.assertAlmostEqual(d.conf_score([0.5, 0, 0.5]), 0.0); self.assertAlmostEqual(d.conf_score([0, 0.57, 0.43]), 0.355, 3)




if __name__ == "__main__": unittest.main()
