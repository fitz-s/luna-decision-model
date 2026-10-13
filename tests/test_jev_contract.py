"""SDK-visible boundary cases recovered from the upstream Jev tests."""
import json
import urllib.request
from unittest.mock import patch
from jev_luna import core as d
from test_http_service import Base, Up, tool_reply


class JevContract(Base):
    def test_conditional_singleton_cannot_invalidate_an_unused_head(self):
        from jsonschema import validate, ValidationError
        state, qs = d.parse_systemone({'model': 'jev-preview', 'state': {'already_filled': True}, 'questions': {
            'operation': {'type': 'choice', 'criteria': {'click': None, 'type': None}},
            'type_target': {'type': 'choice', 'instructions': 'Choose an empty input, if any.', 'criteria': {'search': None}},
        }})
        compiled = d.build_upstream(state, qs)
        schema = compiled['text']['format']['schema']
        valid = {'q0': {'refused': False, 'answer': [.9, .1]}, 'q1': {'refused': False, 'answer': [1]}}
        validate(valid, schema)
        valid['q1']['answer'] = [0]
        with self.assertRaises(ValidationError): validate(valid, schema)
        Up.reply = lambda _: tool_reply({'q0': [.9, .1], 'q1': [1]})
        status, result = self.post('/v1/systemone', {'model': 'jev-preview', 'state': state, 'questions': {
            'operation': {'type': 'choice', 'criteria': {'click': None, 'type': None}},
            'type_target': {'type': 'choice', 'criteria': {'search': None}},
        }})
        self.assertEqual(status, 200)
        self.assertEqual(result['answers']['operation']['choice'], 'click')
        self.assertEqual(result['answers']['type_target']['probabilities'], {'search': 1})

    def test_removed_proxy_route_is_rejected_before_native_auth(self):
        import time
        from jev_luna.transport import prepare
        from jev_luna.lifecycle import RequestContext
        with patch('jev_luna.subscription_auth.request_parameters') as native:
            for settings in ({'transport':'proxy'}, {'transport':'subscription','responses_url':'http://127.0.0.1:2/responses'}):
                with self.assertRaises(d.Reject) as failure:
                    prepare(settings, RequestContext(time.monotonic()+1))
                self.assertEqual(failure.exception.code, 'transport_config')
            native.assert_not_called()
    def test_optional_instructions_and_single_outcome(self):
        Up.reply = lambda _: tool_reply({"q0": [1], "q1": [1], "q2": {"p_true": .9}})
        status, result = self.post("/v1/systemone", {"state": {}, "questions": {
            "single": {"type": "choice", "criteria": {"only": None}},
            "score": {"type": "score", "criteria": ["only"]},
            "yes": {"type": "noul", "criteria": {"true": "The statement is true"}},
        }})
        self.assertEqual(status, 200)
        self.assertEqual(result["answers"]["single"]["probabilities"], {"only": 1})
        self.assertEqual(result["answers"]["score"]["score"], 0)
        self.assertEqual(result["answers"]["score"]["confidence"], 1)

    def test_models_and_request_id_for_unmodified_sdk(self):
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ids = []
        for _ in range(2):
            with client.open(self.url+"/v1/models?unused=1") as response:
                ids.append(response.headers["x-request-id"])
                self.assertEqual(response.headers["x-typesafe-request-id"], ids[-1])
                result = json.load(response)
                self.assertEqual(result["models"][0]["name"], "gpt-6-luna")
                self.assertIsInstance(result["models"][0]["release_date"], str)
        self.assertTrue(all(ids))
        self.assertNotEqual(*ids)
