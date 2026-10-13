"""Responses Lite form, Responses WebSocket transport and their capability fallback, against a local aiohttp upstream."""
import asyncio
import json
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from aiohttp import web

from jev_luna import core, transport
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext

QS = core.parse_systemone({'model': 'gpt-6-luna', 'state': {'parcel': 'red'},
                           'questions': {'q': {'type': 'noul', 'instructions': 'Is it red?'}}})[1]
TEXT = json.dumps({'q0': {'refused': False, 'answer': {'p_true': .9}}})
USAGE = {'input_tokens': 10, 'output_tokens': 2}


def events(text=TEXT, terminal='response.completed'):
    response = {'model': 'gpt-6-luna', 'status': 'completed', 'usage': USAGE,
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}
    return [{'type': 'response.created', 'response': {}}, {'type': 'response.output_text.delta', 'delta': text},
            {'type': terminal, 'response': response}]


class Upstream:
    """POST /responses answers SSE; GET /responses upgrades to the Responses WebSocket."""
    def __init__(self):
        self.posts, self.handshakes, self.messages, self.closed = [], [], [], []
        self.post_status = self.post_error = self.handshake_status = None
        self.lite_rejection = self.post_events = None
        self.ws_script = None  # async (ws, message, index) -> None; default answers events()
        self.close_after_reply = False
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True).start()
        ready.wait(5)
        self.url = f'http://127.0.0.1:{self.port}/backend-api/codex/responses'

    def _run(self, ready):
        asyncio.set_event_loop(self.loop)
        app = web.Application()
        app.router.add_post('/backend-api/codex/responses', self.post)
        app.router.add_get('/backend-api/codex/responses', self.socket)
        self.runner = web.AppRunner(app)
        self.loop.run_until_complete(self.runner.setup())
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        self.loop.run_until_complete(site.start())
        self.port = site._server.sockets[0].getsockname()[1]
        ready.set()
        self.loop.run_forever()

    def stop(self):
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)

    async def post(self, request):
        body = await request.json()
        self.posts.append({'headers': dict(request.headers), 'body': body})
        lite = request.headers.get(transport.LITE_HEADER) == 'true'
        if lite and self.lite_rejection:
            status, error = self.lite_rejection
            return web.json_response({'error': error}, status=status)
        if self.post_status:
            return web.json_response({'error': self.post_error}, status=self.post_status)
        payload = b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in (self.post_events or events()))
        return web.Response(body=payload, content_type='text/event-stream')

    async def socket(self, request):
        self.handshakes.append(dict(request.headers))
        if self.handshake_status:
            return web.Response(status=self.handshake_status)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        connection = len(self.handshakes)
        async for frame in ws:
            message = json.loads(frame.data)
            self.messages.append((connection, message))
            if self.ws_script:
                await self.ws_script(ws, message, len(self.messages))
            else:
                for event in events():
                    await ws.send_str(json.dumps(event))
            if self.close_after_reply:
                await ws.close()
        self.closed.append(connection)
        return ws


class Profiles(unittest.TestCase):
    def setUp(self):
        transport.unavailable.clear()
        self.up = Upstream()
        self.client = transport.Client()
        self.payload = {**core.build_upstream({'parcel': 'red'}, QS), 'model': 'gpt-6-luna'}
        self.payload.pop('max_output_tokens')

    def tearDown(self):
        self.client.close(); self.up.stop(); transport.unavailable.clear()

    def call(self, *, lite=False, websocket=False, headers=None, payload=None, deadline=3, context=None):
        names = []
        context = context or RequestContext(time.monotonic()+deadline, observer=lambda name, fields: names.append((name, fields)))
        context.usage_merger = core.merge_usage; context.begin_attempt()
        route = transport.Route(self.up.url, headers or {'Authorization': 'Bearer A', 'Content-Type': 'application/json',
                                                         'Accept': 'text/event-stream'}, lite=lite, websocket=websocket)
        completed, _, _ = self.client.call(route, payload or self.payload, context)
        return completed, names, context


