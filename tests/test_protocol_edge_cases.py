"""Protocol edge cases: provider errors, usage totals, weight normalization and strict HTTP input handling."""
import json
import math
import unittest
import urllib.error
import urllib.request

from jev_luna import core as d
from jev_luna.validation import loads
from test_http_service import Base, OPEN, Up, tool_reply


class DeterministicDefects(unittest.TestCase):
    def test_terminal_stream_errors_are_not_retryable_native_5xx(self):
        from jev_luna.transport import provider_error
        for code,status in [('model_not_found',404),('context_length_exceeded',413),
                            ('usage_limit_reached',429),('unsupported_capability',422),('server_error',503)]:
            with self.subTest(code=code):
                error=provider_error(200,{'error':{'code':code,'message':'private provider detail'}},{'Retry-After':'2'})
                self.assertEqual(error.status,status);self.assertFalse(error.retryable)
                self.assertEqual(error.upstream_status,200)
                self.assertEqual(error.headers['Retry-After'],'2')
                self.assertNotIn('private provider detail',error.message)

    def test_complete_total_when_one_batch_omits_total(self):
        total = {}
        d.merge_usage(total, {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
        d.merge_usage(total, {"input_tokens": 20, "output_tokens": 3})
        self.assertEqual(total, {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35})

    def test_invalid_usage_is_rejected_atomically(self):
        invalid = [
            {"input_tokens": True, "output_tokens": 2},
            {"input_tokens": "10", "output_tokens": 2},
            {"input_tokens": 10, "output_tokens": 2, "input_tokens_details": {"cached_tokens": -1}},
            {"output_tokens_details": {"reasoning_tokens": True}},
            {"input_tokens_details": []},
        ]
        for usage in invalid:
            total = {"input_tokens": 7}
            with self.assertRaises(d.Reject): d.merge_usage(total, usage)
            self.assertEqual(total, {"input_tokens": 7})

    def test_extreme_valid_weights_normalize_without_overflow(self):
        q = {"kind": "choice", "opts": [{"tid": "a"}, {"tid": "b"}]}
        for weights in ([1e308, 1e308], [5e-324, 5e-324], [1, 1]):
            self.assertEqual(d.vector(q, weights), [.5, .5])
        probabilities = d.vector(q, [1e308, 1e-308])
        self.assertEqual(probabilities, [1, 0])
        self.assertEqual(d.typed(q, probabilities)["top"], 0)
        for weights in ([10**1000, 1], [True, 1], [math.inf, 1], [0, 0]):
            with self.assertRaises(ValueError): d.vector(q, weights)

    def test_empty_structured_instructions_are_preserved(self):
        for instructions in ({}, []):
            _, questions = d.parse_systemone({"model": "jev-latest", "state": "x", "questions": {
                "n": {"type": "noul", "instructions": instructions}}})
            self.assertEqual(questions[0]["text"], json.dumps(instructions))

    def test_semantic_labels_are_not_lost_when_they_are_safe_output_ids(self):
        state, questions = d.parse_systemone({'model':'jev-latest','state':'x','questions':{
            'q':{'type':'choice','instructions':'choose','criteria':{'technical':None,'unknown':None}}}})
        payload = d.build_upstream(state,questions)
        spec = json.loads(payload['input'][-1]['content'][0]['text'].removeprefix('QUESTIONS:\n'))
        self.assertEqual(spec[0]['outcomes'],[{'id':'technical','key':'technical'},{'id':'unknown','key':'unknown'}])

    def test_evidence_integers_remain_lossless(self):
        number = 10**100+1
        self.assertEqual(loads(json.dumps({"number": number}))["number"], number)

    def test_missing_required_usage_cannot_be_fabricated(self):
        with self.assertRaises(d.Reject) as raised:
            d.decisions_usage({"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(raised.exception.code, "usage_capability")
        observed = {"input_tokens": 10, "output_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 1}}
        self.assertEqual(d.decisions_usage(observed), {**observed, "total_tokens": 12})


class InvalidHTTP(Base):
    def raw_post(self, body):
        request = urllib.request.Request(self.url+"/v1/decisions", body, {"Content-Type": "application/json"})
        try:
            with OPEN.open(request) as response: return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_malformed_discriminators_return_json_errors(self):
        for kind in ([], {}, 3, False, None):
            status, response = self.post("/v1/decisions", {"input": "x", "questions": [{"type": kind, "instructions": "x"}]})
            self.assertEqual((status, response["error"]["code"]), (400, "invalid_request"))
        self.assertEqual(Up.seen, [])

    def test_strict_json_rejects_duplicate_nonfinite_surrogate_and_deep_inputs(self):
        bodies = [
            b'{"model":"gpt-6-luna","model":"gpt-6-sol"}',
            b'{"model":"gpt-6-luna","input":1e999}',
            b'{"model":"gpt-6-luna","input":"\\ud800"}',
            (b'{"model":"gpt-6-luna","input":'+b'['*140+b'0'+b']'*140+b'}'),
        ]
        for body in bodies:
            status, response = self.raw_post(body)
            self.assertEqual((status, response["error"]["code"]), (400, "invalid_request"))
        self.assertEqual(Up.seen, [])

    def test_jev_refusal_is_nonretryable_application_error(self):
        Up.reply = lambda body: tool_reply({"q0": {"refused": True}})
        status, response = self.post("/v1/systemone", {"state": "x", "questions": {"n": {"type": "noul", "instructions": "x"}}})
        self.assertEqual((status, response["error"]["code"]), (403, "refusal"))
        self.assertEqual(len(Up.seen), 1)


if __name__ == "__main__":
    unittest.main()
