"""Native absence proof, local configuration, diagnostics and wire regressions."""
import asyncio
import contextlib
import io
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

from jev_luna import cli, connect, core, doctor, native_rpc as native, subscription_auth as auth, transport
from jev_luna.host_config import configure_mcp
from jev_luna.lifecycle import RequestContext, bind
from jev_luna.local import loopback_url
import test_native_manager as broker_fixture
from test_native_manager import ACCOUNT, STATUS

EMPTY_STATUS = {'authMethod': None, 'authToken': None, 'requiresOpenaiAuth': True}
EMPTY_ACCOUNT = {'account': None, 'requiresOpenaiAuth': True, 'workspaceRouting': None}


class NativeAbsence(unittest.TestCase):
    def test_real_broker_path_requires_two_explicit_empty_native_pairs(self):
        broker = broker_fixture.NativeManager()
        with self.assertRaises(auth.CodexLoginRequired):
            broker.invoke([{}, EMPTY_STATUS, EMPTY_ACCOUNT, EMPTY_STATUS, EMPTY_ACCOUNT])
        self.assertFalse(any(item.get('params', {}).get('includeToken') for item in broker.sent))

    def test_ambiguous_token_failure_missing_fields_and_changed_account_fail_closed(self):
        for account in (ACCOUNT, {}, {'requiresOpenaiAuth': True, 'account': None},
                        {**EMPTY_ACCOUNT, 'requiresOpenaiAuth': 1}):
            broker = broker_fixture.NativeManager()
            with self.subTest(account=account), self.assertRaises(auth.NativeAuthStateError) as failure:
                broker.invoke([{}, EMPTY_STATUS, account])
            self.assertEqual(failure.exception.code, 'native_login_unavailable')
        broker = broker_fixture.NativeManager()
        with self.assertRaises(auth.NativeAuthStateError):
            broker.invoke([{}, EMPTY_STATUS, EMPTY_ACCOUNT, STATUS, ACCOUNT])

    def test_absence_never_starts_login_for_an_already_bound_owner(self):
        session = native.Session.__new__(native.Session)
        session.account_epoch = 0
        session.broken = threading.Event()
        with patch.object(session, 'rpc', side_effect=[EMPTY_STATUS, EMPTY_ACCOUNT, EMPTY_STATUS, EMPTY_ACCOUNT]), \
             self.assertRaises(native.NativeRpcError) as failure:
            session.credential(time.monotonic()+1, expected_account='existing-owner')
        self.assertEqual(failure.exception.code, 'native_login_unavailable')

    def test_login_command_is_reachable_only_after_the_real_broker_absence_proof(self):
        broker = broker_fixture.NativeManager()
        replies = [[{}, EMPTY_STATUS, EMPTY_ACCOUNT, EMPTY_STATUS, EMPTY_ACCOUNT], broker.results()]
        def lookup(timeout, **kwargs):
            broker.invoke(replies.pop(0), timeout=timeout)
            return ('/responses', {}, (), None)
        with patch.object(auth, 'request_parameters', side_effect=lookup), \
             patch.object(connect.subprocess, 'run', return_value=Mock(returncode=0)) as login:
            self.assertEqual(connect.login_existing(allow_login=True), 'codex_chatgpt')
        self.assertEqual(login.call_count, 1)
        self.assertEqual(login.call_args.args[0], ['codex', 'login'])
        self.assertEqual(replies, [])


