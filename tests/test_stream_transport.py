"""Streaming transport: SSE framing, connection reuse and request metadata. Local fixtures only; no native auth or inference."""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from jev_luna import core, subscription_auth as auth, transport
from jev_luna.lifecycle import RequestContext


def terminal():
    usage = {"input_tokens": 10, "output_tokens": 2,
             "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
             "output_tokens_details": {"reasoning_tokens": 0}}
    return b'data: '+json.dumps({"type": "response.completed", "response": {
        "model": "gpt-6-luna", "status": "completed", "output": [], "usage": usage}}).encode()+b'\n\n'


class Endpoint(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    seen = []
    body = terminal()
    chunked = False
    tail_gate = None
    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        type(self).seen.append({"authorization": self.headers.get('Authorization'),
                               "cookie": self.headers.get('Cookie'), "port": self.client_address[1],
                               "routing_hint": self.headers.get('x-codex-routing-hint'), "body": json.loads(raw)})
        body, chunked, tail_gate = type(self).body, type(self).chunked, type(self).tail_gate
        tail = b': after terminal\n\n' if tail_gate is not None else b''
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        if chunked:
            self.send_header('Transfer-Encoding', 'chunked')
        else:
            self.send_header('Content-Length', str(len(body)))
        self.send_header('Set-Cookie', 'must_not_persist=1; Path=/')
        self.end_headers()
        try:
            if chunked:
                self.wfile.write(f'{len(body):x}\r\n'.encode()+body+b'\r\n')
            else:
                self.wfile.write(body)
            self.wfile.flush()
            if tail_gate is not None:
                tail_gate.wait(2)
                self.wfile.write(f'{len(tail):x}\r\n'.encode()+tail+b'\r\n')
            if chunked:
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # The client correctly discards a connection with unread tail bytes.
    def log_message(self, *args):
        pass


class RequestMetadata(unittest.TestCase):
    def test_sse_newlines_at_every_chunk_boundary(self):
        for newline in (b'\r', b'\n', b'\r\n'):
            body = b'data: first'+newline+b'data: second'+newline+newline+b'unterminated'
            for split in range(len(body)+1):
                with self.subTest(newline=newline, split=split):
                    framer = transport._SSELines()
                    self.assertEqual(list(framer.feed(body[:split]))+list(framer.feed(body[split:])),
                                     [b'data: first', b'data: second', b''])
                    self.assertEqual(framer.pending, b'unterminated')

    def test_inference_route_constructs_no_stdlib_ssl_connection(self):
        ctx = RequestContext(time.monotonic()+2)
        with patch.object(auth, '_credentials', return_value=('test-token','A',('native','g','NO_CONSTRAINT'))), \
             patch.object(auth, 'getproxies', return_value={}), \
             patch.object(auth.http.client, 'HTTPSConnection') as unused:
            route = transport.prepare({'transport': 'subscription'}, ctx)
        unused.assert_not_called()
        self.assertEqual(route.url, 'https://chatgpt.com/backend-api/codex/responses')
        self.assertEqual(route.headers['Authorization'], 'Bearer test-token')
        self.assertEqual(ctx.auth_registration, 'A')
        with self.assertRaises(TypeError):
            route.headers['Authorization'] = 'changed'

    def test_same_request_owner_and_policy_still_cannot_change(self):
        for second in [('other','B',('native','g2','NO_CONSTRAINT')),
                       ('other','A',('native','g2','us'))]:
            ctx = RequestContext(time.monotonic()+2)
            with patch.object(auth,'_credentials',side_effect=[('t','A',('native','g','NO_CONSTRAINT')),second]), \
                 patch.object(auth,'getproxies',return_value={}):
                transport.prepare({'transport':'subscription'},ctx)
                with self.assertRaises(core.Reject) as result:
                    transport.prepare({'transport':'subscription'},ctx)
            self.assertEqual(result.exception.status,409)

    def test_fast_mode_is_opt_in_codex_priority_tier(self):
        qs = core.parse_systemone({"model": "jev-latest", "state": "s", "questions": {"q": {"type": "noul", "instructions": "x"}}})[1]
        self.assertEqual(core.SERVICE_TIER, "default")
        self.assertNotIn("service_tier", core.build_upstream("s", qs))
        with patch.object(core, "SERVICE_TIER", "fast"):
            self.assertEqual(core.build_upstream("s", qs)["service_tier"], "priority")

    def test_invalid_service_tier_fails_at_startup(self):
        for value, ok in (("fast", True), ("default", True), ("priority", False), ("", False)):
            with self.subTest(value=value):
                run = subprocess.run([sys.executable, "-c", "import jev_luna.core as c; print(c.SERVICE_TIER)"],
                                     env={**os.environ, "LDM_SERVICE_TIER": value}, capture_output=True, text=True,
                                     cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                self.assertEqual(run.returncode == 0, ok, run.stderr)
                if ok: self.assertEqual(run.stdout.strip(), value)
                else: self.assertIn("LDM_SERVICE_TIER must be default or fast", run.stderr)

    def test_rejection_categories_do_not_echo_model_text(self):
        self.assertEqual(core._readout_reason(ValueError('likelihood weights need a positive total')), 'zero_mass')
        self.assertEqual(core._readout_reason(ValueError('secret text')), 'invalid_readout')


class StreamingPool(unittest.TestCase):
    def setUp(self):
        Endpoint.seen = []; Endpoint.body = terminal()
        Endpoint.chunked = False; Endpoint.tail_gate = None
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Endpoint)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.client = transport.Client()
        self.url = f'http://127.0.0.1:{self.server.server_port}/responses'
    def tearDown(self):
        self.client.close(); self.server.shutdown(); self.server.server_close(); self.thread.join(2)
    def invoke(self, token, payload=None):
        events=[]
        ctx=RequestContext(time.monotonic()+3,observer=lambda name, fields: events.append((name,fields)))
        ctx.usage_merger=core.merge_usage;ctx.begin_attempt()
        result=self.client.call(transport.Route(self.url,{'Authorization':'Bearer '+token}),payload or {},ctx)
        return result,events,ctx.snapshot()
    def test_reuse_keeps_fresh_headers_and_no_session_cookies(self):
        self.invoke('A')
        _,events,accounting=self.invoke('B')
        self.assertEqual([x['authorization'] for x in Endpoint.seen],['Bearer A','Bearer B'])
        self.assertTrue(all(x['cookie'] is None for x in Endpoint.seen))
        self.assertEqual(Endpoint.seen[0]['port'],Endpoint.seen[1]['port'])
        self.assertTrue(any(name=='connection_reused' for name,_ in events))
        self.assertEqual(accounting['unknown_usage_attempts'],0)
        self.assertEqual(accounting['observed_usage']['input_tokens'],10)
    def test_service_tier_wire_and_returned_tier(self):
        _, events, _ = self.invoke('A')
        self.assertIsNone(Endpoint.seen[0]['routing_hint'])
        self.assertNotIn('service_tier', Endpoint.seen[0]['body'])
        self.assertIsNone(next(f for n, f in events if n == 'terminal_event')['service_tier'])
        Endpoint.body = terminal().replace(b'"status": "completed"', b'"status": "completed", "service_tier": "priority"')
        _, events, _ = self.invoke('A', {'model': 'gpt-6-luna', 'service_tier': 'priority'})
        self.assertEqual(Endpoint.seen[1]['routing_hint'], 'model=gpt-6-luna;tier=priority')
        self.assertEqual(Endpoint.seen[1]['body']['service_tier'], 'priority')
        self.assertEqual(next(f for n, f in events if n == 'terminal_event')['service_tier'], 'priority')

    def test_rejected_fast_request_is_not_retried_as_default(self):
        error = transport.provider_error(400, {"error": {"code": "invalid_request_error", "param": "service_tier"}})
        self.assertEqual((error.status, error.code, error.param, error.retryable), (400, "invalid_request_error", "service_tier", False))

    def test_budget_is_per_frame_not_per_network_chunk(self):
        event=b'data: '+json.dumps({'type':'response.created','padding':'x'*350}).encode()+b'\n\n'
        Endpoint.body=event+terminal()
        self.assertLess(len(event),512);self.assertLess(len(terminal()),512)
        self.assertGreater(len(Endpoint.body),512)
        with patch.object(transport,'MAX_FRAME_BYTES',512):
            result,_,_=self.invoke('A')
        self.assertEqual(result[0]['status'],'completed')
    def test_one_oversized_frame_still_fails(self):
        Endpoint.body=b'data: '+json.dumps({'type':'response.created','padding':'x'*900}).encode()+b'\n\n'+terminal()
        with patch.object(transport,'MAX_FRAME_BYTES',512), self.assertRaises(core.Reject) as caught:
            self.invoke('A')
        self.assertEqual(caught.exception.code,'response_budget')

    def test_cr_lf_and_crlf_multiline_json_and_comments(self):
        for newline in (b'\r', b'\n', b'\r\n'):
            Endpoint.body = (b': keepalive'+newline+b'data: {'+newline+
                             b'data: '+terminal()[len(b'data: {'): -2]+newline+newline)
            with self.subTest(newline=newline):
                _, _, accounting = self.invoke('A')
                self.assertEqual(accounting['observed_usage']['input_tokens'], 10)
                self.assertEqual(accounting['unknown_usage_attempts'], 0)

    def test_chunked_terminal_does_not_wait_for_or_reuse_an_unread_tail(self):
        gate = threading.Event()
        Endpoint.chunked = True; Endpoint.tail_gate = gate
        events = []
        context = RequestContext(time.monotonic()+.3, observer=lambda name, fields: events.append((name, fields)))
        context.usage_merger = core.merge_usage; context.begin_attempt()
        try:
            completed, _, _ = self.client.call(transport.Route(self.url, {'Authorization':'Bearer A'}), {}, context)
            self.assertEqual(completed['status'], 'completed')
            self.assertFalse(gate.is_set())
            self.assertEqual(context.snapshot()['observed_usage']['input_tokens'], 10)
            terminal_fields = next(fields for name, fields in events if name == 'terminal_event')
            self.assertFalse(terminal_fields['http_body_at_eof'])
            Endpoint.chunked = False; Endpoint.tail_gate = None
            _, second_events, accounting = self.invoke('B')
            self.assertNotEqual(Endpoint.seen[0]['port'], Endpoint.seen[1]['port'])
            self.assertFalse(any(name == 'connection_reused' for name, _ in second_events))
            self.assertEqual(accounting['observed_usage']['input_tokens'], 10)
        finally:
            gate.set()


if __name__=='__main__':
    unittest.main()
