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
            self.launch=launch
            try:return auth._native_login(timeout)
            finally:
                auth._drop_session()  # lookups share one live native process; retire it to check cleanup
                if launch.called:
                    self.assertEqual(launch.call_args.args[0],['/fixture/codex','-c','model_provider="openai"','app-server'])
                    self.assertEqual(launch.call_args.kwargs['env']['CODEX_HOME'],'/fixture/native-codex')
                    self.assertNotIn('OPENAI_API_KEY',launch.call_args.kwargs['env'])
                    self.assertTrue(proc.stdin.closed and proc.stdout.closed)
                    reader.join(1);self.assertFalse(reader.is_alive())
                    self.assertTrue(all(x['method'] in ('initialize','initialized','getAuthStatus','account/read') for x in sent))
                    if sent:
                        self.assertEqual(sent[0]['params']['clientInfo']['version'], native.__version__)
                        self.assertTrue(sent[0]['params']['capabilities']['experimentalApi'])
                else:
                    for stream in (proc.stdin,proc.stdout,peer_out):stream.close()
                    reader.join(1)
                self.sent=sent

    def results(self):return [{},{**STATUS,'authToken':None},STATUS,ACCOUNT,STATUS,ACCOUNT]

    def test_account_is_authoritative_native_routing_not_jwt(self):
        access,account,generation=self.invoke(self.results())
        self.assertEqual((access,account),('opaque-native-access','native-workspace'))
        self.assertEqual(generation[0],'native');self.assertNotIn(access,generation)
        self.assertEqual(self.sent[2]['params']['includeToken'],False)

    def test_sub_timings_are_recorded_for_spawn_initialize_and_each_rpc(self):
        self.invoke(self.results())
        self.assertEqual(set(native.last_timings),{'spawn','initialize','getAuthStatus','account/read'})
        self.assertTrue(all(isinstance(v,float) and v>=0 for v in native.last_timings.values()))

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
        with self.assertRaises(auth.NativeAuthStateError) as error:self.invoke(self.results(),timeout=-.01)
        self.assertEqual((error.exception.code,error.exception.status),('native_auth_deadline',504))
        self.launch.assert_not_called()  # an expired budget never spawns a native process

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
        self.spawned=[];self.state=('exe','home',(1,10,100),None);self.owner='native-workspace';self.policy='NO_CONSTRAINT'
        self.on_read=None;self.after_read=None
        test=self
        class Fake:
            def __init__(self,deadline):
                self.closed=False;self.broken=threading.Event();self.proc=MagicMock();self.proc.poll.return_value=None
                self.inbox=queue.Queue();self.started=time.monotonic();self.fail=None;self.calls=0;self.during=None;self.kwargs=[]
                test.spawned.append(self)
            def credential(self,deadline,**kwargs):
                self.calls+=1;self.kwargs.append(kwargs)
                if self.fail:raise self.fail
                if self.during:self.during();self.during=None
                if test.on_read:test.on_read()
                expected=kwargs.get('expected_account')
                if expected is not None and expected!=test.owner:
                    raise native.NativeRpcError('native_account_changed','changed',409)
                value=native.Credential(token(),test.owner,'https://chatgpt.com',test.policy)
                if test.after_read:hook,test.after_read=test.after_read,None;hook()  # the store moves after this read answered
                return value
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
        for changed in (('exe','home',(1,11,100),None),('exe','home',(2,11,100),None),('exe','home',(2,11,100),(5,1,1)),
                        ('exe','other',(2,11,100),(5,1,1)),('exe2','other',(2,11,100),(5,1,1))):
            with self.subTest(state=changed):
                self.state=changed;n=len(self.spawned)
                auth._native_login(1)
                self.assertEqual(len(self.spawned),n+1);self.assertTrue(self.spawned[-2].closed)

    def test_store_change_during_reused_read_is_answered_by_fresh_process(self):
        auth._native_login(1)
        def rotate():self.state=('exe','home',(1,99,100),None)
        self.spawned[0].during=rotate
        auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)
        self.assertEqual(self.spawned[1].calls,1);self.assertIs(auth._session,self.spawned[1])

    def test_store_change_during_fresh_lookup_cannot_switch_owner(self):
        # Owner A is observed by a fresh process while the store switches to B: neither is published.
        self.owner='account-a'
        def switch():self.state=('exe','home',(7,20,200),None);self.owner='account-b'
        self.after_read=switch
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
        self.assertEqual((error.exception.code,error.exception.status),('native_account_changed',409))
        self.assertEqual(len(self.spawned),2);self.assertTrue(all(s.closed for s in self.spawned))
        self.assertEqual(self.spawned[1].kwargs[-1]['expected_account'],'account-a')
        self.assertIsNone(auth._session)

    def test_store_change_during_reused_read_cannot_switch_owner(self):
        auth._native_login(1)
        def switch():self.state=('exe','home',(7,20,200),None);self.owner='account-b';self.on_read=None
        self.on_read=switch
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
        self.assertEqual((error.exception.code,error.exception.status),('native_account_changed',409))
        self.assertTrue(all(s.closed for s in self.spawned));self.assertIsNone(auth._session)

    def test_store_change_during_lookup_cannot_switch_routing(self):
        for fresh in (True,False):
            with self.subTest(fresh=fresh):
                auth._drop_session();self.spawned.clear();self.policy='NO_CONSTRAINT';self.state=('exe','home',(1,10,100),None)
                if not fresh:auth._native_login(1)
                def switch():self.state=('exe','home',(8,30,300),None);self.policy='us'
                if fresh:self.after_read=switch  # A/NO_CONSTRAINT answered, then the store and routing moved
                else:self.on_read=switch
                with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
                self.assertEqual((error.exception.code,error.exception.status),('native_policy_changed',409))
                self.assertTrue(all(s.closed for s in self.spawned));self.assertIsNone(auth._session)
                self.on_read=None

    def test_store_change_during_lookup_allows_same_owner_rotation(self):
        for fresh in (True,False):
            with self.subTest(fresh=fresh):
                auth._drop_session();self.spawned.clear();self.state=('exe','home',(1,10,100),None)
                if not fresh:auth._native_login(1)
                def rotate():self.state=('exe','home',(9,40,400),None);self.on_read=None
                self.on_read=rotate
                self.assertEqual(auth._native_login(1)[1],'native-workspace')
                self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)
                self.assertEqual(self.spawned[1].kwargs[-1]['expected_account'],'native-workspace')
                self.assertIs(auth._session,self.spawned[1]);self.assertEqual(self.spawned[1].store_state,self.state)

    def test_store_churn_fails_closed_without_publishing(self):
        churn=iter(range(100))
        def bump():self.state=('exe','home',(1,next(churn),100),None)
        self.on_read=bump
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
        self.assertEqual((error.exception.code,error.exception.status),('native_account_changed',409))
        self.assertEqual(len(self.spawned),auth.STORE_REBUILDS+1)
        self.assertTrue(all(s.closed for s in self.spawned));self.assertIsNone(auth._session)

    def test_unobservable_store_uses_one_process_per_lookup(self):
        self.state=None
        auth._native_login(1);auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(all(s.closed for s in self.spawned))
        self.assertIsNone(auth._session)

    def test_unsolicited_idle_message_retires_process(self):
        auth._native_login(1);self.spawned[0].inbox.put(b'{"method":"account/updated"}\n')
        auth._native_login(1)
        self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)

    def test_retry_allowlist_is_channel_failures_only(self):
        self.assertEqual(auth.REUSE_RETRY,{'native_auth_channel','native_auth_protocol'})
        for code in sorted(auth.REUSE_RETRY):
            with self.subTest(code=code):
                auth._drop_session();self.spawned.clear()
                auth._native_login(1);self.spawned[0].fail=native.NativeRpcError(code,'ended',502)
                self.assertEqual(auth._native_login(1)[1],'native-workspace')
                self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[0].closed)
                # The replacement is bound to the owner the reused process had established.
                self.assertEqual(self.spawned[1].kwargs[-1]['expected_account'],'native-workspace')
                self.assertIs(auth._session,self.spawned[1])

    def test_reused_account_change_is_not_retried(self):
        auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_account_changed','changed',409)
        self.owner='account-b'  # a fresh process would now answer for someone else
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
        self.assertEqual((error.exception.code,error.exception.status),('native_account_changed',409))
        self.assertEqual(len(self.spawned),1);self.assertTrue(self.spawned[0].closed);self.assertIsNone(auth._session)

    def test_reused_policy_change_is_not_retried(self):
        auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_policy_changed','changed',409)
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
        self.assertEqual((error.exception.code,error.exception.status),('native_policy_changed',409))
        self.assertEqual(len(self.spawned),1);self.assertIsNone(auth._session)

    def test_permanent_reused_errors_are_not_retried(self):
        for code,status in (('native_managed_login',401),('native_token_unavailable',401),('native_routing_capability',422),
                            ('native_regional_route',422),('native_auth_rpc',422),('native_auth_profile',422),
                            ('native_login_unavailable',422),('native_auth_deadline',504)):
            with self.subTest(code=code):
                auth._drop_session();self.spawned.clear()
                auth._native_login(1);self.spawned[0].fail=native.NativeRpcError(code,'x',status)
                with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
                self.assertEqual(error.exception.code,code);self.assertEqual(len(self.spawned),1)
        auth._drop_session();self.spawned.clear()
        auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_login_required','absent')
        with self.assertRaises(auth.CodexLoginRequired):auth._native_login(1)
        self.assertEqual(len(self.spawned),1)

    def test_retry_to_a_different_owner_or_policy_fails_closed(self):
        for change in ('owner','policy'):
            with self.subTest(change=change):
                auth._drop_session();self.spawned.clear();self.owner='native-workspace';self.policy='NO_CONSTRAINT'
                auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_auth_channel','ended',502)
                if change=='owner':self.owner='account-b'
                else:self.policy='us'
                with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(1)
                self.assertEqual(error.exception.status,409)
                self.assertEqual(error.exception.code,'native_account_changed' if change=='owner' else 'native_policy_changed')
                self.assertEqual(len(self.spawned),2);self.assertTrue(self.spawned[1].closed);self.assertIsNone(auth._session)

    def test_reuse_retry_happens_once(self):
        auth._native_login(1);self.spawned[0].fail=native.NativeRpcError('native_auth_channel','ended',502)
        original=native.Session
        def failing(deadline):
            session=original(deadline);session.fail=native.NativeRpcError('native_auth_channel','ended',502);return session
        with patch.object(native,'Session',side_effect=failing),self.assertRaises(auth.NativeAuthStateError) as error:
            auth._native_login(1)
        self.assertEqual(error.exception.code,'native_auth_channel');self.assertEqual(len(self.spawned),2)

    def test_fresh_process_fault_is_not_retried(self):
        original=native.Session
        def failing(deadline):
            session=original(deadline);session.fail=native.NativeRpcError('native_auth_channel','ended',502);return session
        with patch.object(native,'Session',side_effect=failing),self.assertRaises(auth.NativeAuthStateError):auth._native_login(1)
        self.assertEqual(len(self.spawned),1);self.assertTrue(self.spawned[0].closed);self.assertIsNone(auth._session)

    def test_lock_wait_honors_deadline_and_spawns_nothing_after_expiry(self):
        entered,release=threading.Event(),threading.Event()
        def block():entered.set();self.assertTrue(release.wait(2))
        self.on_read=block
        first=threading.Thread(target=auth._native_login,args=(3,),daemon=True);first.start()
        self.assertTrue(entered.wait(1));self.on_read=None
        before=time.monotonic()
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(.1)
        self.assertEqual((error.exception.code,error.exception.status),('native_auth_deadline',504))
        self.assertLess(time.monotonic()-before,.5);self.assertEqual(len(self.spawned),1)
        release.set();first.join(2);self.assertFalse(first.is_alive())
        self.assertEqual(len(self.spawned),1)

    def test_stuck_close_does_not_outlive_deadline(self):
        auth._native_login(1);stuck=self.spawned[0];stuck.broken.set()
        gate=threading.Event();stuck.close=lambda:gate.wait(2)
        before=time.monotonic()
        with self.assertRaises(auth.NativeAuthStateError) as error:auth._native_login(.2)
        self.assertEqual(error.exception.code,'native_auth_deadline')
        self.assertLess(time.monotonic()-before,.6);self.assertEqual(len(self.spawned),1)
        gate.set()

    def test_refresh_uses_own_process_and_retires_shared(self):
        auth._native_login(1);shared=self.spawned[0]
        auth._native_login(1,refresh=True)
        self.assertEqual(len(self.spawned),2);self.assertTrue(shared.closed and self.spawned[1].closed)
        self.assertIsNone(auth._session)


