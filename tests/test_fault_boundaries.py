"""Fault boundaries exercised with actual queues and OS pipes; no account changes."""
import io
import json
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from jev_luna import native_rpc as rpc, receipts, subscription_auth as auth
from jev_luna.lifecycle import RequestContext
from test_native_credentials import account, status, fake_session

class NativeFaultBoundaries(unittest.TestCase):
    def test_policy_change_cannot_reach_forced_refresh(self):
        calls=[]
        ac=account();ac['workspaceRouting']['accountRoutingOverride']='us'
        def peer(method,params,deadline):
            calls.append(params)
            return ac if method=='account/read' else status()
        expected=rpc.validate_pair(status(),account()).generation
        with self.assertRaises(rpc.NativeRpcError) as error:
            fake_session(peer).credential(time.monotonic()+1,force_refresh=True,
                                          expected_account='A',expected_generation=expected)
        self.assertEqual((error.exception.status,error.exception.code),(409,'native_policy_changed'))
        self.assertFalse(any(p.get('refreshToken') for p in calls))

    def test_rpc_queue_overflow_marks_channel_broken(self):
        session=rpc.Session.__new__(rpc.Session)
        session.proc=Mock();session.proc.stdout=io.BytesIO(b'{}\n'*(rpc.MAX_NOTICES+1))
        session.broken=threading.Event();session.inbox=queue.Queue(maxsize=rpc.MAX_NOTICES)
        session._pump()
        self.assertTrue(session.broken.is_set())
        self.assertEqual(session.inbox.qsize(),rpc.MAX_NOTICES)

    def test_real_full_pipe_write_obeys_manager_deadline(self):
        readfd,writefd=os.pipe();os.set_blocking(writefd,False)
        try:
            try:
                while True:os.write(writefd,b'x'*8192)
            except BlockingIOError:pass
            session=rpc.Session.__new__(rpc.Session);session.deadline=time.monotonic()+.05
            session.proc=Mock();session.proc.poll.return_value=None;session.proc.stdin.fileno.return_value=writefd
            before=time.monotonic()
            with self.assertRaises(rpc.NativeRpcError) as error:session._write({'method':'initialized'})
            self.assertEqual(error.exception.status,504);self.assertLess(time.monotonic()-before,.2)
        finally:os.close(readfd);os.close(writefd)

    def test_cancelled_waiter_detaches_while_shared_waiter_succeeds(self):
        entered,release=threading.Event(),threading.Event();answers=[];errors=[]
        first=RequestContext(time.monotonic()+2);second=RequestContext(time.monotonic()+2)
        def native(*a,**k):entered.set();release.wait(2);return 't','A',('native','g','NO_CONSTRAINT')
        def wait(ctx):
            try:answers.append(auth._credentials(2,context=ctx))
            except Exception as e:errors.append(e)
        with tempfile.TemporaryDirectory() as directory,patch.object(auth,'auth_file',return_value=Path(directory)/'auth.json'),patch.object(auth,'_native_login',side_effect=native) as lookup:
            workers=[threading.Thread(target=wait,args=(ctx,)) for ctx in (first,second)]
            for w in workers:w.start()
            self.assertTrue(entered.wait(1));time.sleep(.05);first.cancel();workers[0].join(.3)
            self.assertFalse(workers[0].is_alive());self.assertTrue(workers[1].is_alive())
            release.set();workers[1].join(1)
            self.assertFalse(workers[1].is_alive());self.assertEqual(len(answers),1)
            self.assertEqual(errors[0].status,499);self.assertEqual(lookup.call_count,1)

class DiagnosticFaultBoundaries(unittest.TestCase):
    def test_real_queue_overflow_and_shutdown_drain_never_certify_complete(self):
        self.assertTrue(receipts.flush_traces(2))
        entered,release=threading.Event(),threading.Event();original=receipts.AtomicReceipt._publish
        def slow(*a,**k):entered.set();release.wait(3);return original(*a,**k)
        # Exercise the real queue's overflow/drain paths, not disk throughput
        # from thousands of progressively larger durable snapshots.
        with tempfile.TemporaryDirectory() as directory,patch.object(receipts.AtomicReceipt,'_publish',side_effect=slow,autospec=True), \
             patch.object(receipts._TRACE_QUEUE,'maxsize',2):
            trace=receipts.RequestTrace(directory,'queue-full',{})
            try:
                self.assertTrue(entered.wait(1))
                for n in range(receipts._TRACE_QUEUE.maxsize+2):trace('metadata',{'n':n})
                trace('finished',{})
                self.assertGreater(trace.dropped,0);self.assertFalse(trace.flush(.03))
            finally:release.set();self.assertTrue(receipts.flush_traces(6))
            self.assertFalse(trace.flush())
            with self.assertRaises(ValueError):receipts.require_complete_trace(json.loads(trace.path.read_text()))

    def test_failed_write_cannot_certify_memory_or_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            trace=receipts.RequestTrace(directory,'write-failure',{})
            self.assertTrue(receipts.flush_traces(2))
            with patch.object(receipts.AtomicReceipt,'save',side_effect=OSError('fixture')):
                trace('finished',{});self.assertTrue(receipts.flush_traces(2))
            self.assertFalse(trace.flush())
            with self.assertRaises(ValueError):receipts.require_complete_trace(json.loads(trace.path.read_text()))
