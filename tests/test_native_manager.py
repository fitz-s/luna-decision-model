"""Native owner routing, strict grants, stable snapshots and bounded credential lookups."""
import io
import json
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from jev_luna import subscription_auth as auth, native_rpc as native

STATUS={'authMethod':'chatgpt','authToken':'opaque-native-access','requiresOpenaiAuth':True}
ACCOUNT={'account':{'type':'chatgpt'},'requiresOpenaiAuth':True,'workspaceRouting':{'chatgptAccountId':'native-workspace','backendOrigin':'https://chatgpt.com','accountRoutingOverride':'NO_CONSTRAINT'}}

def token():return 'opaque-native-access'

class NativeManager(unittest.TestCase):
    def invoke(self, results, timeout=1, bad_frame=None):
        # Real pipes: the fake native peer answers only after receiving each RPC.
        # Preloading BytesIO replies cannot exercise bounded nonblocking writes.
        input_read,input_write=os.pipe();output_read,output_write=os.pipe()
        proc=MagicMock();proc.poll.return_value=None
        proc.stdin=os.fdopen(input_write,'wb',buffering=0)
        proc.stdout=os.fdopen(output_read,'rb',buffering=0)
        peer_in=os.fdopen(input_read,'rb',buffering=0)
        peer_out=os.fdopen(output_write,'wb',buffering=0)
        sent=[]
        def terminate():
            proc.poll.return_value=0
            peer_out.close()
        proc.terminate.side_effect=terminate;proc.kill.side_effect=terminate
        def peer():
            replies=iter(results)
            try:
                for raw in iter(peer_in.readline,b''):
                    message=json.loads(raw);sent.append(message)
                    if 'id' in message:
                        reply=bad_frame if bad_frame is not None else json.dumps({'id':message['id'],'result':next(replies)}).encode()+b'\n'
                        peer_out.write(reply)
            except (OSError,ValueError,StopIteration):pass
            finally:peer_in.close()
        reader=threading.Thread(target=peer,daemon=True);reader.start()
        with patch.object(native.subprocess,'Popen',return_value=proc) as launch,patch.object(native.shutil,'which',return_value='/fixture/codex'),patch.dict(os.environ,{'CODEX_HOME':'/fixture/native-codex','OPENAI_API_KEY':'fixture-paid-key'}):
            try:return auth._native_login(timeout)
            finally:
                self.assertEqual(launch.call_args.args[0],['/fixture/codex','-c','model_provider="openai"','app-server'])
                self.assertEqual(launch.call_args.kwargs['env']['CODEX_HOME'],'/fixture/native-codex')
                self.assertNotIn('OPENAI_API_KEY',launch.call_args.kwargs['env'])
                self.assertTrue(proc.stdin.closed and proc.stdout.closed)
                reader.join(1);self.assertFalse(reader.is_alive())
                self.assertTrue(all(x['method'] in ('initialize','initialized','getAuthStatus','account/read') for x in sent))
                if sent:
                    self.assertEqual(sent[0]['params']['clientInfo']['version'], native.__version__)
                    self.assertTrue(sent[0]['params']['capabilities']['experimentalApi'])
                self.sent=sent

    def results(self):return [{},{**STATUS,'authToken':None},STATUS,ACCOUNT,STATUS,ACCOUNT]

    def test_account_is_authoritative_native_routing_not_jwt(self):
        access,account,generation=self.invoke(self.results())
        self.assertEqual((access,account),('opaque-native-access','native-workspace'))
        self.assertEqual(generation[0],'native');self.assertNotIn(access,generation)
        self.assertEqual(self.sent[2]['params']['includeToken'],False)

    def test_missing_login_and_api_key_mode_fail_before_exporting_token(self):
        with self.assertRaises(auth.NativeAuthStateError) as absent:self.invoke([{}, {'authMethod':None,'requiresOpenaiAuth':True},ACCOUNT])
        self.assertEqual(absent.exception.code,'native_login_unavailable')
        with self.assertRaises(auth.NativeAuthStateError):self.invoke([{}, {'authMethod':'apikey','requiresOpenaiAuth':True}])
        self.assertFalse(any(x.get('params',{}).get('includeToken') for x in self.sent))

    def test_managed_mode_and_routing_required(self):
        bad_accounts=[{**ACCOUNT,'workspaceRouting':None},{**ACCOUNT,'workspaceRouting':{**ACCOUNT['workspaceRouting'],'backendOrigin':'https://untrusted.invalid'}},{**ACCOUNT,'workspaceRouting':{**ACCOUNT['workspaceRouting'],'accountRoutingOverride':'unsupported'}}]
        for account in bad_accounts:
            with self.subTest(routing=bool(account['workspaceRouting'])),self.assertRaises(native.NativeRpcError):native.validate_pair(STATUS,account)
        for mode in ('apikey','chatgptAuthTokens','agentIdentity',None):
            with self.subTest(mode=mode),self.assertRaises(native.NativeRpcError):native.validate_pair({**STATUS,'authMethod':mode},ACCOUNT)
        with self.assertRaises(native.NativeRpcError):native.validate_pair({**STATUS,'authToken':'bad\r\nheader'},ACCOUNT)

    def test_account_snapshot_change_is_named_409(self):
        session=native.Session.__new__(native.Session);session.account_epoch=0
        other={**ACCOUNT,'workspaceRouting':{**ACCOUNT['workspaceRouting'],'chatgptAccountId':'changed'}}
        responses=[{**STATUS,'authToken':None}]+[STATUS,ACCOUNT,STATUS,other]*2
        with patch.object(session,'rpc',side_effect=responses),self.assertRaises(native.NativeRpcError) as error:session.credential(time.monotonic()+1)
        self.assertEqual((error.exception.code,error.exception.status),('native_account_changed',409))

    def test_no_turn_login_logout_or_execution_methods(self):
        session=native.Session.__new__(native.Session)
        for method in ('thread/start','turn/start','account/login/start','account/logout','command/exec'):
            with self.assertRaises(native.NativeRpcError):session.rpc(method,{},time.monotonic()+1)

    def test_expired_wait_never_accepts_buffered_success(self):
        with self.assertRaises(auth.NativeAuthStateError):self.invoke(self.results(),timeout=-.01)

    def test_oversized_stdio_frame_is_bounded_and_closed(self):
        before=time.monotonic()
        with self.assertRaises(auth.NativeAuthStateError) as error:
            self.invoke([],bad_frame=b'x'*(native.MAX_LINE+1))
        self.assertEqual(error.exception.code,'native_auth_channel')
        self.assertLess(time.monotonic()-before,2)