class Lite(Profiles):
    def test_lite_off_sends_the_standard_payload_unchanged(self):
        self.call()
        sent = self.up.posts[0]
        self.assertEqual(sent['body'], self.payload)
        self.assertNotIn(transport.LITE_HEADER, sent['headers'])

    def test_lite_golden_payload_and_header(self):
        self.call(lite=True)
        sent = self.up.posts[0]
        self.assertEqual(sent['headers'][transport.LITE_HEADER], 'true')
        standard = self.payload
        expected = {key: value for key, value in standard.items() if key != 'instructions'}
        expected.update({
            'input': [{'type': 'additional_tools', 'id': 'at_e546a61d-0b15-515a-bdd4-835f1a57f54d', 'role': 'developer', 'tools': []},
                      {'type': 'message', 'id': 'msg_'+str(uuid.uuid5(transport.LITE_IDS, standard['instructions'])), 'role': 'developer',
                       'content': [{'type': 'input_text', 'text': standard['instructions']}]}]+standard['input'],
            'tool_choice': 'auto', 'parallel_tool_calls': False,
            'reasoning': {'effort': 'none', 'context': 'all_turns'}})
        body = sent['body']
        self.assertEqual(body, expected)
        self.assertNotIn('instructions', body); self.assertNotIn('tools', body)
        # Stable item identities across requests; the JSON schema and state/question messages are unchanged.
        self.assertEqual(transport.lite(standard)['input'][:2], body['input'][:2])
        self.assertEqual(body['input'][2:], standard['input']); self.assertEqual(body['text'], standard['text'])

    def test_lite_capability_rejection_falls_back_once_for_the_process(self):
        self.up.lite_rejection = (400, {'code': 'invalid_request_error', 'param': 'input[0].type'})
        completed, names, _ = self.call(lite=True)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(len(self.up.posts), 2)
        self.assertNotIn(transport.LITE_HEADER, self.up.posts[1]['headers'])
        self.assertEqual(self.up.posts[1]['body'], self.payload)
        self.assertIn('responses_lite', transport.unavailable)
        self.assertEqual(next(f for n, f in names if n == 'capability_fallback')['capability'], 'responses_lite')
        self.call(lite=True)
        self.assertEqual(len(self.up.posts), 3); self.assertNotIn(transport.LITE_HEADER, self.up.posts[2]['headers'])
        with patch.object(core, 'RESPONSES_LITE', True):
            self.assertEqual(core._protocols()['responses_lite'], {'configured': True, 'active': False})

    def test_lite_never_falls_back_on_auth_quota_or_unrelated_fields(self):
        cases = [(401, {'code': 'token_expired'}), (429, {'code': 'usage_limit_reached'}),
                 (403, {'code': 'subscription_sharing_user_not_eligible'}),
                 (400, {'code': 'invalid_request_error', 'param': 'input[2].content[0].image_url'}),
                 (400, {'code': 'context_length_exceeded'})]
        for status, error in cases:
            with self.subTest(status=status, error=error):
                self.up.posts.clear(); self.up.lite_rejection = (status, error)
                with self.assertRaises(Reject):
                    self.call(lite=True)
                self.assertEqual(len(self.up.posts), 1)
                self.assertNotIn('responses_lite', transport.unavailable)

    def test_lite_never_falls_back_after_the_stream_started(self):
        self.up.post_events = events()[:2]+[{'type': 'error', 'code': 'invalid_request_error'}]
        with self.assertRaises(Reject) as raised:
            self.call(lite=True)
        self.assertEqual(raised.exception.code, 'invalid_request_error')
        self.assertEqual(len(self.up.posts), 1); self.assertNotIn('responses_lite', transport.unavailable)

    def test_lite_incomplete_and_refusal_keep_standard_handling(self):
        incomplete = events(terminal='response.incomplete')
        async def script(ws, message, index):
            for event in incomplete: await ws.send_str(json.dumps(event))
        self.up.ws_script = script
        with self.assertRaises(Reject) as raised:
            self.call(lite=True, websocket=True)
        self.assertEqual(raised.exception.code, 'incomplete_response')
        self.assertNotIn('responses_lite', transport.unavailable)
        refusal = {'model': 'gpt-6-luna', 'status': 'completed', 'usage': USAGE,
                   'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}
        async def refuse(ws, message, index):
            await ws.send_str(json.dumps({'type': 'response.completed', 'response': refusal}))
        self.up.ws_script = refuse
        context = RequestContext(time.monotonic()+3)
        with patch.object(core, 'prepare_auth', return_value=transport.Route(self.up.url, {}, lite=True, websocket=True)), \
             patch('jev_luna.transport.client', return_value=self.client):
            answers, observed, _ = core.decide({'parcel': 'red'}, QS, context=context)
        self.assertEqual(answers, [{'refused': True}]); self.assertEqual(observed['input_tokens'], 10)


class WebSocket(Profiles):
    def test_handshake_headers_and_independent_response_create(self):
        self.call(websocket=True)
        tiered = {**self.payload, 'service_tier': 'priority'}
        self.call(websocket=True, payload=tiered)
        first = self.up.handshakes[0]
        self.assertEqual(first['OpenAI-Beta'], 'responses_websockets=2026-02-06')
        self.assertEqual(first['x-codex-routing-hint'], 'model=gpt-6-luna')
        self.assertEqual(first['Authorization'], 'Bearer A')
        self.assertNotIn('Content-Type', first)
        self.assertEqual(self.up.handshakes[1]['x-codex-routing-hint'], 'model=gpt-6-luna;tier=priority')
        self.assertEqual(len(self.up.handshakes), 2)  # tiers never share a socket
        message = self.up.messages[0][1]
        self.assertEqual(message, {'type': 'response.create', **self.payload})
        self.assertNotIn('previous_response_id', message)
        self.assertEqual(self.up.posts, [])

    def test_lite_over_websocket_marks_client_metadata(self):
        self.call(lite=True, websocket=True)
        message = self.up.messages[0][1]
        self.assertEqual(message['client_metadata'], {transport.LITE_WS_METADATA: 'true'})
        self.assertNotIn(transport.LITE_HEADER.lower(), {k.lower() for k in self.up.handshakes[0]})
        self.assertEqual(message['input'][0]['type'], 'additional_tools')

    def test_socket_reuse_is_partitioned_and_one_decision_per_socket(self):
        _, names, _ = self.call(websocket=True)
        _, names, _ = self.call(websocket=True)
        self.assertEqual(len(self.up.handshakes), 1); self.assertIn('websocket_reused', [n for n, _ in names])
        self.call(websocket=True, headers={'Authorization': 'Bearer B'})
        self.assertEqual(len(self.up.handshakes), 2)
        release = threading.Event()
        async def slow(ws, message, index):
            await asyncio.get_running_loop().run_in_executor(None, release.wait, 2)
            for event in events(): await ws.send_str(json.dumps(event))
        self.up.ws_script = slow
        threads = [threading.Thread(target=self.call, kwargs={'websocket': True}) for _ in range(2)]
        for thread in threads: thread.start()
        deadline = time.monotonic()+2
        while len(self.up.messages) < 5 and time.monotonic() < deadline: time.sleep(.01)
        release.set()
        for thread in threads: thread.join(3)
        connections = [connection for connection, _ in self.up.messages[3:]]
        self.assertEqual(len(set(connections)), 2)

    def test_cancellation_closes_the_socket_and_it_is_not_reused(self):
        async def stall(ws, message, index):
            await ws.send_str(json.dumps({'type': 'response.created', 'response': {}}))
        self.up.ws_script = stall
        context = RequestContext(time.monotonic()+3)
        threading.Timer(.2, context.cancel).start()
        started = time.monotonic()
        with self.assertRaises(Reject) as raised:
            self.call(websocket=True, context=context)
        self.assertEqual(raised.exception.status, 499); self.assertLess(time.monotonic()-started, 1)
        deadline = time.monotonic()+2
        while not self.up.closed and time.monotonic() < deadline: time.sleep(.01)
        self.assertEqual(self.up.closed, [1])
        self.up.ws_script = None
        self.call(websocket=True)
        self.assertEqual(len(self.up.handshakes), 2)

    def test_idle_socket_closed_by_server_reconnects_transparently(self):
        self.up.close_after_reply = True
        self.call(websocket=True)
        deadline = time.monotonic()+2
        while not self.up.closed and time.monotonic() < deadline: time.sleep(.01)
        completed, _, context = self.call(websocket=True)
        self.assertEqual(completed['status'], 'completed'); self.assertEqual(len(self.up.handshakes), 2)
        self.assertEqual(context.snapshot()['observed_usage']['input_tokens'], 10)

    def test_late_frame_of_an_earlier_response_never_answers_the_next_decision(self):
        def answer(p, rid):
            text = json.dumps({'q0': {'refused': False, 'answer': {'p_true': p}}})
            sent = events(text)
            for event in sent[::2]: event['response']['id'] = rid  # created and terminal
            return sent
        async def script(ws, message, index):
            sent = answer(.9, 'resp_1')+[{'type': 'response.done', 'response': answer(.9, 'resp_1')[-1]['response']}] \
                if index == 1 else answer(.1, 'resp_2')
            for event in sent: await ws.send_str(json.dumps(event))
        self.up.ws_script = script
        first, _, _ = self.call(websocket=True)
        second, _, _ = self.call(websocket=True)
        text = lambda done: json.loads(done['output'][0]['content'][0]['text'])['q0']['answer']['p_true']
        self.assertEqual((text(first), text(second)), (.9, .1))
        self.assertEqual(second['id'], 'resp_2')

    def test_reused_socket_closing_after_send_is_not_resent(self):
        async def script(ws, message, index):
            if index == 2:
                await ws.close(); return
            for event in events(): await ws.send_str(json.dumps(event))
        self.up.ws_script = script
        self.call(websocket=True)
        with self.assertRaises(Reject) as raised:
            self.call(websocket=True)
        self.assertEqual((raised.exception.reason, raised.exception.retryable), ('websocket_closed', True))
        self.assertEqual((len(self.up.messages), len(self.up.handshakes)), (2, 1))

    def test_terminal_validation_matches_sse(self):
        bad = [({'type': 'response.output_item.done', 'output_index': 0, 'item': {'a': 1}},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': {'a': 2}}), ()]
        for script, reason in zip(bad, ('conflicting_output_item', 'websocket_closed')):
            with self.subTest(reason=reason):
                async def send(ws, message, index, script=script):
                    for event in script: await ws.send_str(json.dumps(event))
                    await ws.close()
                self.up.ws_script = send
                with self.assertRaises(Reject) as raised:
                    self.call(websocket=True)
                self.assertEqual((raised.exception.code, raised.exception.reason), ('invalid_output', reason))
        async def quota(ws, message, index):
            await ws.send_str(json.dumps({'type': 'error', 'status': 429, 'error': {'code': 'usage_limit_reached'},
                                          'headers': {'retry-after': '7'}}))
        self.up.ws_script = quota
        with self.assertRaises(Reject) as raised:
            self.call(websocket=True)
        self.assertEqual((raised.exception.status, raised.exception.code), (429, 'usage_limit_reached'))
        self.assertEqual(raised.exception.headers.get('Retry-After'), '7')
        self.assertEqual(self.up.posts, []); self.assertNotIn('websocket', transport.unavailable)

    def test_unsupported_handshake_falls_back_to_http_for_the_process(self):
        self.up.handshake_status = 426
        completed, names, _ = self.call(websocket=True)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(len(self.up.posts), 1); self.assertEqual(self.up.posts[0]['body'], self.payload)
        self.assertIn('websocket', transport.unavailable)
        fallback = next(f for n, f in names if n == 'capability_fallback')
        self.assertEqual((fallback['capability'], fallback['permanent']), ('websocket', True))
        self.call(websocket=True)
        self.assertEqual(len(self.up.handshakes), 1); self.assertEqual(len(self.up.posts), 2)
        with patch.object(core, 'UPSTREAM_TRANSPORT', 'websocket'):
            self.assertEqual(core._protocols()['upstream_transport'], {'configured': 'websocket', 'active': 'http'})

    def test_auth_and_quota_handshakes_never_fall_back(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                self.up.handshake_status = status
                with self.assertRaises(Reject) as raised:
                    self.call(websocket=True)
                self.assertEqual(raised.exception.status, status)
                self.assertEqual(raised.exception.auth_recoverable, status == 401)
                self.assertEqual(self.up.posts, []); self.assertNotIn('websocket', transport.unavailable)

    def test_connection_limit_sends_only_this_decision_over_http(self):
        async def limited(ws, message, index):
            await ws.send_str(json.dumps({'type': 'error', 'status': 429,
                                          'error': {'code': 'websocket_connection_limit_reached'}}))
        self.up.ws_script = limited
        completed, _, _ = self.call(websocket=True)
        self.assertEqual(completed['status'], 'completed'); self.assertEqual(len(self.up.posts), 1)
        self.assertNotIn('websocket', transport.unavailable)


class Health(unittest.TestCase):
    def test_health_and_doctor_report_configured_and_active(self):
        from jev_luna import doctor
        transport.unavailable.clear()
        self.assertEqual(core._protocols(), {'upstream_protocol': 'responses-sse',
            'upstream_transport': {'configured': 'http', 'active': 'http'},
            'responses_lite': {'configured': False, 'active': False}})
        with patch.object(core, 'UPSTREAM_TRANSPORT', 'websocket'), patch.object(core, 'RESPONSES_LITE', True):
            self.assertEqual(core._protocols()['upstream_protocol'], 'responses-websocket')
            self.assertEqual(core.transport_settings()['upstream_transport'], 'websocket')
        self.assertIn("'hedge_after_ms'", open(doctor.__file__).read())

    def test_invalid_protocol_settings_fail_at_startup(self):
        import os, subprocess, sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name, value, ok in (('LDM_RESPONSES_LITE', '1', True), ('LDM_RESPONSES_LITE', 'true', False),
                                ('LDM_TRANSPORT', 'websocket', True), ('LDM_TRANSPORT', 'ws', False)):
            with self.subTest(name=name, value=value):
                run = subprocess.run([sys.executable, '-c', 'import jev_luna.core'], env={**os.environ, name: value},
                                     capture_output=True, text=True, cwd=root)
                self.assertEqual(run.returncode == 0, ok, run.stderr)


if __name__ == '__main__':
    unittest.main()
