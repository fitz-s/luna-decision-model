"""Delayed hedge: deterministic fault schedules against a fake upstream and a local SSE server. No network."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from jev_luna import core as d, transport
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext, attempt_scope, current_context
from jev_luna.transport import Route

QS = d.parse_systemone({'model': 'gpt-6-luna', 'state': 's', 'questions': {'q': {'type': 'noul', 'instructions': 'x'}}})[1]
ANSWER = {'q0': {'p_true': .8}}
REFUSAL = {'q0': {'refused': True}}


def usage(tokens):
    return {'input_tokens': tokens, 'output_tokens': 1}


class Upstream:
    """Each physical attempt follows one step: (seconds before outcome or None to stall, outcome, output_first)."""
    def __init__(self, *steps):
        self.steps, self.calls, self.cancelled, self.lock = list(steps), [], [], threading.Lock()

    def __call__(self, payload, timeout):
        scope, context = attempt_scope.get(), current_context.get()
        with self.lock:
            index = len(self.calls)
            self.calls.append(time.monotonic())
        delay, outcome, output_first = self.steps[index]
        stop = threading.Event()
        drop, remove = scope.register(stop.set), context.register(stop.set)
        try:
            scope.mark_sent()
            if output_first:
                scope.output.set()
            if stop.wait(delay):
                self.cancelled.append(index)
                context.remaining()
                raise Reject(499, 'cancelled', 'Request cancelled')
            if isinstance(outcome, Reject):
                raise outcome
            return outcome, usage(10+index)
        finally:
            drop(); remove()


class Hedge(unittest.TestCase):
    def run_decision(self, upstream, *, after=100, deadline=2.0, slots=4, spare=True, cancel_after_calls=None):
        events = []
        context = RequestContext(time.monotonic()+deadline, observer=lambda name, fields: events.append((name, fields)))
        if cancel_after_calls:
            def cancel():
                while len(upstream.calls) < cancel_after_calls: time.sleep(.005)
                context.cancel()
            threading.Thread(target=cancel, daemon=True).start()
        client = type('Client', (), {'spare': lambda self, route: spare})()
        with patch.object(d, 'HEDGE_AFTER_MS', after), patch.object(d, '_slots', threading.BoundedSemaphore(slots)), \
             patch.object(d, 'prepare_auth', return_value=Route('http://127.0.0.1:1', {})), \
             patch.object(d, 'upstream_call', side_effect=upstream), patch('jev_luna.transport.client', return_value=client):
            try:
                result = d.decide('s', QS, context=context)
            except Reject as error:
                result = error
        time.sleep(.05)  # let a cancelled loser enter its unknown usage
        names = [name for name, _ in events]
        resolved = next((fields for name, fields in events if name == 'hedge_resolved'), None)
        return result, context, names, resolved

    def test_primary_finishing_just_after_threshold_wins_and_hedge_usage_is_unknown(self):
        upstream = Upstream((.13, ANSWER, False), (1, ANSWER, False))
        result, context, names, resolved = self.run_decision(upstream)
        self.assertEqual(result[0][0]['p'], .8)
        self.assertEqual(len(upstream.calls), 2); self.assertEqual(upstream.cancelled, [1])
        self.assertEqual(resolved['winner'], 'primary'); self.assertIn('hedge_fired', names)
        self.assertFalse(context.cancelled.is_set())
        accounting = context.snapshot()
        self.assertEqual(accounting['observed_usage']['input_tokens'], 10)
        self.assertEqual(accounting['unknown_usage_attempts'], 1)
        self.assertEqual(accounting['usage_status'], 'partial')

    def test_hedge_timer_starts_at_send_and_does_not_fire_after_output(self):
        upstream = Upstream((.3, ANSWER, True))
        result, _, names, resolved = self.run_decision(upstream)
        self.assertEqual(len(upstream.calls), 1); self.assertNotIn('hedge_fired', names)
        self.assertEqual(resolved['winner'], 'primary')

    def test_stalled_primary_loses_to_hedge_without_cancelling_the_request(self):
        upstream = Upstream((None, ANSWER, False), (.02, ANSWER, False))
        started = time.monotonic()
        result, context, _, resolved = self.run_decision(upstream)
        self.assertLess(time.monotonic()-started, .5)
        self.assertEqual(resolved['winner'], 'hedge'); self.assertEqual(upstream.cancelled, [0])
        self.assertGreaterEqual(upstream.calls[1]-upstream.calls[0], .1)
        self.assertFalse(context.cancelled.is_set())
        self.assertEqual(context.snapshot()['observed_usage']['input_tokens'], 11)

    def test_both_stalled_reach_the_deadline_and_stop_both(self):
        upstream = Upstream((None, ANSWER, False), (None, ANSWER, False))
        result, context, _, _ = self.run_decision(upstream, deadline=.4)
        self.assertIsInstance(result, Reject); self.assertEqual((result.status, result.code), (504, 'deadline'))
        self.assertEqual(sorted(upstream.cancelled), [0, 1])
        self.assertEqual(context.snapshot()['unknown_usage_attempts'], 2)

    def test_refusal_is_a_winning_outcome(self):
        upstream = Upstream((None, ANSWER, False), (.02, REFUSAL, False))
        result, _, _, resolved = self.run_decision(upstream)
        self.assertEqual(result[0], [{'refused': True}]); self.assertEqual(resolved['winner'], 'hedge')

    def test_quota_rejection_is_never_hedged_or_retried(self):
        quota = Reject(429, 'usage_limit_reached', 'quota')
        upstream = Upstream((.01, quota, False), (0, ANSWER, False))
        result, _, names, _ = self.run_decision(upstream)
        self.assertEqual(result.code, 'usage_limit_reached'); self.assertEqual(len(upstream.calls), 1)
        self.assertNotIn('hedge_fired', names)

    def test_caller_cancellation_stops_primary_and_hedge(self):
        upstream = Upstream((None, ANSWER, False), (None, ANSWER, False))
        started = time.monotonic()
        result, context, _, _ = self.run_decision(upstream, deadline=3, cancel_after_calls=2)
        self.assertLess(time.monotonic()-started, .6)
        self.assertEqual((result.status, result.code), (499, 'cancelled'))
        self.assertEqual(sorted(upstream.cancelled), [0, 1])

    def test_no_spare_slot_or_connection_means_no_hedge(self):
        for options, reason in (({'slots': 1}, 'inference_slots'), ({'spare': False}, 'connection_pool')):
            with self.subTest(reason=reason):
                upstream = Upstream((.25, ANSWER, False), (0, ANSWER, False))
                result, context, names, _ = self.run_decision(upstream, **options)
                self.assertEqual(len(upstream.calls), 1); self.assertNotIn('hedge_fired', names)
                self.assertIn('hedge_skipped', names)
                self.assertEqual(result[0][0]['p'], .8)

    def test_hedge_replaces_retries_at_most_two_physical_attempts(self):
        retryable = Reject(502, 'upstream_failure', 'transient', retryable=True)
        upstream = Upstream((.01, retryable, False), (.01, retryable, False), (0, ANSWER, False))
        result, _, names, resolved = self.run_decision(upstream)
        self.assertEqual(result.code, 'upstream_failure'); self.assertEqual(len(upstream.calls), 2)
        self.assertNotIn('hedge_fired', names); self.assertIsNone(resolved['winner'])
        upstream = Upstream((.01, retryable, False), (None, ANSWER, False))
        result, _, names, _ = self.run_decision(upstream, deadline=.5)
        self.assertEqual(result.code, 'deadline'); self.assertEqual(len(upstream.calls), 2)
        self.assertNotIn('hedge_fired', names)

    def test_failed_hedge_waits_for_the_primary(self):
        quota = Reject(429, 'rate_limit_exceeded', 'limited')
        upstream = Upstream((.25, ANSWER, False), (.01, quota, False))
        result, _, _, resolved = self.run_decision(upstream)
        self.assertEqual(result[0][0]['p'], .8); self.assertEqual(resolved['winner'], 'primary')
        self.assertEqual(resolved['codes'], {'hedge': 'rate_limit_exceeded'})

    def test_reasoning_batches_keep_the_three_attempt_retry(self):
        retryable = Reject(502, 'upstream_failure', 'transient', retryable=True)
        upstream = Upstream(*[(.0, retryable, False)]*3)
        with patch.object(d, 'REASONING_EFFORT', 'low'):
            result, _, names, _ = self.run_decision(upstream)
        self.assertEqual(len(upstream.calls), 3); self.assertNotIn('hedge_resolved', names)

    def test_loser_usage_is_claimed_exactly_once(self):
        from jev_luna.lifecycle import Attempt
        context = RequestContext(time.monotonic()+1); context.usage_merger = d.merge_usage
        context.begin_attempt(); scope = Attempt()
        transport.observe(usage(5), context, scope); transport.observe({}, context, scope)
        self.assertEqual(context.snapshot()['observed_usage']['input_tokens'], 5)
        self.assertEqual(context.snapshot()['unknown_usage_attempts'], 0)

    def test_invalid_hedge_setting_fails_at_startup(self):
        for value, ok in (('0', True), ('5000', True), ('-1', False), ('1.5', False), ('120000', False), ('x', False)):
            with self.subTest(value=value):
                run = subprocess.run([sys.executable, '-c', 'import jev_luna.core as c; print(c.HEDGE_AFTER_MS)'],
                                     env={**os.environ, 'LDM_HEDGE_AFTER_MS': value}, capture_output=True, text=True,
                                     cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                self.assertEqual(run.returncode == 0, ok, run.stderr)


def completed_sse():
    text = json.dumps({'q0': {'refused': False, 'answer': {'p_true': .7}}})
    events = [{'type': 'response.created', 'response': {}},
              {'type': 'response.output_text.delta', 'delta': text},
              {'type': 'response.completed', 'response': {'model': 'gpt-6-luna', 'status': 'completed', 'usage': usage(9),
               'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}}]
    return b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events)


class HedgeOverTransport(unittest.TestCase):
    def test_stalled_sse_primary_is_disconnected_when_the_hedge_wins(self):
        seen, closed = [], threading.Event()
        class Stream(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                seen.append(time.monotonic())
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                if len(seen) == 1:
                    self.end_headers(); self.wfile.write(b': keepalive\n\ndata: {"type":"response.created"}\n\n'); self.wfile.flush()
                    self.connection.settimeout(3)
                    try:
                        if self.connection.recv(1) == b'': closed.set()
                    except OSError: closed.set()
                    return
                body = completed_sse(); self.send_header('Content-Length', str(len(body))); self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Stream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        events = []
        context = RequestContext(time.monotonic()+3, observer=lambda name, fields: events.append((name, fields)))
        try:
            with patch.object(d, 'HEDGE_AFTER_MS', 150), \
                 patch.object(d, 'prepare_auth', return_value=Route(f'http://127.0.0.1:{server.server_port}/responses', {})):
                answers, observed, _ = d.decide('s', QS, context=context)
            self.assertEqual(answers[0]['p'], .7)
            self.assertTrue(closed.wait(1))
            self.assertGreaterEqual(seen[1]-seen[0], .14)
            resolved = next(fields for name, fields in events if name == 'hedge_resolved')
            self.assertEqual(resolved['winner'], 'hedge'); self.assertFalse(resolved['timings']['primary']['first_output'])
            self.assertFalse(context.cancelled.is_set()); self.assertEqual(observed['input_tokens'], 9)
            deadline = time.monotonic()+1
            while d._stats['active_calls'] and time.monotonic() < deadline: time.sleep(.01)
            self.assertEqual(d._stats['active_calls'], 0)
            self.assertEqual(context.snapshot()['unknown_usage_attempts'], 1)
        finally:
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
