"""Original SDK/native entry contracts against an isolated candidate and local mock upstream.

No Codex login, subscription request, API-key route, or active host modification.
Pass --runtime-root pointing to an installed wheel and its dependency interpreter
for final-artifact acceptance. Node SDKs and prepared upstream sources are explicit
prerequisites; this tool does not install or silently download them.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import queue
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def selector(bundle, preload, node, env, workspace):
    process = subprocess.Popen([node, '--import', str(preload), str(bundle)], env=env, cwd=workspace,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    responses, stdout = queue.Queue(), []
    def collect():
        for line in process.stdout:
            stdout.append(line)
            try:
                responses.put(json.loads(line))
            except json.JSONDecodeError:
                responses.put({'invalid_stdout': True})
    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    def rpc(identity, method, parameters):
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': parameters}) + '\n')
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = responses.get(timeout=max(.01, deadline - time.monotonic()))
            require('invalid_stdout' not in response, 'Human logs polluted MCP stdout')
            if response.get('id') == identity:
                require('error' not in response, 'Original MCP returned a protocol error')
                return response['result']
        raise TimeoutError('Original MCP operation did not complete')
    try:
        initialized = rpc(1, 'initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                          'clientInfo': {'name': 'integration-contract', 'version': '1'}})
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
        process.stdin.flush()
        tools = rpc(2, 'tools/list', {})
        require({tool['name'] for tool in tools['tools']} ==
                {'read_large_text_evidence', 'read_selected_evidence', 'search_workspace_evidence'}, 'Original tool set changed')
        reply = rpc(3, 'tools/call', {'name': 'read_large_text_evidence', 'arguments': {
            'workspaceRoot': str(workspace), 'path': 'application.log',
            'query': 'Why did checkout initialization fail?',
            'requirements': ['Identify the root cause and configuration key', 'Preserve the associated stack trace'],
            'includeDiagnostics': True}})
        require(not reply.get('isError'), 'Original MCP tool failed')
        value = reply.get('structuredContent') or json.loads(reply['content'][0]['text'])
        require(value.get('mode') == 'jev', 'Selector bypass/fallback is a failed connectivity check')
        return {'server': initialized['serverInfo'], 'mode': value['mode'],
                'metrics': value['metrics'], 'diagnostics': value['diagnostics']}
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=3)
        reader.join(timeout=3)
        require(all(json.loads(line).get('jsonrpc') == '2.0' for line in stdout), 'MCP stdout was not pure JSON-RPC')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--node-sdk-root', type=Path, required=True, help='Directory containing installed openai and @typesafe-ai/sdk packages')
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--node', default='node')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    node = shutil.which(args.node)
    if node is None: raise ValueError('Install Node and supply its interpreter with --node')
    args.node = str(Path(node).resolve())
    runtime_root = args.runtime_root.resolve()
    sys.path.insert(0, str(runtime_root))
    with tempfile.TemporaryDirectory(prefix='luna-integration-contract-') as temporary:
        home = Path(temporary)
        env = {key: value for key, value in os.environ.items() if key not in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'TYPESAFE_API_KEY', 'JEV_MODEL', 'NODE_OPTIONS')}
        env.update({'HOME': str(home), 'JEV_LUNA_HOME': str(home / 'state'), 'JEV_DATA': str(home / 'usage')})
        with patch.dict(os.environ, env, clear=True):
            from jev_luna import core, connect
            from jev_luna.local import service_key
            from jev_luna.transport import Route
            from jev_luna.receipts import flush_traces
            from typesafe_sdk import Noul, Choice, Score
            key = service_key(create=True)
            marker = 'integration-contract-' + uuid.uuid4().hex
            state = 'The switch is enabled. ' + marker
            trace_directory = home / 'traces'; trace_directory.mkdir()
            observed = []
            class Upstream(BaseHTTPRequestHandler):
                delay = 0
                def log_message(self, *args): pass
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    observed.append({'path': self.path, 'body_sha256': hashlib.sha256(json.dumps(body).encode()).hexdigest(),
                                     'run_marker_present': marker in json.dumps(body)})
                    if self.delay: time.sleep(self.delay)
                    report = {}
                    for qid, question in body['text']['format']['schema']['properties'].items():
                        schema = question['properties']['answer']
                        if 'p_true' in schema.get('properties', {}): answer = {'p_true': 1}
                        elif schema.get('type') == 'array': answer = [1] + [0] * (schema['minItems'] - 1)
                        else: answer = {name: int(index == 0) for index, name in enumerate(schema['required'])}
                        report[qid] = {'refused': False, 'answer': answer}
                    response = {'model': 'gpt-6-luna', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(report)}]}],
                                'usage': {'input_tokens': 1, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                                          'output_tokens': 1, 'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': 2}}
                    raw = ('data: ' + json.dumps({'type': 'response.completed', 'response': response}) + '\n\n').encode()
                    try:
                        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                    except OSError: pass  # The caller-abort contract intentionally closes its own socket.
            upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
            candidate = core.ThreadingHTTPServer(('127.0.0.1', 0), core.H)
            candidate.local_key, candidate.trace_directory = key, str(trace_directory)
            root = f'http://127.0.0.1:{candidate.server_port}'
            for server in (upstream, candidate): threading.Thread(target=server.serve_forever, daemon=True).start()
            result = {'passed': False, 'fixture_only': True, 'subscription_calls': 0, 'run_marker': marker, 'tool_sha256': digest(__file__), 'candidate_build_sha256': core.BUILD_SHA256,
                      'runtime_files': {str(path.relative_to(runtime_root)): digest(path) for path in [runtime_root / 'jev_luna/connect.py', runtime_root / 'jev_luna/integration_config.py', runtime_root / 'jev_luna/assets/sdk-local.mjs']},
                      'versions': {'openai_python': importlib.metadata.version('openai'), 'typesafe_python': importlib.metadata.version('typesafe-sdk')}, 'checks': []}
            from jev_luna.integration_config import configuration, project_configuration
            result['configuration_checks'] = []
            for target in ('typesafe','openai','native-jev','codex-selector'):
                project=home/target;project.mkdir();claude=project/'.claude';claude.mkdir()
                user_settings=claude/'settings.local.json';original='{"env":{"USER_FIELD":"keep"}}\n';user_settings.write_text(original)
                settings=configuration(target,port=candidate.server_port,preparation=str(args.preparation.resolve()) if target in ('native-jev','codex-selector') else None,
                                       node=args.node if target=='codex-selector' else None)
                before={p.relative_to(project) for p in project.rglob('*')}
                assert project_configuration(project,settings)['status']=='dry-run'
                assert {p.relative_to(project) for p in project.rglob('*')}==before
                assert project_configuration(project,settings,action='write')['status']=='configured'
                assert project_configuration(project,settings,action='write')['status']=='already_configured'
                assert project_configuration(project,settings,action='check')['status']=='verified'
                assert key not in (project/'.luna-decision-connect.json').read_text() and key not in (claude/'rules/luna-decision-connection.md').read_text()
                assert project_configuration(project,settings,action='restore')['status']=='restored'
                assert project_configuration(project,settings,action='restore')['status']=='already_restored'
                assert user_settings.read_text()==original
                result['configuration_checks'].append({'target':target,'dry_run_no_writes':True,'write_check_duplicate_restore_passed':True,'user_settings_preserved':True,'plaintext_key_absent':True})
            def record(name, callback, *, require_request=True):
                before = set(trace_directory.glob('req_*.json'))
                value = callback()
                if not flush_traces(3): raise RuntimeError('Candidate trace drain failed')
                traces = [json.loads(path.read_text()) for path in set(trace_directory.glob('req_*.json')) - before]
                if require_request: traces = [trace for trace in traces if any(event['event'] == 'request' for event in trace['events'])]
                if not traces or not all(trace['phase'] == 'finished' and trace['build_sha256'] == core.BUILD_SHA256 for trace in traces): raise RuntimeError('Missing candidate trace')
                result['checks'].append({'entry': name, 'passed': True, 'logical_requests': len(traces), 'request_ids': [trace['request_id'] for trace in traces],
                                         'request_events': [next((event for event in trace['events'] if event['event'] == 'request'), None) for trace in traces],
                                         'http_statuses': [next((event.get('status') for event in reversed(trace['events']) if event['event']=='http_response'),None) for trace in traces], 'result': value})
            questions = [{'type': 'predicate', 'name': 'enabled', 'instructions': 'Is it enabled?'},
                         {'type': 'choice', 'name': 'topic', 'instructions': 'Choose a topic.', 'choices': [{'value': 'billing'}, {'value': 'technical'}]},
                         {'type': 'score', 'name': 'severity', 'instructions': 'Select severity.', 'levels': [{'label': 'low'}, {'label': 'high'}]}]
            tq = {'enabled': Noul(instructions='Is it enabled?'), 'topic': Choice(instructions='Choose a topic.', criteria={'billing': None, 'technical': None}), 'severity': Score(instructions='Select severity.', criteria=['low', 'high'])}
            try:
                with patch.object(core, 'prepare_auth', return_value=Route(f'http://127.0.0.1:{upstream.server_port}/responses', {})):
                    def openai_sync():
                        with connect.openai_client(base_url=root+'/v1') as client:
                            reply = client.decisions.create(model='gpt-6-luna', input=state, questions=questions)
                            assert [answer.type for answer in reply.answers] == ['predicate', 'choice', 'score']
                            return {'types': [answer.type for answer in reply.answers]}
                    record('OpenAI Python sync', openai_sync)
                    async def openai_async():
                        async with connect.async_openai_client(base_url=root+'/v1') as client:
                            reply = await client.decisions.create(model='gpt-6-luna', input=state, questions=questions)
                            assert len(reply.answers) == 3
                            return {'types': [answer.type for answer in reply.answers]}
                    record('OpenAI Python async', lambda: asyncio.run(openai_async()))
                    def typesafe_sync():
                        with connect.typesafe_client(base_url=root) as client:
                            assert client._config.timeout.read == 10
                            reply = client.system_one(state=state, questions=tq)
                            assert len(reply.answers) == 3
                            return {'types': [type(answer).__name__ for answer in reply.answers.values()]}
                    record('TypeSafe Python sync', typesafe_sync)
                    async def typesafe_async():
                        async with connect.async_typesafe_client(base_url=root) as client:
                            reply = await client.system_one(state=state, questions=tq)
                            assert len(reply.answers) == 3
                            return {'types': [type(answer).__name__ for answer in reply.answers.values()]}
                    record('TypeSafe Python async', lambda: asyncio.run(typesafe_async()))
                    helper = (runtime_root / 'jev_luna/assets/sdk-local.mjs').as_uri()
                    sdkroot = args.node_sdk_root.resolve()
                    for profile, module, factory, klass, base in [('openai', sdkroot/'openai/index.mjs', 'openaiClient', 'OpenAI', root+'/v1'),
                                                                 ('typesafe', sdkroot/'@typesafe-ai/sdk/dist/index.mjs', 'typesafeClient', 'TypeSafeClient', root)]:
                        code = f'import {{ {factory} }} from {json.dumps(helper)};\n'
                        code += (f'import OpenAI from {json.dumps(module.as_uri())};' if profile == 'openai' else f'import {{TypeSafeClient,noul,choice,score}} from {json.dumps(module.as_uri())};')
                        code += f'const client={factory}({klass},{{baseURL:{json.dumps(base)}}});'
                        code += ('const reply=await client.decisions.create('+json.dumps({'model':'gpt-6-luna','input':state,'questions':questions})+').withResponse();'
                                 if profile == 'openai' else "const reply=await client.systemOne({model:'gpt-6-luna',state:"+json.dumps(state)+",questions:{enabled:noul('Is it enabled?'),topic:choice('Choose a topic.',{billing:null,technical:null}),severity:score('Select severity.',['low','high'])}}).withResponse();")
                        code += "console.log(JSON.stringify({rid:reply.request_id??reply.requestId,status:reply.response.status,model:reply.data.model}));"
                        def js(code=code):
                            reply = subprocess.run([args.node, '--input-type=module', '-e', code], env=env, capture_output=True, text=True, timeout=30, check=True)
                            value=json.loads(reply.stdout); assert value['status']==200 and value['rid']; return value
                        record(profile+' JavaScript SDK', js)
                        # Factory preserves the actual SDK caller's AbortSignal; no custom fetch or retry policy.
                        Upstream.delay = .5
                        abort_code = code[:code.index('const reply=')] + 'const signal=AbortSignal.timeout(100);let abortedError=false;'
                        abort_code += ('try{await client.decisions.create('+json.dumps({'model':'gpt-6-luna','input':state,'questions':questions})+', {signal});}'
                                       if profile=='openai' else "try{await client.systemOne({model:'gpt-6-luna',state:"+json.dumps(state)+",questions:{enabled:noul('Is it enabled?')}},{signal});}")
                        abort_code += "catch(error){if(!signal.aborted)throw error;abortedError=true;console.log(JSON.stringify({caller_aborted:true,error_class:error.constructor.name}));}if(!abortedError)throw new Error('abort not delivered');"
                        def abort_js(code=abort_code):
                            reply = subprocess.run([args.node,'--input-type=module','-e',code],env=env,text=True,capture_output=True,timeout=30,check=True)
                            value=json.loads(reply.stdout);assert value['caller_aborted'];time.sleep(.6);return value
                        record(profile+' JavaScript caller abort', abort_js)
                        assert result['checks'][-1]['http_statuses'] == [499]
                        Upstream.delay = 0
                        result['versions'][profile+'_javascript'] = json.loads((module.parent / 'package.json' if profile=='openai' else module.parent.parent/'package.json').read_text())['version']
                    # Original SDK returns a local authentication error, with no mock/model upstream forwarding.
                    before_fault = len(observed)
                    credential = Path(env['JEV_LUNA_HOME'])/'service.key'
                    credential.write_text('jvl_local_'+'Z'*40+'\n')
                    try:
                        def auth_fault():
                            import openai
                            with connect.openai_client(base_url=root+'/v1') as client:
                                try: client.decisions.create(model='gpt-6-luna', input='The switch is enabled.', questions=questions)
                                except openai.AuthenticationError as error: assert error.status_code==401; return {'status':401,'error_class':type(error).__name__,'upstream_forwarded':False}
                                raise AssertionError('Expected local authentication rejection')
                        record('OpenAI Python local auth fault', auth_fault, require_request=False)
                        assert len(observed)==before_fault
                    finally: credential.write_text(key+'\n')
                    prep=json.loads(args.preparation.read_text()); adapters={name:Path(path) for name,path in prep['adapters'].items()}
                    env.update({'JEV_URL':root+'/v1/systemone','DECIDE_D_URL':root+'/v1/systemone'})
                    source=home/'workflow.py';source.write_text('\n'.join('ordinary source '+str(i)+' '+marker for i in range(601)))
                    transcript=home/'transcript.jsonl';transcript.write_text(json.dumps({'type':'user','message':{'role':'user','content':'Locate the implementation. '+marker}})+'\n')
                    hook={'tool_name':'Read','tool_input':{'file_path':str(source),'custom':{'keep':True}},'transcript_path':str(transcript),'cwd':str(home)}
                    def native():
                        reply=subprocess.run([str(adapters['jev']),'hook','read'],input=json.dumps(hook),env=env,text=True,capture_output=True,timeout=30,check=True)
                        value=json.loads(reply.stdout);assert value['hookSpecificOutput']['updatedInput']['custom']=={'keep':True};return {'narrowed':True,'extra_tool_arguments_preserved':True}
                    record('Pinned native Jev ReadHook',native)
                    # Original bundle and original unchanged selector fixture policy.
                    lines = ['INFO checkout request completed attempt='+str(i)+' cache=healthy duration_ms=20' for i in range(8000)]
                    lines[5777:5777] = ['ERROR checkout initialization failed '+marker, 'Caused by: MissingSigningKeyError: CHECKOUT_SIGNING_KEY was empty',
                                       '    at loadCheckoutConfig (src/config.ts:88:11)', '    at initializeCheckout (src/checkout.ts:41:5)']
                    (home/'application.log').write_text('\n'.join(lines))
                    record('Pinned Codex selector MCP',lambda: {'mode':selector(adapters['codex_bundle'],adapters['node_preload'],args.node,env,home)['mode']})
                    result['mock_upstream_requests']=len(observed)
                    result['mock_upstream_evidence']=observed
                    assert all(request['run_marker_present'] for request in observed)
                    result['passed']=True
            finally:
                for server in (candidate,upstream): server.shutdown();server.server_close()
                flush_traces(3)
                result['owned_servers_closed']=True
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(result,indent=2)+'\n')
                print(json.dumps({'passed':result['passed'],'checks':len(result['checks']),'subscription_calls':0,'output':str(args.output)}))


if __name__ == '__main__':
    main()