class NativeStoreState(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name)/'codex';self.home.mkdir()
        self.bin=Path(self.tmp.name)/'codex-bin';self.bin.write_text('');self.etc=Path(self.tmp.name)/'etc'
        auth._layer_modes.clear()
        self.patches=[patch.dict(os.environ,{'CODEX_HOME':str(self.home),'LDM_CODEX_BIN':str(self.bin)}),
                      patch.object(auth,'SYSTEM_LAYERS',(self.etc/'config.toml',self.etc/'requirements.toml')),
                      patch.object(auth,'_managed_preferences',lambda:(self.etc/'managed.plist',))]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in self.patches:p.stop()
        auth._layer_modes.clear();self.tmp.cleanup()

    def test_stat_only_identity_and_unobservable_store(self):
        import builtins
        from pathlib import Path
        self.assertIsNone(auth._store_state())  # no auth.json: keyring or absent login
        (self.home/'auth.json').write_text('{}')
        real=builtins.open
        def guard(path,*a,**k):
            if Path(path).name=='auth.json':raise AssertionError('auth.json contents must not be read')
            return real(path,*a,**k)
        with patch.object(builtins,'open',guard):
            first=auth._store_state()
            self.assertEqual(first[:2],(str(self.bin.resolve()),str(self.home.resolve())));self.assertIsNone(first[4])
            (self.home/'config.toml').write_text('model = "x"\n')
            second=auth._store_state();self.assertNotEqual(first,second)
            os.utime(self.home/'auth.json',ns=(1,1))
            self.assertNotEqual(second,auth._store_state())
        with patch.object(auth.os,'stat',side_effect=PermissionError):self.assertIsNone(auth._store_state())

    def test_keyring_or_auto_store_disables_reuse_despite_leftover_auth_json(self):
        (self.home/'auth.json').write_text('{}')
        for text in ('cli_auth_credentials_store = "keyring"\n','cli_auth_credentials_store = "auto"\n',
                     'cli_auth_credentials_store = "ephemeral"\n','model = "x"\ncli_auth_credentials_store = \'auto\' # comment\n',
                     '[profiles.work]\ncli_auth_credentials_store = "keyring"\n','"cli_auth_credentials_store" = "keyring"\n',
                     '"cli_auth_credentials_stor\\u0065" = "keyring"\n','cli_auth_credentials_store = [\n'):
            for parser in ('tomllib','fallback'):
                with self.subTest(config=text,parser=parser),patch.object(auth,'tomllib',auth.tomllib if parser=='tomllib' else None):
                    if parser=='tomllib' and auth.tomllib is None:continue
                    (self.home/'config.toml').write_text(text);os.utime(self.home/'config.toml',ns=(len(text),hash(text)%10**9))
                    auth._layer_modes.clear()
                    self.assertIsNone(auth._store_state())
        (self.home/'config.toml').write_bytes(b'\xff\n');auth._layer_modes.clear()
        self.assertIsNone(auth._store_state())  # undecodable config is a parse doubt

    def test_file_store_explicit_or_default_allows_reuse(self):
        (self.home/'auth.json').write_text('{}')
        for text in ('','model = "x"\n','cli_auth_credentials_store = "file"\n[mcp_servers.a]\ncommand = "x"\n',
                     'developer_instructions = """\nkey = value\n"""\n'):
            for parser in ('tomllib','fallback'):
                with self.subTest(config=text,parser=parser),patch.object(auth,'tomllib',auth.tomllib if parser=='tomllib' else None):
                    if parser=='tomllib' and auth.tomllib is None:continue
                    (self.home/'config.toml').write_text(text);auth._layer_modes.clear()
                    self.assertIsNotNone(auth._store_state())

    def test_system_layer_or_managed_preferences_can_disable_reuse(self):
        (self.home/'auth.json').write_text('{}');self.etc.mkdir()
        self.assertIsNotNone(auth._store_state())
        (self.etc/'requirements.toml').write_text('cli_auth_credentials_store = "keyring"\n')
        self.assertIsNone(auth._store_state())
        (self.etc/'requirements.toml').unlink();(self.etc/'managed.plist').write_text('')
        self.assertIsNone(auth._store_state())

    def test_unreadable_managed_preference_disables_reuse_only_missing_is_absent(self):
        (self.home/'auth.json').write_text('{}')
        plist=self.etc/'managed.plist'
        self.assertIsNotNone(auth._store_state())  # parent missing: absent
        self.etc.mkdir();self.assertIsNotNone(auth._store_state())  # file missing: absent
        (self.etc/'file').write_text('');absent_under_file=self.etc/'file'/'managed.plist'
        with patch.object(auth,'_managed_preferences',lambda:(absent_under_file,)):
            self.assertIsNotNone(auth._store_state())  # NotADirectoryError: missing parent
        real=os.lstat
        def denied(path,*a,**k):
            if str(path)==str(plist):raise PermissionError('inspection denied')
            return real(path,*a,**k)
        with patch.object(os,'lstat',denied):
            self.assertIsNone(auth._store_state())  # not evidence of absence
        plist.write_text('');self.assertIsNone(auth._store_state())

    def test_special_config_layer_never_blocks_or_holds_the_session_lock(self):
        import threading as th
        (self.home/'auth.json').write_text('{}')
        fifo=self.home/'config.toml';os.mkfifo(fifo,0o600)
        started=time.monotonic()
        self.assertIsNone(auth._store_state())  # a FIFO is not a regular config file; no writer, still returns
        self.assertLess(time.monotonic()-started,.5)
        # Through the broker: the budget is honoured and the session lock is released.
        done=th.Event()
        def run():
            try:auth._native_login(.05)
            except Exception:pass
            finally:done.set()
        spawned=[]
        class Fake:
            def __init__(self,deadline):
                self.closed=False;self.broken=th.Event();self.proc=MagicMock();self.proc.poll.return_value=None
                self.inbox=queue.Queue();self.started=time.monotonic();spawned.append(self)
            def credential(self,deadline,**kwargs):return native.Credential(token(),'native-workspace','https://chatgpt.com')
            def close(self):self.closed=True
        with patch.object(native,'Session',Fake):
            th.Thread(target=run,daemon=True).start()
            self.assertTrue(done.wait(1));self.assertFalse(auth._session_lock.locked())
        self.assertTrue(all(sp.closed for sp in spawned));self.assertIsNone(auth._session)
        fifo.unlink();(self.home/'target').mkdir()
        os.symlink(self.home/'target',fifo)
        self.assertIsNone(auth._store_state())  # directory behind a symlink: not a regular file
        fifo.unlink();(self.home/'real.toml').write_text('model = "x"\n');os.symlink(self.home/'real.toml',fifo)
        self.assertIsNotNone(auth._store_state())  # symlinked regular file is followed, as Codex does

    def test_codex_binary_or_home_change_retires_process(self):
        (self.home/'auth.json').write_text('{}')
        spawned=[]
        class Fake:
            def __init__(self,deadline):
                self.closed=False;self.broken=threading.Event();self.proc=MagicMock();self.proc.poll.return_value=None
                self.inbox=queue.Queue();self.started=time.monotonic();spawned.append(self)
            def credential(self,deadline,**kwargs):return native.Credential(token(),'native-workspace','https://chatgpt.com')
            def close(self):self.closed=True
        other=self.bin.with_name('codex-other');other.write_text('')
        other_home=self.home.with_name('codex-other-home');other_home.mkdir();(other_home/'auth.json').write_text('{}')
        try:
            with patch.object(native,'Session',Fake):
                auth._native_login(1);auth._native_login(1);self.assertEqual(len(spawned),1)
                with patch.dict(os.environ,{'LDM_CODEX_BIN':str(other)}):
                    auth._native_login(1)
                self.assertEqual(len(spawned),2);self.assertTrue(spawned[0].closed)
                with patch.dict(os.environ,{'CODEX_HOME':str(other_home)}):
                    auth._native_login(1)
                self.assertEqual(len(spawned),3);self.assertTrue(spawned[1].closed)
                self.bin.write_text('upgraded')  # in-place upgrade of the same path
                auth._native_login(1);self.assertEqual(len(spawned),4);self.assertTrue(spawned[2].closed)
        finally:
            auth._drop_session()