class LocalConnection(unittest.TestCase):
    def test_base_install_names_the_missing_mcp_extra_before_optional_dependencies(self):
        script = '''import importlib.abc, sys
class MissingOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in ('mcp','pydantic'):
            raise ModuleNotFoundError('missing optional package', name=fullname.split('.')[0])
sys.meta_path.insert(0,MissingOptional())
from jev_luna.cli import main
sys.argv=['luna-decision','mcp']
sys.exit(main())
'''
        result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('luna-decision-model[mcp]',result.stderr)
        self.assertEqual(result.stdout,'')

    def test_removed_configuration_is_rejected_even_when_empty(self):
        for key, value in (('DECIDE_D_TRANSPORT', 'proxy'), ('DECIDE_D_UPSTREAM', ''),
                           ('DECIDE_D_RESPONSES_URL', 'http://127.0.0.1:1/responses'), ('DECIDE_D_PROXY_KEY', '')):
            env = {name: data for name, data in os.environ.items() if name not in (
                'DECIDE_D_UPSTREAM', 'DECIDE_D_RESPONSES_URL', 'DECIDE_D_PROXY_KEY', 'DECIDE_D_TRANSPORT')}
            env[key] = value
            result = subprocess.run([sys.executable, '-c', 'import jev_luna.core'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('remove legacy proxy settings', result.stderr)
        with patch('sys.argv', ['luna-decision', 'serve', '--backend', 'proxy']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error: cli.main()
        self.assertEqual(error.exception.code, 2)

    def test_loopback_urls_fail_before_reading_a_service_key(self):
        for url in ('http://localhost', 'http://localhost:0', 'https://localhost:2',
                    'http://user@localhost:2', 'http://localhost:2/v1', 'http://localhost:2?a=b',
                    'http://localhost:2\n'):
            with self.subTest(url=url), self.assertRaises(ValueError): loopback_url(url)
        self.assertEqual(loopback_url('http://[::1]:20299/'), 'http://[::1]:20299')

    def test_async_sdk_keeps_caller_options_and_uses_a_local_client(self):
        custom = object()
        with patch.object(connect, 'service_key', return_value='fixture'), patch('openai.AsyncOpenAI') as sdk:
            connect.async_openai_client(base_url='http://localhost:20299/v1', timeout=7, max_retries=0, http_client=custom)
            self.assertEqual(sdk.call_args.kwargs, {'api_key':'fixture', 'base_url':'http://localhost:20299/v1',
                                                   'timeout':7, 'max_retries':0, 'http_client':custom})
        with patch.object(connect, 'service_key', return_value='fixture'), patch('openai.AsyncOpenAI'), \
             patch('openai.DefaultAsyncHttpxClient') as http:
            connect.async_openai_client()
            http.assert_called_once_with(trust_env=False)
        with self.assertRaises(ValueError): connect.async_openai_client(api_key='external')

    def test_remote_image_order_and_detail_are_exact_and_non_http_sources_fail(self):
        parts = [{'type':'input_text','text':'before'}, {'type':'input_image',
                  'image_url':'https://images.example/a.png?query=literal', 'detail':'original'},
                 {'type':'input_text','text':'after'}]
        body = {'model':'gpt-6-luna', 'input':[{'role':'user','content':parts}],
                'questions':[{'type':'predicate','instructions':'Red?'}]}
        state, qs, _ = core.parse_decisions(body)
        self.assertEqual(core.build_upstream(state, qs)['input'][0]['content'], parts)
        for url in ('file:///private/image.png', '//images.example/a.png', 'https://user:pass@images.example/a.png',
                    'https://', 'https://images.example:invalid/a.png', 'https://images.example/a\nb.png'):
            with self.subTest(url=url), self.assertRaises(core.Reject):
                core.parse_decisions({**body, 'input':[{'role':'user','content':[{'type':'input_image','image_url':url}]}]})


class ProjectMCP(unittest.TestCase):
    def test_project_lock_links_never_change_an_outside_sentinel(self):
        for linked in ('symlink', 'hardlink'):
            with self.subTest(link=linked), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); project=root/'project';project.mkdir()
                outside=root/'outside-user-work';outside.write_bytes(b'preserve user work\n')
                lock=project/'.luna-decision-mcp.lock'
                if linked=='symlink': lock.symlink_to(outside)
                else: os.link(outside,lock)
                with self.assertRaises(ValueError): configure_mcp(project)
                self.assertEqual(outside.read_bytes(),b'preserve user work\n')
                self.assertFalse((project/'.mcp.json').exists())

    def test_concurrent_same_entry_has_one_publication_and_one_journal(self):
        import concurrent.futures
        with tempfile.TemporaryDirectory() as directory:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                results=list(pool.map(lambda _:configure_mcp(directory),range(2)))
            self.assertEqual(sorted(result['status'] for result in results),['already_configured','configured'])
            self.assertEqual(set(json.loads((Path(directory)/'.mcp.json').read_text())['mcpServers']),{'luna-decision'})
            self.assertEqual(configure_mcp(directory,restore=True)['status'],'restored')

    def test_configure_is_idempotent_preserves_user_fields_and_restore_preserves_later_edits(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'JEV_LUNA_HOME':'/fixture/app-state'}):
            root = Path(directory); path = root/'.mcp.json'
            original = {'custom': {'keep': True}, 'mcpServers': {'existing': {'command':'custom'}}}
            path.write_text(json.dumps(original))
            first = configure_mcp(root, port=20299)
            installed = path.read_bytes()
            self.assertEqual(first['status'], 'configured')
            self.assertEqual(configure_mcp(root, port=20299)['status'], 'already_configured')
            self.assertEqual(path.read_bytes(), installed)
            current = json.loads(installed); current['later'] = 4; path.write_text(json.dumps(current))
            self.assertEqual(configure_mcp(root, restore=True)['status'], 'restored')
            self.assertEqual(json.loads(path.read_text()), {**original, 'later':4})
            self.assertEqual(configure_mcp(root, restore=True)['status'], 'already_restored')

    def test_new_file_restore_and_modified_entry_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root/'.mcp.json'
            configure_mcp(root); configure_mcp(root, restore=True)
            self.assertFalse(path.exists())
            configure_mcp(root)
            changed = json.loads(path.read_text()); changed['mcpServers']['luna-decision']['args'] = ['modified']
            path.write_text(json.dumps(changed)); before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'changed'): configure_mcp(root, restore=True)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, 'different settings'): configure_mcp(root)
            self.assertEqual(path.read_bytes(), before)

    def test_legacy_equivalent_does_not_install_duplicate_and_symlinks_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root/'.mcp.json'
            path.write_text(json.dumps({'mcpServers': {'jev-decisions': connect.configuration('127.0.0.1', 20141)['mcp']}}))
            self.assertEqual(configure_mcp(root)['server_name'], 'jev-decisions')
            self.assertEqual(set(json.loads(path.read_text())['mcpServers']), {'jev-decisions'})
            before = path.read_bytes(); target = root/'target'; path.rename(target); path.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'symlink'): configure_mcp(root)
            self.assertEqual(target.read_bytes(), before)

    def test_cli_config_stdout_is_json_and_never_reads_a_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(connect, 'service_key') as key:
            output = io.StringIO()
            with patch('sys.argv', ['luna-decision','config','--write-mcp',directory]), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'configured')
            key.assert_not_called()


