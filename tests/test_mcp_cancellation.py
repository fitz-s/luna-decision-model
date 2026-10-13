"""Model-free stdio cancellation through the actual MCP, HTTP and SSE clients."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from jev_luna import core
from jev_luna.server import BoundedHTTPServer
from jev_luna.transport import Route

KEY = 'jvl_local_' + 'mcp_cancellation_fixture_' * 2
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install the MCP extra to run stdio cancellation regressions')
class MCPCancellation(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix='mcp-cancellation-')
        key = Path(self.home.name) / 'service.key'
        key.write_text(KEY)
        key.chmod(0o600)
        self.changed = threading.Condition()
        self.entered = 0
        self.closed = 0
        self.readout_sizes = []
        fixture = self

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'data: {"type":"response.created"}\n\n')
                self.wfile.flush()
                with fixture.changed:
                    fixture.entered += 1
                    fixture.readout_sizes.append(len(payload['text']['format']['schema']['required']))
                    fixture.changed.notify_all()
                self.connection.settimeout(5)
                try:
                    if self.connection.recv(1) == b'':
                        with fixture.changed:
                            fixture.closed += 1
                            fixture.changed.notify_all()
                except OSError:
                    pass

        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.backend = BoundedHTTPServer(('127.0.0.1', 0), core.H, local_key=KEY)
        self.backend_thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.backend_thread.start()
        self.route = patch.object(core, 'prepare_auth', return_value=Route(
            f'http://127.0.0.1:{self.upstream.server_port}/responses', {}))
        self.route.start()
        self.upstream_calls_before = core._stats['upstream_calls']
        env = {**os.environ, 'JEV_LUNA_HOME': self.home.name,
               'CODEX_HOME': str(Path(self.home.name) / 'unused-native-home'),
               'DECIDE_D_URL': f'http://127.0.0.1:{self.backend.server_port}/v1/systemone',
               'LUNA_TEST_SOURCE_ROOT': str(ROOT)}
        env.pop('DECIDE_D_CLIENT_TIMEOUT', None)  # Exercise the original 30s MCP deadline.
        # -I isolates host startup hooks. Only this explicit test driver exposes
        # the source tree; there is no production mock/configuration switch.
        driver = ('import os,sys;sys.path.insert(0,os.environ["LUNA_TEST_SOURCE_ROOT"]);'
                  'from jev_luna.cli import main;sys.argv=["luna-decision","mcp"];sys.exit(main())')
        self.proc = subprocess.Popen([sys.executable, '-I', '-c', driver], cwd=self.home.name,
                                     env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = []
        self.inbox = queue.Queue()
        def collect():
            for line in iter(self.proc.stdout.readline, b''):
                self.lines.append(line)
                self.inbox.put(line)
        self.reader = threading.Thread(target=collect, daemon=True)
        self.reader.start()

    def tearDown(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try: self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=2)
        self.reader.join(1)
        self.proc.stdout.close()
        self.proc.stderr.close()
        self.backend.cancel_requests()
        self.backend.shutdown(); self.backend.server_close(); self.backend_thread.join(1)
        self.upstream.shutdown(); self.upstream.server_close(); self.upstream_thread.join(1)
        self.route.stop()
        self.home.cleanup()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode() + b'\n')
        self.proc.stdin.flush()

    def response(self, identifier, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try: raw = self.inbox.get(timeout=max(.001, deadline-time.monotonic()))
            except queue.Empty: break
            message = json.loads(raw)
            self.assertEqual(message.get('jsonrpc'), '2.0')
            if message.get('id') == identifier:
                return message
        self.fail(f'MCP response {identifier} was not received')

    def start_pending_tool(self):
        self.send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{
            'protocolVersion':'2025-06-18','capabilities':{},
            'clientInfo':{'name':'cancellation-test','version':'1'}}})
        self.assertIn('result', self.response(1))
        self.send({'jsonrpc':'2.0','method':'notifications/initialized'})
        self.send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{
            'name':'jev_decide','arguments':{'state':'Local cancellation fixture.',
                'questions':{f'q{i}':{'type':'noul','instructions':'Read the fixture.'} for i in range(73)}}}})
        # 73 questions require later batches. Neither initial stream completes,
        # so an extra request after cancellation is observable useless work.
        with self.changed:
            self.assertTrue(self.changed.wait_for(lambda: self.entered >= 2, timeout=5))
        self.assertEqual(self.entered, 2)
        self.assertEqual(self.readout_sizes, [24, 24])
        self.assertEqual(core._stats['active_calls'], 2)

    def assert_stopped_without_later_work(self, started):
        with self.changed:
            self.assertTrue(self.changed.wait_for(lambda: self.closed == 2, timeout=1.5))
        deadline = started + 1.5
        while core._stats['active_calls'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(core._stats['active_calls'], 0)
        self.assertLess(time.monotonic()-started, 1.5)
        # Leave a small observation window after cleanup to catch appended
        # batches/retries, rather than checking only the first closed socket.
        with self.changed:
            self.assertFalse(self.changed.wait_for(lambda: self.entered > 2, timeout=.2))
        self.assertEqual(core._stats['upstream_calls']-self.upstream_calls_before, 2)
        with self.backend.contexts_lock:
            self.assertEqual(len(self.backend.contexts), 0)

    def assert_jsonrpc_stdout(self):
        self.assertTrue(self.lines)
        for raw in self.lines:
            message = json.loads(raw)
            self.assertIsInstance(message, dict)
            self.assertEqual(message.get('jsonrpc'), '2.0')

    def test_explicit_cancel_closes_streams_releases_slots_and_keeps_stdio_usable(self):
        self.start_pending_tool()
        started = time.monotonic()
        self.send({'jsonrpc':'2.0','method':'notifications/cancelled',
                   'params':{'requestId':2,'reason':'Local regression cancellation'}})
        self.assert_stopped_without_later_work(started)
        self.assertIsNone(self.proc.poll())
        self.send({'jsonrpc':'2.0','id':3,'method':'tools/list','params':{}})
        response = self.response(3)
        self.assertIn('jev_decide', [tool['name'] for tool in response['result']['tools']])
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=2), 0)
        self.reader.join(1)
        self.assert_jsonrpc_stdout()

    def test_stdio_eof_closes_streams_releases_slots_and_exits(self):
        self.start_pending_tool()
        started = time.monotonic()
        self.proc.stdin.close()
        self.assert_stopped_without_later_work(started)
        self.assertEqual(self.proc.wait(timeout=2), 0)
        self.reader.join(1)
        self.assert_jsonrpc_stdout()


if __name__ == '__main__': unittest.main()