class BackgroundSuccessor(unittest.TestCase):
    """The next app-server is prepared off the lock; an age-only retirement adopts it without a spawn wait."""
    def setUp(self):
        auth._drop_session()
        self.spawned=[];self.state=('exe','home',(1,10,100),None);self.owner='native-workspace'
        self.delay=0;self.fail_spawn=0;self.lock=threading.Lock();self.served=[]
        test=self
        class Fake:
            def __init__(self,deadline):
                with test.lock:test.spawned.append(self);self.index=len(test.spawned)
                self.closed=False;self.broken=threading.Event();self.proc=MagicMock();self.proc.poll.return_value=None
                self.inbox=queue.Queue();self.calls=0;self.kwargs=[]
                if test.index_delay(self.index):time.sleep(test.delay)
                if test.fail_spawn:
                    test.fail_spawn-=1
                    raise native.NativeRpcError('codex_start','Could not start the native Codex auth process',422)
                self.started=time.monotonic()
            def credential(self,deadline,**kwargs):
                self.calls+=1;self.kwargs.append(kwargs);test.served.append(self.index)
                return native.Credential(token(),test.owner,'https://chatgpt.com')
            def close(self):self.closed=True
        self.patches=[patch.object(native,'Session',Fake),patch.object(auth,'_store_state',lambda:self.state)]
        for p in self.patches:p.start()
    def tearDown(self):
        auth._drop_session()
        for p in self.patches:p.stop()

    def index_delay(self,index):return index>1  # only background spawns are slow; the first is the cold lookup

    def age(self,session,seconds):session.started-=seconds
    def near_limit(self,session):self.age(session,auth.SESSION_MAX_AGE-auth.SUCCESSOR_MARGIN+1)
    def wait(self,predicate,limit=3):
        end=time.monotonic()+limit
        while not predicate():
            self.assertLess(time.monotonic(),end);time.sleep(.005)
    def timed(self):
        began=time.monotonic();auth._native_login(1);return time.monotonic()-began

    def test_successor_is_adopted_with_no_lookup_blocking_on_spawn(self):
        self.delay=.4
        auth._native_login(1);old=self.spawned[0]
        self.near_limit(old)
        slow=[self.timed() for _ in range(3)]  # the spawn is in flight; lookups keep using the live process
        self.assertTrue(all(t<.15 for t in slow),slow)
        self.assertEqual(len(self.spawned),2,'at most one successor in flight');self.assertEqual(old.calls,4)
        self.wait(lambda:auth._succ['ready'] is not None)
        self.age(old,auth.SUCCESSOR_MARGIN)  # now aged out for age alone
        self.assertLess(self.timed(),.15)
        new=self.spawned[1]
        self.assertEqual(len(self.spawned),2);self.assertIs(auth._session,new);self.assertEqual(new.calls,1)
        self.assertEqual(new.kwargs[0]['expected_account'],'native-workspace','the old binding carries over')
        self.wait(lambda:old.closed);self.assertEqual(old.calls,4,'the old process never serves after adoption')
        self.assertFalse(new.closed);auth._native_login(1);self.assertEqual((len(self.spawned),new.calls),(2,2))
        self.assertEqual(self.served,[1,1,1,1,2,2])  # lookups never interleave two processes

    def test_successor_is_discarded_when_the_store_changed(self):
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        auth._native_login(1);self.wait(lambda:auth._succ['ready'] is not None)
        successor=self.spawned[1];self.age(old,auth.SUCCESSOR_MARGIN)
        self.state=('exe','home',(1,11,100),None)
        auth._native_login(1)
        self.assertEqual(len(self.spawned),3);self.assertIs(auth._session,self.spawned[2])
        self.wait(lambda:successor.closed and old.closed);self.assertEqual(successor.calls,0)
        self.assertIsNone(auth._succ['ready'])

    def test_successor_made_under_another_store_state_is_never_adopted(self):
        auth._native_login(1);old=self.spawned[0];self.age(old,auth.SESSION_MAX_AGE+1)  # aged for age alone
        stray=native.Session(time.monotonic()+1)
        with auth._succ_lock:auth._succ['ready']=(stray,('exe','home',(9,9,9),None))
        auth._native_login(1)
        self.assertNotIn(stray.index,self.served);self.wait(lambda:stray.closed and old.closed)
        self.assertIsNot(auth._session,stray);self.assertEqual(len(self.spawned),3)

    def test_fault_retires_immediately_and_drops_the_successor(self):
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        auth._native_login(1);self.wait(lambda:auth._succ['ready'] is not None)
        successor=self.spawned[1];old.broken.set()
        auth._native_login(1)
        self.assertEqual(len(self.spawned),3);self.assertIsNot(auth._session,successor)
        self.wait(lambda:successor.closed and old.closed)

    def test_idle_message_or_refresh_retires_immediately(self):
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        auth._native_login(1);self.wait(lambda:auth._succ['ready'] is not None)
        successor=self.spawned[1];old.inbox.put(b'{}\n')
        auth._native_login(1)
        self.assertEqual(len(self.spawned),3);self.wait(lambda:successor.closed)
        auth._native_login(1)
        self.near_limit(auth._session);auth._native_login(1)
        self.wait(lambda:auth._succ['ready'] is not None);ready=auth._succ['ready'][0]
        auth._native_login(1,refresh=True)
        self.assertTrue(ready.closed);self.assertIsNone(auth._succ['ready']);self.assertIsNone(auth._session)

    def test_successor_failure_is_logged_and_the_lookup_falls_back_to_a_synchronous_spawn(self):
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        self.fail_spawn=1
        with self.assertLogs(auth.log,'WARNING') as logs:
            auth._native_login(1);self.wait(lambda:not auth._succ['busy'])
        self.assertIn('codex_start',logs.output[0]);self.assertNotIn('native-workspace',logs.output[0])
        self.assertIsNone(auth._succ['ready']);self.age(old,auth.SUCCESSOR_MARGIN)
        self.assertEqual(auth._native_login(1)[1],'native-workspace')
        self.assertEqual(len(self.spawned),3);self.assertIs(auth._session,self.spawned[2]);self.wait(lambda:old.closed)

    def test_stale_ready_successor_is_not_adopted(self):
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        auth._native_login(1);self.wait(lambda:auth._succ['ready'] is not None)
        successor=self.spawned[1];self.age(old,auth.SUCCESSOR_MARGIN);self.age(successor,auth.SESSION_MAX_AGE+1)
        auth._native_login(1)
        self.assertEqual(len(self.spawned),3);self.wait(lambda:successor.closed and old.closed)

    def test_dropping_the_session_closes_ready_and_in_flight_successors(self):
        self.delay=.3
        auth._native_login(1);old=self.spawned[0];self.near_limit(old)
        auth._native_login(1);self.wait(lambda:len(self.spawned)==2)
        pending=self.spawned[1];auth._drop_session()  # the spawn is still running
        self.wait(lambda:pending.closed and old.closed);self.assertIsNone(auth._succ['ready'])
        self.wait(lambda:not auth._succ['busy'])
        auth._native_login(1);self.near_limit(auth._session)
        auth._native_login(1);self.wait(lambda:auth._succ['ready'] is not None)
        ready=auth._succ['ready'][0];auth._drop_session()
        self.assertTrue(ready.closed)

    def test_unobservable_store_never_prepares_a_successor(self):
        self.state=None;auth._native_login(1)
        self.assertIsNone(auth._session);self.assertFalse(auth._succ['busy']);self.assertEqual(len(self.spawned),1)


