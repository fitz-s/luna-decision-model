"""Contract checks: changing the inference backend must not change the caller's decision policy."""
import concurrent.futures, json, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from jev_luna.transport import Route
from jev_luna import core as d
from test_http_service import Base, Up, tool_reply

BODY = {"model": "gpt-6-luna", "state": "A duplicate charge needs refunding.",
        "questions": {"payment": {"type": "noul", "instructions": "Is this about a payment?"}}}


class Parity(Base):
    def test_large_predicate_batch_runs_bounded_parallel_reads_and_preserves_order_usage(self):
        state,qs=d.parse_systemone({**BODY,'questions':{f'p{i}':{'type':'noul','instructions':str(i)} for i in range(48)}})
        barrier=threading.Barrier(2);seen=[];lock=threading.Lock()
        def call(payload,timeout):
            spec=json.loads(payload['input'][-1]['content'][0]['text'].split('\n',1)[1])
            with lock:seen.append(payload)
            barrier.wait(timeout=3)
            return {q['question_id']:{'p_true':int(q['question'])/100} for q in spec},{'input_tokens':100,'output_tokens':24}
        with patch.object(d,'upstream_call',side_effect=call):answers,usage=d._infer(state,qs,time.monotonic()+5)
        self.assertEqual(answers,[{'p':i/100} for i in range(48)])
        self.assertEqual(usage,{'input_tokens':200,'output_tokens':48,'total_tokens':248})
        self.assertEqual(len(seen),2)
        self.assertTrue(all(len(p['text']['format']['schema']['required'])==24 for p in seen))
        self.assertTrue(all(state in p['input'][0]['content'][0]['text'] for p in seen))

    def test_scalar_metadata_precedes_containers_without_changing_evidence(self):
        state={'file':{'chunks':{'c10':{'text':'ten','start_line':101},'c2':{'text':'two','start_line':21}},'path':'source.py'},'goal':'Locate the implementation','items':[{'data':[3,2,1],'tag':'a'},'b']}
        ordered=d._metadata_first(state)
        self.assertEqual(ordered,state)
        self.assertEqual(list(ordered)[0],'goal')
        self.assertEqual(list(ordered['file'])[0],'path')
        self.assertEqual(list(ordered['file']['chunks']),['c10','c2'])
        self.assertEqual(ordered['items'][0]['data'],[3,2,1])

    def test_long_evidence_uses_the_same_luna_with_native_reasoning(self):
        state,qs=d.parse_systemone(BODY)
        qs.append({'id':'pick','kind':'choice','text':'Select the supported outcome','opts':[{'key':key,'tid':key,'desc':key} for key in ('a','b')]})
        short=d.build_upstream(state,qs);long=d.build_upstream('Evidence. '*2000,qs)
        self.assertEqual(short['reasoning']['effort'],'none')
        self.assertEqual(long['reasoning']['effort'],'medium')
        self.assertEqual(short['model'],long['model'])
        self.assertEqual(short['text']['format'],long['text']['format'])

    def test_predicate_batches_keep_fast_reasoning_at_large_input_sizes(self):
        state,qs=d.parse_systemone(BODY)
        self.assertEqual(d.build_upstream('Evidence. '*2000,qs)['reasoning']['effort'],'none')

    def test_default_has_no_result_reuse_or_coalescing(self):
        self.assertEqual(d.CACHE_TTL, 0)
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .9}})
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: self.post("/v1/systemone", BODY), range(2)))
        self.post("/v1/systemone", BODY)
        self.assertEqual(len(Up.seen), 3)
        self.assertTrue(all(j["usage"]["input_tokens"] == 100 for _, j in responses))

    def test_255_outcomes_retain_full_precision_and_formula(self):
        criteria = {f"c{i}": None for i in range(255)}
        Up.reply = lambda b: tool_reply({"q0": {k: 1/255 for k in criteria}})
        status, result = self.post("/v1/systemone", {**BODY, "questions": {"pick": {"type": "choice", "instructions": "pick", "criteria": criteria}}})
        self.assertEqual(status, 200)
        answer = result["answers"]["pick"]
        self.assertEqual(list(answer["probabilities"]), list(criteria))
        self.assertAlmostEqual(sum(answer["probabilities"].values()), 1, 12)
        self.assertAlmostEqual(answer["confidence"], d.conf_choice(list(answer["probabilities"].values())), 12)
        self.assertEqual(len(Up.seen[0]["text"]["format"]["schema"]["properties"]["q0"]["properties"]["answer"]["required"]), 255)

    def test_relative_weights_preserve_scale_and_full_distribution(self):
        q={"kind":"choice","opts":[{"tid":x} for x in ("a","b","c")]}
        probabilities=d.vector(q,[60,30,10])
        self.assertEqual(probabilities,[.6,.3,.1])
        self.assertEqual(probabilities,d.vector(q,[600,300,100]))
        self.assertAlmostEqual(d.conf_choice(probabilities),.4)

    def test_large_multiquestion_request_batches_without_dropping_outcomes(self):
        def reply(body):
            props = body["text"]["format"]["schema"]["properties"]
            return tool_reply({qid: {k: 1 for k in q["properties"]["answer"]["required"]} for qid,q in props.items()})
        Up.reply = reply
        q = {"type": "choice", "instructions": "pick", "criteria": {f"c{i}": None for i in range(255)}}
        status, result = self.post("/v1/systemone", {**BODY, "questions": {f"question{i}": q for i in range(200)}})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["answers"]), 200)
        self.assertTrue(all(len(a["probabilities"]) == 255 for a in result["answers"].values()))
        self.assertEqual(len(Up.seen), 29)
        self.assertEqual(result["usage"], {"input_tokens": 2900, "output_tokens": 580})
        self.assertTrue(all('max_output_tokens' not in b for b in Up.seen))

    def test_required_model_and_typed_jev_state_and_rubrics(self):
        self.assertEqual(self.post("/v1/systemone", {**BODY, "model": "gpt-6-sol"})[0], 400)
        for body in ({**BODY, "model": None}, {**BODY, "state": 42},
                     {**BODY, "questions": {"x": {"type": "noul", "instructions": 42}}},
                     {**BODY, "questions": {"x": {"type": "choice", "instructions": "pick", "criteria": {"a": 42, "b": None}}}}):
            self.assertEqual(self.post("/v1/systemone", body)[0], 422)
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .4}})
        for state in ("", {}, []):
            self.assertEqual(self.post("/v1/systemone", {**BODY, "model": "jev-latest", "state": state})[0], 200)
        with self.assertRaises(d.Reject): d.parse_systemone({"state": "s", "questions": BODY["questions"]})

    def test_structured_instructions_and_question_id_transparency(self):
        q = {"type": "choice", "instructions": {"question": "Which team?", "context": ["refund"]},
             "criteria": {"billing": {"handles": ["refund"]}, "other": None}}
        state, qs = d.parse_systemone({**BODY, "state": ["refund"], "questions": {"user supplied id": q}})
        state2, qs2 = d.parse_systemone({**BODY, "state": ["refund"], "questions": {"renamed": q}})
        self.assertEqual(d.build_upstream(state, qs), d.build_upstream(state2, qs2))
        self.assertEqual(qs[0]["opts"][0]["desc"], {"handles": ["refund"]})

    def test_image_order_message_boundaries_detail_and_identifier(self):
        body = {"model": "gpt-6-luna", "input": [{"role": "user", "content": "First message"},
            {"role": "user", "content": [{"type": "input_text", "text": "Second message"},
                {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "original"}]}],
            "safety_identifier": "opaque-test-user", "questions": [{"type": "predicate", "instructions": "Is it red?"}]}
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .8}})
        self.assertEqual(self.post("/v1/decisions", body)[0], 200)
        sent = Up.seen[0]
        self.assertEqual(sent["input"][:2], [{"role": "user", "content": [{"type": "input_text", "text": "First message"}]}, body["input"][1]])
        self.assertEqual(sent["safety_identifier"], "opaque-test-user")
        self.assertEqual(sent["model"], d.MODEL)
        self.assertTrue(sent["stream"])

    def test_images_limits_roles_and_numeric_choices_rejected(self):
        q = [{"type": "predicate", "instructions": "Is it red?"}]
        for body in ({"input": [{"role": "system", "content": "x"}], "questions": q},
                     {"input": [{"role": "user", "content": [{"type": "input_image", "file_id": "file"}]}], "questions": q},
                     {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}]*129}], "questions": q},
                     {"input": "x", "safety_identifier": "x"*129, "questions": q},
                     {"input": "x", "questions": [{"type": "choice", "instructions": "pick", "choices": [{"value": 1}, {"value": 2}]}]}):
            self.assertEqual(self.post("/v1/decisions", body)[0], 400)
        self.assertEqual(Up.seen, [])

    def test_hosted_images_are_forwarded_in_order_without_a_local_fetch(self):
        for url in ("http://example.com/image.png", "https://example.com/image.png",
                    "http://127.0.0.1:20141/private", "https://example.com/image.png?token=opaque"):
            body = {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": url}]}],
                    "questions": [{"type": "predicate", "instructions": "Is it red?"}]}
            Up.reply = lambda _: tool_reply({'q0': {'p_true': .9}})
            status, result = self.post("/v1/decisions", body)
            self.assertEqual(status, 200)
            self.assertEqual(Up.seen[-1]['input'][0]['content'][0]['image_url'], url)

    def test_partial_refusal_retains_other_question(self):
        Up.reply = lambda b: tool_reply({"q0": {"refused": True}, "q1": {"p_true": .7}})
        status, result = self.post("/v1/decisions", {"input": "s", "questions": [
            {"type": "predicate", "name": "refused", "instructions": "q"}, {"type": "predicate", "instructions": "q2"}]})
        self.assertEqual(status, 200)
        self.assertEqual(result["answers"], [{"type": "refusal", "name": "refused"}, {"type": "predicate", "name": None, "probability": .7}])
        Up.reply = lambda b: tool_reply({"q0": {"refused": True}})
        self.assertEqual(self.post("/v1/systemone", BODY)[1]["error"]["code"], "refusal")

    def test_observed_usage_details_and_no_cached_input_double_count(self):
        observed = {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 0},
                    "output_tokens": 20, "output_tokens_details": {"reasoning_tokens": 7}, "total_tokens": 120}
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .7}}, observed)
        _, result = self.post("/v1/decisions", {"input": "s", "questions": [{"type": "predicate", "instructions": "q"}]})
        self.assertEqual(result["usage"], observed)

    def test_broken_stream_returns_json_error(self):
        for data in ('data: invalid-json\n\n', 'data: []\n\n', 'data: null\n\n', 'data: {"type":"response.created"}\n\n'):
            Up.reply = lambda b: (200, data)
            status, result = self.post("/v1/systemone", BODY)
            self.assertEqual((status, result["error"]["code"]), (502, "invalid_output"))

    def test_retry_counts_all_observed_usage(self):
        def reply(body):
            return tool_reply({"q0": {"p_true": 7 if len(Up.seen) == 1 else .4}})
        Up.reply = reply
        status, result = self.post("/v1/systemone", BODY)
        self.assertEqual(status, 200)
        self.assertEqual(result["usage"], {"input_tokens": 200, "output_tokens": 40})

    def test_completed_response_is_terminal_despite_later_stream_garbage(self):
        _, obj = tool_reply({"q0": {"p_true": .7}})
        Up.reply = lambda b: (200, 'data: '+json.dumps({"type":"response.completed","response":obj})+'\n\ndata: invalid-json\n\n')
        status, result = self.post("/v1/systemone", BODY)
        self.assertEqual(status, 200)
        self.assertEqual(result["usage"], {"input_tokens": 100, "output_tokens": 20})

    def test_completed_stream_items_survive_an_empty_terminal_output(self):
        _, obj=tool_reply({"q0":{"p_true":.7}})
        item=obj['output'][0];item['status']='completed';obj['output']=[]
        events=[{'type':'response.output_item.done','output_index':0,'item':item},{'type':'response.completed','response':obj}]
        Up.reply=lambda b:(200,''.join('data: '+json.dumps(e)+'\n\n' for e in events))
        status,result=self.post('/v1/systemone',BODY)
        self.assertEqual(status,200)
        self.assertEqual(result['answers']['payment']['noul'],.7)
        self.assertEqual(result['usage'],{'input_tokens':100,'output_tokens':20})

    def test_done_items_without_terminal_response_are_not_success(self):
        _,obj=tool_reply({'q0':{'p_true':.7}})
        Up.reply=lambda b:(200,'data: '+json.dumps({'type':'response.output_item.done','output_index':0,'item':obj['output'][0]})+'\n\n')
        status,result=self.post('/v1/systemone',BODY)
        self.assertEqual((status,result['error']['code']),(502,'invalid_output'))

    def test_codex_oauth_stream_can_omit_content_type_but_not_terminal_proof(self):
        from jev_luna.transport import Route
        Up.omit_content_type=True;Up.reply=lambda b:tool_reply({'q0':{'p_true':.7}})
        # Mock the transport seam; production keeps the official fixed HTTPS origin.
        with patch.object(d,'TRANSPORT','subscription'),patch('jev_luna.transport.prepare',return_value=Route(f'http://127.0.0.1:{self.up.server_port}/responses',{},profile='subscription')):
            status,result=self.post('/v1/systemone',BODY)
            self.assertEqual(status,200)
            self.assertEqual(result['answers']['payment']['noul'],.7)
            Up.reply=lambda b:(200,'<html>not a decision</html>')
            status,result=self.post('/v1/systemone',BODY)
            self.assertEqual((status,result['error']['code']),(502,'invalid_output'))

    def test_missing_or_invalid_usage_is_not_fabricated_as_zero(self):
        for usage in (None, {}, {"input_tokens": True, "output_tokens": 20}, {"input_tokens": 100, "output_tokens": -1}):
            def reply(body):
                _, obj = tool_reply({"q0": {"p_true": .7}})
                if usage is None: obj.pop("usage")
                else: obj["usage"] = usage
                return 200, obj
            Up.reply = reply
            status, result = self.post("/v1/systemone", BODY)
            self.assertEqual((status, result["error"]["code"]), (502, "invalid_output"))

    def test_transient_gateway_failure_retries_the_same_luna_request(self):
        Up.reply = lambda b: (502, {"error": {"message": "temporary"}}) if len(Up.seen)==1 else tool_reply({"q0":{"p_true":.7}})
        status, result = self.post("/v1/systemone", BODY)
        self.assertEqual(status, 200)
        self.assertEqual(len(Up.seen), 2)
        self.assertEqual(Up.seen[0], Up.seen[1])
        self.assertEqual(result["usage"], {"input_tokens":100,"output_tokens":20})

    def test_rate_limit_headers_reach_the_native_client(self):
        for status in (429,529):
            Up.reply = lambda b: (status, {"error":{}}, {"Retry-After":"7", "Retry-After-Ms":"7000", "X-Private":"do-not-forward"})
            request=__import__('urllib.request',fromlist=['Request']).Request(self.url+"/v1/systemone",json.dumps(BODY).encode(),{"Content-Type":"application/json"})
            from test_http_service import OPEN
            import urllib.error
            with self.assertRaises(urllib.error.HTTPError) as caught: OPEN.open(request)
            error=caught.exception
            self.assertEqual(error.code,status)
            self.assertEqual(error.headers['Retry-After'],'7')
            self.assertEqual(error.headers['Retry-After-Ms'],'7000')
            self.assertNotIn('X-Private',error.headers)
            error.close()

    def test_global_refusal_does_not_override_valid_partial_report(self):
        def reply(body):
            status, obj = tool_reply({"q0": {"refused": True}, "q1": {"p_true": .7}})
            obj["output"].insert(0, {"type": "message", "content": [{"type": "refusal", "refusal": "declined"}]})
            return status, obj
        Up.reply = reply
        _, result = self.post("/v1/decisions", {"input": "s", "questions": [{"type": "predicate", "instructions": "q"}]*2})
        self.assertEqual(result["answers"][1]["probability"], .7)

    def test_large_state_does_not_have_artificial_2mib_limit(self):
        Up.reply = lambda b: tool_reply({"q0": {"p_true": .9}})
        self.assertEqual(self.post("/v1/systemone", {**BODY, "state": "x"*(2*1024*1024+1)})[0], 200)

    def test_dripping_sse_line_obeys_absolute_deadline(self):
        class Drip(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
                try:
                    for _ in range(40):
                        self.wfile.write(b'data: '); self.wfile.flush(); time.sleep(.025)
                except (BrokenPipeError, ConnectionResetError): pass
        server = HTTPServer(('127.0.0.1',0), Drip)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with patch.object(d,'prepare_auth',return_value=Route(f'http://127.0.0.1:{server.server_port}/responses',{})):
                start=time.monotonic()
                with self.assertRaises(d.Reject) as error: d.upstream_call({},.10)
                self.assertEqual(error.exception.status,504)
                self.assertLess(time.monotonic()-start,.25)
        finally: server.shutdown(); server.server_close()


if __name__ == "__main__": unittest.main()
