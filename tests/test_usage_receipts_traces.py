"""Partial usage accounting, receipt atomicity, trace boundaries and opt-in measurement invariants."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from jev_luna import cli, core as d, subscription_auth as auth
from jev_luna.client import DecisionError, request
from jev_luna.lifecycle import RequestContext, bind, attempt_batch
from jev_luna.receipts import AtomicReceipt, RequestTrace, flush_traces
from jev_luna.transport import Route
from test_http_service import Base, OPEN, Up, tool_reply
from test_oauth_and_usage_accounting import BODY, USAGE


class PartialUsage(Base):
    def test_partial_usage_object_then_success_stays_partial(self):
        details = {k: v for k, v in USAGE.items() if k.endswith('_details')}
        for partial in (details, {'input_tokens': 7}, {**details, 'input_tokens': 7},
                        {'output_tokens': 3}, {'total_tokens': 10}):
            with self.subTest(partial=partial):
                Up.seen = []
                Up.reply = lambda _: (502, {'error': {}, 'usage': partial}) if len(Up.seen) == 1 else tool_reply({'q0': {'p_true': .9}})
                req = urllib.request.Request(self.url+'/v1/systemone', json.dumps(BODY).encode(), {'Content-Type': 'application/json'})
                with OPEN.open(req) as response:
                    self.assertEqual(response.headers['X-Decide-D-Usage-Status'], 'partial')
                    self.assertEqual(response.headers['X-Decide-D-Unknown-Usage-Attempts'], '1')
                    result = json.load(response)
                self.assertEqual(result['usage'], {'input_tokens': 100, 'output_tokens': 20})
                self.assertEqual(set(result), {'model', 'answers', 'usage'})
                Up.reply = lambda _: (403, {'error': {'code': 'permission_denied'}, 'usage': partial})
                status, error = self.post('/v1/systemone', BODY)
                self.assertEqual(status, 403)
                self.assertEqual(error['usage_status'], 'partial')
                self.assertEqual(error['unknown_usage_attempts'], 1)
                self.assertEqual(error['partial_usage_attempts'], [partial])
                self.assertEqual(error['observed_usage'], {})
                wrapped = DecisionError(status, error, {})
                self.assertEqual(wrapped.accounting['partial_usage_attempts'], [partial])

    def test_contradictory_total_is_invalid_and_counted_once(self):
        Up.reply = lambda _: (502, {'error': {}, 'usage': {**USAGE, 'total_tokens': 999}})
        status, error = self.post('/v1/systemone', BODY)
        self.assertEqual((status, error['error']['code']), (502, 'invalid_output'))
        self.assertEqual(error['unknown_usage_attempts'], 1)
        self.assertEqual(len(Up.seen), 1)
        self.assertEqual(error['observed_usage'], {})

    def test_complete_primary_without_details_is_partial_without_unknown_attempt(self):
        context = RequestContext(time.monotonic()+1)
        context.begin_attempt()
        context.observe({'input_tokens': 7, 'output_tokens': 2}, d.merge_usage)
        snapshot = context.snapshot()
        self.assertEqual(snapshot['usage_status'], 'partial')
        self.assertEqual(snapshot['unknown_usage_attempts'], 0)
        self.assertEqual(snapshot['observed_usage'], {'input_tokens': 7, 'output_tokens': 2, 'total_tokens': 9})


class NativeManagerFault(unittest.TestCase):
    def test_native_manager_faults_cannot_launch_login_or_network(self):
        with patch.object(auth,'_credentials',side_effect=auth.NativeAuthStateError('Native manager unavailable')),patch.object(auth.http.client,'HTTPSConnection') as network,patch.object(cli.subprocess,'run') as login:
            with self.assertRaises(auth.NativeAuthStateError):auth.connection(1)
            with patch.object(sys,'argv',['luna-decision','login']),contextlib.redirect_stderr(io.StringIO()):self.assertEqual(cli.main(),1)
        network.assert_not_called();login.assert_not_called()


class Receipts(unittest.TestCase):
    def test_two_processes_cannot_take_the_same_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'run.json'
            code = "from jev_luna.receipts import AtomicReceipt; import sys; AtomicReceipt(sys.argv[1], {'phase':'created','owner':sys.argv[2]})"
            children = [subprocess.Popen([sys.executable, '-c', code, str(path), str(i)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(2)]
            results = [child.communicate(timeout=5) for child in children]
            self.assertEqual(sorted(child.returncode for child in children), [0, 1], results)
            self.assertIn(json.loads(path.read_text())['owner'], ('0', '1'))

    def test_interrupted_update_preserves_complete_previous_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'run.json'
            code = "from jev_luna.receipts import AtomicReceipt; import os,sys; receipt=AtomicReceipt(sys.argv[1], {'phase':'created'}); os.replace=lambda *a:os._exit(17); receipt.save({'phase':'incomplete-update'})"
            child = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, timeout=5)
            self.assertEqual(child.returncode, 17)
            self.assertEqual(json.loads(path.read_text()), {'phase': 'created'})
            with self.assertRaises(FileExistsError): AtomicReceipt(path, {'phase': 'overwrite'})


class TraceBoundaries(Base):
    def test_http_low_score_and_timeout_have_distinct_metadata_without_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            self.srv.trace_directory = directory
            try:
                Up.reply = lambda _: tool_reply({'q0': {'p_true': .1}})
                status, result = self.post('/v1/systemone', BODY)
                self.assertEqual(status, 200)
                self.assertEqual(result['answers']['on']['noul'], .1)
                def delayed(_): time.sleep(.2); return tool_reply({'q0': {'p_true': .9}})
                Up.reply = delayed
                req = urllib.request.Request(self.url+'/v1/systemone', json.dumps(BODY).encode(), {'Content-Type': 'application/json', 'X-Decide-D-Timeout-Ms': '50'})
                with self.assertRaises(urllib.error.HTTPError) as raised: OPEN.open(req)
                self.assertEqual(raised.exception.code, 504)
                raised.exception.close()
                time.sleep(.1)
                self.assertTrue(flush_traces(3))
                traces = [json.loads(path.read_text()) for path in Path(directory).glob('*.json')]
            finally: self.srv.trace_directory = None
        self.assertEqual(len(traces), 2)
        low = next(t for t in traces if any(e['event'] == 'http_response' and e['status'] == 200 for e in t['events']))
        timeout = next(t for t in traces if any(e['event'] == 'cancelled' and e['code'] == 'deadline' for e in t['events']))
        self.assertNotIn('cancelled', [e['event'] for e in low['events']])
        self.assertEqual(timeout['events'][-1]['event'], 'finished')
        for trace in traces:
            self.assertIn('build_sha256', trace)
            serialized = json.dumps(trace)
            self.assertNotIn(BODY['state'], serialized)
            self.assertNotIn('Authorization', serialized)
            compiled = next(e for e in trace['events'] if e['event'] == 'compiled_batch')
            wire = next(e for e in trace['events'] if e['event'] == 'upstream_request')
            self.assertEqual(compiled['wire_payload_sha256'], wire['payload_sha256'])
            self.assertEqual(compiled['questions'], [{'original_id': 'on', 'readout_id': 'q0'}])

    def test_second_batch_failure_has_ordinal_and_original_mapping(self):
        body = {'model': 'gpt-6-luna', 'state': 'fixture', 'questions': {f'n{i}': {'type': 'noul', 'instructions': f'predicate {i}'} for i in range(32)}}
        state, questions = d.parse_systemone(body)
        with tempfile.TemporaryDirectory() as directory:
            trace = RequestTrace(directory, 'fixture', {'build_sha256': d.BUILD_SHA256})
            context = RequestContext(time.monotonic()+2, observer=trace)
            def call(payload, timeout):
                if attempt_batch.get() == 1: raise d.Reject(400, 'fixture_second_batch', 'fixture')
                return {f'q{i}': {'p_true': .1} for i in range(24)}, USAGE
            with patch.object(d, 'prepare_auth', return_value=Route('http://127.0.0.1:1', {})), patch.object(d, 'upstream_call', side_effect=call):
                with self.assertRaises(d.Reject): d._infer(state, questions, context.deadline, context)
            self.assertTrue(flush_traces(3))
            events = json.loads((Path(directory)/'fixture.json').read_text())['events']
        second = next(e for e in events if e['event'] == 'compiled_batch' and e['batch'] == 1)
        self.assertEqual(second['questions'][0], {'original_id': 'n24', 'readout_id': 'q0'})
        self.assertTrue(any(e['event'] == 'attempt_finished' and e['code'] == 'fixture_second_batch' and e['batch'] == 1 for e in events))
        self.assertTrue(any(e['event'] == 'cancelled' and e['code'] == 'sibling_failure' for e in events))