class RuntimeEvidence(unittest.TestCase):
    def test_refresh_preserves_unique_physical_attempts_and_usage_once(self):
        state, qs = core.parse_systemone({'model':'gpt-6-luna','state':'on',
                                         'questions':{'on':{'type':'noul','instructions':'On?'}}})
        events=[]; ctx=RequestContext(time.monotonic()+3, observer=lambda name, fields: events.append((name, fields)))
        error=transport.provider_error(401, {'error':{'code':'token_expired'}})
        route=transport.Route('https://chatgpt.com/backend-api/codex/responses',{},auth_generation=('native','g','NO_CONSTRAINT'))
        with patch.object(core,'prepare_auth',return_value=route), patch.object(auth,'refresh'), \
             patch.object(core,'upstream_call',side_effect=[error,({'q0':{'p_true':.9}},{'input_tokens':7,'output_tokens':2})]):
            answers,usage,_=core.decide(state,qs,context=ctx)
        attempts=[data for name,data in events if name=='attempt_started']
        self.assertEqual([data['attempt'] for data in attempts], [1,1])
        self.assertEqual([data['physical_attempt_id'] for data in attempts], [1,2])
        self.assertEqual(usage['input_tokens'],7)
        self.assertEqual(ctx.snapshot()['unknown_usage_attempts'],1)

    def test_completed_model_errors_and_stream_errors_retain_codes_but_different_evidence(self):
        state,qs=core.parse_systemone({'model':'gpt-6-luna','state':'x',
                                     'questions':{'on':{'type':'noul','instructions':'On?'}}})
        payload=core.build_upstream(state,qs)
        client=Mock(); client.call.return_value=({'model':'gpt-6-luna','status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':'not json'}]}],
            'usage':{'input_tokens':7,'output_tokens':2}}, {}, {'input_tokens':7,'output_tokens':2})
        ctx=RequestContext(time.monotonic()+1)
        with patch('jev_luna.transport.client',return_value=client), bind(ctx,route=transport.Route('http://fixture',{})), \
             self.assertRaises(core.Reject) as failure:
            core.upstream_call(payload,1)
        self.assertEqual((failure.exception.code, failure.exception.category, failure.exception.reason),
                         ('invalid_output','model_readout_invalid','invalid_json'))
        self.assertTrue(failure.exception.upstream_completed)
        stream=transport._stream_failure('Invalid event', 'invalid_event_json')
        self.assertEqual(stream.code,'invalid_output')
        self.assertEqual(stream.category,'stream_protocol_error')
        self.assertFalse(stream.upstream_completed)

    def test_mcp_describes_questions_without_restricting_shape_or_claiming_idempotence(self):
        from jev_luna import mcp
        tools=asyncio.run(mcp.mcp.list_tools())
        tool=next(item for item in tools if item.name=='jev_decide')
        self.assertIn('criteria',tool.inputSchema['properties']['questions']['description'])
        self.assertFalse(tool.annotations.idempotentHint)
        self.assertTrue(tool.inputSchema['properties']['questions']['additionalProperties'])