class NativeLookup(unittest.TestCase):
    def test_auth_wait_keeps_callers_deadline_status(self):
        from jev_luna.transport import prepare
        from jev_luna.lifecycle import RequestContext
        from jev_luna.errors import Reject
        context=RequestContext(time.monotonic()+.01)
        def delayed(timeout, **kwargs):
            time.sleep(.03)
            raise auth.NativeAuthStateError('Auth deadline expired')
        with patch.object(auth,'request_parameters',side_effect=delayed),self.assertRaises(Reject) as error:
            prepare({'transport':'subscription'},context)
        self.assertEqual(error.exception.code,'deadline')

    def test_concurrent_lookup_shared_but_later_account_switch_is_fresh(self):
        started,finish=threading.Event(),threading.Event();answers=[];calls=[]
        def native(timeout):
            calls.append(timeout);started.set();self.assertTrue(finish.wait(2));return token(), 'account-one', ('native','one')
        with patch.object(auth,'_native_login',side_effect=native):
            threads=[threading.Thread(target=lambda:answers.append(auth._credentials(2))) for _ in range(4)]
            for t in threads:t.start()
            self.assertTrue(started.wait(1));time.sleep(.05);finish.set()
            for t in threads:t.join(2);self.assertFalse(t.is_alive())
        self.assertEqual(len(calls),1);self.assertEqual(len(answers),4)
        with patch.object(auth,'_native_login',return_value=(token(),'account-two',('native','two'))) as fresh:
            self.assertEqual(auth._credentials(1)[1],'account-two')
        fresh.assert_called_once()

    def test_caller_deadline_does_not_interrupt_native_rotation(self):
        started,finish=threading.Event(),threading.Event()
        def native(timeout):started.set();finish.wait(2);return token(),'account',('native','new')
        with patch.object(auth,'_native_login',side_effect=native):
            before=time.monotonic()
            with self.assertRaises(auth.NativeAuthStateError):auth._credentials(.05)
            self.assertTrue(started.is_set());self.assertLess(time.monotonic()-before,.2)
            with auth._lookup_lock:flight=next(iter(auth._lookups.values()))
            finish.set();self.assertTrue(flight['event'].wait(1))
        self.assertEqual(auth._lookups,{})
