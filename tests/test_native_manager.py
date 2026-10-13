"""Native owner routing, strict grants, stable snapshots and bounded credential lookups."""
import io
import json
import os
import queue
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
        proc.stdout=os.fdopen(output_read,'rb')  # buffered like Popen's default; unbuffered readline() is one syscall per byte, so a 1 MiB frame alone nearly exhausted the 1 s budget
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
                auth._drop_session()  # lookups share one live native process; retire it to check cleanup
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


class SharedNativeSession(unittest.TestCase):
    """One live app-server serves successive lookups while the native store is unchanged."""
    def setUp(self):
        auth._drop_session()
        self.spawned=[];self.state=('home',(1,10,100),None)
        test=self
        class Fake:
            def __init__(self,deadline):
                self.closed=False;self.broken=threading.Event();self.proc=MagicMock();self.proc.poll.return_value=None
                self.inbox=queue.Queue();self.started=time.monotonic();self.fail=None;self.calls=0;self.during=None
                test.spawned.append(self)
            def credential(self,deadline,**kwargs):
                self.calls+=1
                if self.fail:raise self.fail
                if self.during:self.during();self.during=None
                return native.Credential(token(),'native-workspace','https://chatgpt.com')
            def close(self):self.closed=True
        self.patches=[patch.object(native,'Session',Fake),patch.object(auth,'_store_state',lambda:self.state)]
        for p in self.patches:p.start()
    def tearDown(self):
        auth._drop_session()
        for p in self.patches:p.stop()

    def test_successive_lookups_reuse_one_process(self):
        for _ in range(3):self.assertEqual(auth._native_login(1)[1],'native-workspace')
        self.assertEqual(len(self.spawned),1);self.assertEqual(self.spawned[0].calls,3)
        self.assertFalse(self.spawned[0].closed)

    def test_broken_or_aged_process_is_replaced(self):
        auth._native_login(1);self.spawned[0].broken.set()
        auth._native_login(1);self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)
        self.spawned[1].started-=auth.SESSION_MAX_AGE+1
        auth._native_login(1);self.assertEqual(len(self.spawned),3);self.assertTrue(self.spawned[1].closed)

    def test_store_change_login_logout_rotation_or_config_retires_process(self):
        auth._native_login(1)
        for changed in (('home',(1,11,100),None),('home',(2,11,100),None),('home',(2,11,100),(5,1,1)),('other',(2,11,100),(5,1,1))):
            with self.subTest(state=changed):
                self.state=changed;n=len(self.spawned)
                auth._native_login(1)
                self.assertEqual(len(self.spawned),n+1);self.assertTrue(self.spawned[-2].closed)

    def test_store_change_during_reused_read_is_answered_by_fresh_process(self):
        auth._native_login(1)
        def rotate():self.state=('home',(1,99,100),None)
        self.spawned[0].during=rotate
        auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)
        self.assertEqual(self.spawned[1].calls,1);self.assertIs(auth._session,self.spawned[1])

    def test_unobservable_store_uses_one_process_per_lookup(self):
        self.state=None
        auth._native_login(1);auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(all(s.closed for s in self.spawned))
        self.assertIsNone(auth._session)

    def test_unsolicited_idle_message_retires_process(self):
        auth._native_login(1);self.spawned[0].inbox.put(b'{"method":"account/updated"}\n')
        auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)

    def test_reused_process_fault_retries_once_on_fresh_process(self):
        auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_auth_channel','ended',502)
        self.assertEqual(auth._native_login(1)[1],'native-workspace')
        self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)

    def test_fresh_process_fault_is_not_retried(self):
        original=native.Session
        def failing(deadline):
            session=original(deadline);session.fail=native.NativeRpcError('native_login_required','absent');return session
        with patch.object(native,'Session',side_effect=failing),self.assertRaises(auth.CodexLoginRequired):auth._native_login(1)
        self.assertEqual(len(self.spawned),1);self.assertTrue(self.spawned[0].closed);self.assertIsNone(auth._session)

    def test_refresh_uses_own_process_and_retires_shared(self):
        auth._native_login(1);shared=self.spawned[0]
        auth._native_login(1,refresh=True)
        self.assertEqual(len(self.spawned),2);self.assertTrue(shared.closed and self.spawned[1].closed)
        self.assertIsNone(auth._session)


class NativeStoreState(unittest.TestCase):
    def test_stat_only_identity_and_unobservable_store(self):
        import builtins, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ,{'CODEX_HOME':home}):
            self.assertIsNone(auth._store_state())  # no auth.json: keyring or absent login
            Path(home,'auth.json').write_text('{}')
            with patch.object(builtins,'open',side_effect=AssertionError('contents must not be read')):
                first=auth._store_state()
            self.assertEqual(first[0],str(Path(home).resolve()));self.assertIsNone(first[2])
            Path(home,'config.toml').write_text('')
            second=auth._store_state();self.assertNotEqual(first,second)
            os.utime(Path(home,'auth.json'),ns=(1,1))
            self.assertNotEqual(second,auth._store_state())
            with patch.object(auth.os,'stat',side_effect=PermissionError):self.assertIsNone(auth._store_state())
