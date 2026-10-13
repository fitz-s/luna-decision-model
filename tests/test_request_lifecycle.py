"""Adversarial admission, deadline, cancellation and partial-usage checks."""
import http.client
import json
import socket
import threading
import time
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from jev_luna import core as d
from jev_luna.errors import Reject
from jev_luna.lifecycle import RequestContext, current_context
from jev_luna.server import BoundedHTTPServer
from jev_luna.transport import Route

BODY = {'model':'gpt-6-luna','state':'x','questions':{'n':{'type':'noul','instructions':'x'}}}
KEY = 'jvl_local_'+('test'*12)


class LoopbackAdmission(unittest.TestCase):
    def setUp(self):
        self.srv=BoundedHTTPServer(('127.0.0.1',0),d.H,local_key=KEY,max_requests=2)
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.srv.cancel_requests();self.srv.shutdown();self.srv.server_close();self.thread.join(1)

    def post(self, *, headers=None, raw=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.srv.server_port,timeout=2)
        values={'Content-Type':'application/json','Authorization':'Bearer '+KEY, **(headers or {})}
        conn.request('POST','/v1/systemone',raw or json.dumps(BODY),values)
        response=conn.getresponse();status=response.status;body=json.loads(response.read());conn.close()
        return status,body

    def test_access_and_framing_rejections_never_start_inference(self):
        cases=[({'Authorization':'Bearer wrong'},401),({'Host':'evil.example'},403),
               ({'Origin':'https://evil.example'},403),({'Content-Type':'text/plain'},415),
               ({'Transfer-Encoding':'chunked'},422),({'Content-Length':str(d.MAX_BODY_BYTES+1)},413)]
        with patch.object(d,'upstream_call') as call:
            for headers, expected in cases:
                with self.subTest(headers=headers):
                    status,_=self.post(headers=headers);self.assertEqual(status,expected)
            call.assert_not_called()

    def test_nonloopback_is_rejected_before_binding(self):
        with self.assertRaises(ValueError):BoundedHTTPServer(('0.0.0.0',0),d.H,local_key=KEY)

    def test_admission_is_bounded_before_header_threads(self):
        sockets=[]
        try:
            for _ in range(2):
                conn=socket.create_connection(('127.0.0.1',self.srv.server_port),timeout=2)
                conn.sendall(b'POST /v1/systemone HTTP/1.1\r\nHost: ')
                sockets.append(conn)
            deadline=time.monotonic()+1
            while len(self.srv.contexts)<2 and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(len(self.srv.contexts),2)
            third=socket.create_connection(('127.0.0.1',self.srv.server_port),timeout=2)
            try:self.assertTrue(third.recv(1024).startswith(b'HTTP/1.1 503'))
            finally:third.close()
            self.assertEqual(len(self.srv.contexts),2)
        finally:
            for conn in sockets:conn.close()

    def test_slow_body_uses_original_caller_deadline_and_never_infers(self):
        raw=json.dumps(BODY).encode();conn=socket.create_connection(('127.0.0.1',self.srv.server_port),timeout=2)
        headers=f'POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1:{self.srv.server_port}\r\nAuthorization: Bearer {KEY}\r\nContent-Type: application/json\r\nX-Decide-D-Timeout-Ms: 100\r\nContent-Length: {len(raw)}\r\n\r\n'.encode()
        with patch.object(d,'upstream_call') as call:
            started=time.monotonic();conn.sendall(headers+raw[:1])
            try:
                while time.monotonic()-started < .35:
                    try:conn.sendall(raw[:1])
                    except OSError:break
                    time.sleep(.025)
                self.assertLess(time.monotonic()-started,.45)
                call.assert_not_called()
                with self.srv.contexts_lock:self.assertEqual(len(self.srv.contexts),0)
            finally:conn.close()

    def test_disconnect_cancels_stream_and_releases_inference_slot(self):
        entered,closed=threading.Event(),threading.Event()
        class Stream(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.flush()
                entered.set();self.connection.settimeout(2)
                try:
                    if self.connection.recv(1)==b'':closed.set()
                except OSError:pass
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Stream)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        conn=http.client.HTTPConnection('127.0.0.1',self.srv.server_port,timeout=2)
        try:
            with patch.object(d,'prepare_auth',return_value=Route(f'http://127.0.0.1:{upstream.server_port}/responses',{})):
                conn.request('POST','/v1/systemone',json.dumps(BODY),{'Authorization':'Bearer '+KEY,'Content-Type':'application/json','X-Decide-D-Timeout-Ms':'15000'})
                self.assertTrue(entered.wait(1));started=time.monotonic();conn.close()
                self.assertTrue(closed.wait(1));self.assertLess(time.monotonic()-started,.5)
                until=time.monotonic()+1
                while d._stats['active_calls'] and time.monotonic()<until:time.sleep(.01)
                self.assertEqual(d._stats['active_calls'],0)
        finally:conn.close();upstream.shutdown();upstream.server_close();thread.join(1)

    def test_native_node_abort_cancels_python_stream_and_releases_slot(self):
        entered,closed=threading.Event(),threading.Event()
        class Stream(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.flush()
                entered.set();self.connection.settimeout(2)
                try:
                    if self.connection.recv(1)==b'':closed.set()
                except OSError:pass
        upstream=ThreadingHTTPServer(('127.0.0.1',0),Stream)
        thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                key=Path(directory)/'service.key';key.write_text(KEY);key.chmod(0o600)
                preload=Path(__file__).resolve().parents[1]/'jev_luna/assets/jev-local.mjs'
                code='const start=performance.now();try { await fetch("https://api.typesafe.ai/v1/systemone",{method:"POST",headers:{"Content-Type":"application/json"},body:process.env.TEST_BODY,signal:AbortSignal.timeout(200)});process.exit(2); } catch(e) { console.log(JSON.stringify({name:e.name,milliseconds:performance.now()-start})); }'
                env={**os.environ,'JEV_LUNA_HOME':directory,'DECIDE_D_URL':f'http://127.0.0.1:{self.srv.server_port}/v1/systemone','TEST_BODY':json.dumps(BODY)}
                with patch.object(d,'prepare_auth',return_value=Route(f'http://127.0.0.1:{upstream.server_port}/responses',{})):
                    result=subprocess.run(['node','--import',str(preload),'-e',code],env=env,capture_output=True,text=True,timeout=4,check=True)
                    self.assertTrue(entered.is_set());self.assertTrue(closed.wait(1))
                    receipt=json.loads(result.stdout);self.assertEqual(receipt['name'],'TimeoutError');self.assertLess(receipt['milliseconds'],800)
                    until=time.monotonic()+1
                    while d._stats['active_calls'] and time.monotonic()<until:time.sleep(.01)
                    self.assertEqual(d._stats['active_calls'],0)
        finally:upstream.shutdown();upstream.server_close();thread.join(1)


class BatchCancellation(unittest.TestCase):
    def test_first_batch_failure_stops_sibling_and_all_later_batches(self):
        body={'model':'gpt-6-luna','state':'same evidence','questions':{f'n{i}':{'type':'noul','instructions':f'predicate {i}'} for i in range(73)}}
        state,questions=d.parse_systemone(body)
        started=threading.Barrier(2);calls=[]
        def run(payload, timeout):
            context=current_context.get(); text=payload['input'][-1]['content'][0]['text']; calls.append(text)
            started.wait(1)
            if 'predicate 0"' in text:
                error=Reject(400,'unsupported_capability','permanent request error')
                error.usage={'input_tokens':7,'output_tokens':1}
                raise error
            self.assertTrue(context.cancelled.wait(1))
            raise context.reason
        def prepare(context):return Route('http://127.0.0.1:1',{})
        with patch.object(d,'prepare_auth',side_effect=prepare),patch.object(d,'upstream_call',side_effect=run):
            started_at=time.monotonic()
            with self.assertRaises(Reject) as raised:d._infer(state,questions,time.monotonic()+2)
        self.assertLess(time.monotonic()-started_at,.5)
        self.assertEqual(raised.exception.code,'unsupported_capability')
        self.assertEqual(len(calls),2)
        accounting=raised.exception.accounting
        self.assertEqual(accounting['observed_usage']['input_tokens'],7)
        self.assertEqual(accounting['observed_usage']['total_tokens'],8)
        self.assertEqual(accounting['usage_status'],'partial')
        self.assertEqual(accounting['unknown_usage_attempts'],1)
        self.assertEqual(d._stats['active_calls'],0)