class TargetDoctor(unittest.TestCase):
    def test_dripping_target_health_is_bounded_by_a_total_budget(self):
        class Drip(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length','65535');self.end_headers()
                try:
                    for _ in range(100): self.wfile.write(b' ');self.wfile.flush();time.sleep(.03)
                except (BrokenPipeError, ConnectionResetError): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Drip)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            started=time.monotonic()
            with self.assertRaises((OSError,ValueError,http.client.HTTPException)):
                doctor.server_health(f'http://127.0.0.1:{server.server_port}',timeout=.12)
            self.assertLess(time.monotonic()-started,.35)
        finally: server.shutdown();server.server_close();worker.join(1)

    def test_target_build_mismatch_unreachable_and_no_inference_claim(self):
        class Health(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                raw=json.dumps({'ok':True,'build_sha256':'0'*64,'readout_profile':'baseline',
                                'transport':'subscription','model':'gpt-6-luna'}).encode()
                self.send_response(200); self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Health)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(auth,'connection',return_value=(Mock(),'/responses',{})):
                result=doctor.check(url=url)
            self.assertFalse(result['ok']);self.assertTrue(result['target_server']['liveness'])
            self.assertFalse(result['target_server']['build_matches_client'])
            self.assertFalse(result['target_server']['native_inference_verified'])
            self.assertEqual(result['client_build_sha256'],doctor.identity())
        finally: server.shutdown();server.server_close();worker.join(1)
        with patch.object(auth,'connection',return_value=(Mock(),'/responses',{})):
            result=doctor.check(url=url)
        self.assertFalse(result['target_server']['reachable'])
        self.assertFalse(result['ok'])


if __name__ == '__main__': unittest.main()