class ChildStderr(unittest.TestCase):
    def test_tail_is_bounded_and_keeps_the_newest_bytes(self):
        tail=native._Tail()
        for index in range(100):tail.add(b'%05d ' % index+b'x'*250+b'\n')
        self.assertLessEqual(len(tail.data),native.STDERR_TAIL);self.assertIn('00099',tail.text());self.assertNotIn('00000',tail.text())

    def test_drain_thread_empties_a_real_pipe_beyond_the_ring_size(self):
        read,write=os.pipe();session=native.Session.__new__(native.Session)
        session.proc=MagicMock();session.proc.stderr=os.fdopen(read,'rb');session.stderr=native._Tail()
        drain=threading.Thread(target=session._drain,daemon=True);drain.start()
        with os.fdopen(write,'wb',buffering=0) as out:
            for _ in range(64):out.write(b'noise '*1000)  # 384 KB, far more than a pipe buffer
            out.write(b'FINAL LINE\n')
        drain.join(2);self.assertFalse(drain.is_alive())
        self.assertLessEqual(len(session.stderr.data),native.STDERR_TAIL);self.assertTrue(session.stderr.text().endswith('FINAL LINE\n'))

    def test_redaction_strips_tokens_jwts_and_bearer_values(self):
        jwt='eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl'
        text=f'GET failed Authorization: Bearer {jwt}\naccess_token="abc123"\nrefresh_token: rt-AbCdEf123456\nopaque {"A"*40}\nsk-live_12345678\nplain note\x1b[31m'
        out=native.redact(text)
        for secret in (jwt,'abc123','AbCdEf123456','A'*40,'live_12345678'):self.assertNotIn(secret,out)
        self.assertIn('plain note',out);self.assertNotIn('\x1b',out)

    def test_only_channel_and_protocol_errors_carry_the_redacted_tail(self):
        session=native.Session.__new__(native.Session);session.proc=MagicMock();session.proc.poll.return_value=None
        session.stderr=native._Tail();session.stderr.add(b'panic: boom Bearer sekrit-token-value\n')
        for code in ('native_auth_channel','native_auth_protocol'):
            error=session._fail(code,'Native credential channel ended',502)
            self.assertIn('panic: boom',error.message);self.assertNotIn('sekrit',error.message);self.assertEqual((error.code,error.status),(code,502))
        self.assertEqual(session._fail('native_auth_rpc','rejected',422).message,'rejected')
        quiet=native.Session.__new__(native.Session);quiet.proc=MagicMock();quiet.stderr=native._Tail()
        self.assertEqual(quiet._fail('native_auth_channel','ended',502).message,'ended')
        self.assertLessEqual(len(session._fail('native_auth_channel','x',502).message),native.ERROR_TAIL+80)
