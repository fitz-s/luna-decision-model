"""Build the pinned native adapters (Jev CLI/Read Hook, Codex selector) and write a preparation receipt.

With --prepare-only it only builds; otherwise it also exercises local fault paths against a mock
upstream (no OAuth, no subscription calls). Pass the receipt to
`luna-decision config --integration native-jev|codex-selector --preparation <receipt>`.
The script is standalone: copy it anywhere and point --asset-root at the installed jev_luna/assets.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def files(source, kind):
    result = {}
    for path in source.rglob('*'):
        relative = path.relative_to(source)
        if any(part in ('.git', 'node_modules') for part in relative.parts):
            continue
        selected = (path.suffix == '.go' or path.name in ('go.mod', 'go.sum', 'LICENSE')) if kind == 'go' else (
            relative.parts[0] in ('src', 'dist') or path.name in ('package.json', 'package-lock.json', 'LICENSE'))
        if selected and path.is_symlink():
            raise ValueError('Linked integration source inputs are not supported')
        if selected and path.is_file():
            result[relative.as_posix()] = sha(path)
    return result


def match(source, kind, lock):
    actual = files(source, kind)
    candidates = [(name, profile) for name, profile in lock['profiles'].items()
                  if profile['kind'] == kind and actual == profile['files']]
    if len(candidates) != 1:
        raise ValueError('Unknown or modified source; use the exact commit in the supplied asset lock.json')
    return candidates[0]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, **options):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120, **options)
    if result.returncode:
        raise RuntimeError(f'{command[0]} exited {result.returncode}: {result.stderr[-5000:]}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-root', type=Path, required=True)
    parser.add_argument('--asset-root', type=Path, default=ROOT / 'jev_luna/assets',
                        help='Installed jev_luna/assets directory; no checkout assets are read when supplied')
    parser.add_argument('--work-dir', type=Path, default=ROOT / '.runtime/adapters')
    parser.add_argument('--wheel', type=Path, help='Verify all supplied assets against this frozen wheel')
    parser.add_argument('--prepare-only', action='store_true', help='Build the optional adapters without mock or live HTTP calls')
    parser.add_argument('--output', type=Path, required=True, help='New receipt path (never overwritten)')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError('Preserve the prior receipt; choose a new output path')
    assets = args.asset_root.resolve()
    private = args.work_dir.resolve()
    lock = json.loads((assets / 'lock.json').read_text())
    asset_hashes = {name: sha(assets / name) for name in
                    ['lock.json', 'jev-local.mjs', 'NOTICE.md', *lock['patches'], *lock['contracts']]}
    if args.wheel:
        with zipfile.ZipFile(args.wheel) as wheel:
            for name, digest in asset_hashes.items():
                if hashlib.sha256(wheel.read('jev_luna/assets/' + name)).hexdigest() != digest:
                    raise ValueError('Installed asset differs from the supplied wheel: ' + name)
    for name, digest in {**lock['patches'], **lock['contracts']}.items():
        if sha(assets / name) != digest:
            raise ValueError('Integration resource differs from its lock: ' + name)
    private.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='native-', dir=private))
    receipt = {'scope': 'offline mock transport only; no subscription acceptance',
               'platform': platform.platform(), 'python': platform.python_version(),
               'logical_subscription_calls': 0, 'stage': str(stage), 'profiles': {}, 'rows': [],
               'preload_sha256': sha(assets / 'jev-local.mjs'), 'tool_sha256': sha(Path(__file__)),
               'asset_root': str(assets), 'asset_hashes': asset_hashes,
               'wheel_sha256': sha(args.wheel) if args.wheel else None,
               'mode': 'prepare-only' if args.prepare_only else 'offline-mock'}
    for source_name, kind in [('jev-cli', 'go'), ('codex-selector', 'node')]:
        source = args.upstream_root / source_name
        name, profile = match(source, kind, lock)
        destination = stage / name
        for relative in profile['files']:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        if files(destination, kind) != profile['files']:
            raise ValueError('Source changed while staging; no build was attempted')
        for patch_name in profile['patches']:
            patch = assets / patch_name
            if patch.stat().st_size:
                run(['git', 'apply', '--check', str(patch)], cwd=destination)
                run(['git', 'apply', str(patch)], cwd=destination)
        expected = {**profile['files'], **profile.get('patched_files', {})}
        if files(destination, kind) != expected:
            raise ValueError('Patch result differs from its locked output')
        changed = [relative for relative in profile['files'] if sha(destination / relative) != profile['files'][relative]]
        entry = {'commit': profile['commit'], 'repository': profile['repository'],
                 'changed_files': changed, 'source_files': profile['files'], 'checks': []}
        receipt['profiles'][name] = entry
        if kind == 'go':
            for contract, target in [('local_test.go', 'internal/typesafe/jev_luna_local_test.go'),
                                     ('hook_test.go', 'internal/run/jev_luna_hook_test.go')]:
                shutil.copy2(assets / 'contracts' / contract, destination / target)
                expected[target] = lock['contracts']['contracts/' + contract]
            for command in [['go', 'test', '-count=1', './...'], ['go', 'vet', './...'],
                            ['go', 'build', '-o', str(stage / 'jev'), './cmd/jev']]:
                result = run(command, cwd=destination)
                entry['checks'].append({'command': command, 'exit': result.returncode,
                                        'stdout': result.stdout, 'stderr': result.stderr})
            entry['binary_sha256'] = sha(stage / 'jev')
            entry['host_sources'] = {name: sha(source / name) for name in
                                    ['hooks/hooks.json', '.claude-plugin/plugin.json', 'skills/jev/SKILL.md', 'Makefile']}
            for relative, digest in entry['host_sources'].items():
                original = subprocess.check_output(['git', 'show', profile['commit'] + ':' + relative], cwd=source)
                if hashlib.sha256(original).hexdigest() != digest:
                    raise ValueError('Host source differs from pinned commit: ' + relative)
            entry['host_sources_verified_at_commit'] = profile['commit']
            entry['toolchain'] = run(['go', 'version']).stdout.strip()
        else:
            entry['bundle_sha256'] = sha(destination / 'dist/server.mjs')
            entry['toolchain'] = run(['node', '--version']).stdout.strip()
        if files(destination, kind) != expected:
            raise ValueError('Build changed locked source bytes; do not use the output')
    receipt['adapters'] = {'jev': str(stage / 'jev'),
                           'codex_bundle': str(stage / 'codex-selector/dist/server.mjs'),
                           'node_preload': str(assets / 'jev-local.mjs')}
    if args.prepare_only:
        receipt['scope'] = 'Pinned native adapter preparation only; no HTTP or subscription calls'
        receipt['passed'] = True
        receipt['completed_at_unix'] = time.time()
        with args.output.open('x') as output:
            json.dump(receipt, output, indent=2)
            output.write('\n')
        print(json.dumps({'passed': True, 'output': str(args.output), 'adapters': receipt['adapters'],
                          'logical_subscription_calls': 0}))
        return
    home = stage / 'home'
    home.mkdir(mode=0o700)
    key = 'jvl_local_' + uuid.uuid4().hex + uuid.uuid4().hex
    (home / 'service.key').write_text(key)
    (home / 'service.key').chmod(0o600)
    observed = []
    fault = {'mode': 'success'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(body)
            observed.append({'path': self.path, 'payload_sha256': hashlib.sha256(body).hexdigest(),
                             'model_label': payload.get('model'), 'questions': list(payload['questions']),
                             'timeout_ms': self.headers.get('X-Decide-D-Timeout-Ms'),
                             'credential_is_local': self.headers.get('Authorization') == 'Bearer ' + key})
            if fault['mode'] in ('401', '422'):
                self.send_response(int(fault['mode']))
                self.end_headers()
                self.wfile.write(b'{"error":{"code":"offline_fault"}}')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            if fault['mode'] == 'malformed':
                self.wfile.write(b'not-json')
                return
            answers = {}
            for name, question in payload['questions'].items():
                if question['type'] == 'noul':
                    answers[name] = {'type': 'noul', 'noul': .9}
                else:
                    criteria = question['criteria']
                    choices = list(criteria) if isinstance(criteria, dict) else [str(i) for i in range(len(criteria))]
                    answers[name] = {'type': question['type'], 'choice': choices[len(choices)//2],
                                     'confidence': .95, 'score': 1, 'probabilities': {choice: 1/len(choices) for choice in choices}}
            self.wfile.write(json.dumps({'model': 'gpt-6-luna', 'answers': answers,
                                        'usage': {'input_tokens': 1, 'output_tokens': 1}}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_port}/v1/systemone'
    env = {**os.environ, 'JEV_LUNA_HOME': str(home), 'JEV_URL': endpoint,
           'DECIDE_D_URL': endpoint, 'JEV_HOOK_DEBUG': '1'}
    for variable in ['TYPE_SAFE_AI_KEY', 'TYPESAFE_API_KEY', 'TYPESAFE_AI_API_KEY',
                     'JEV_HOOK_DISABLE', 'JEV_HOOK_MIN_LINES', 'JEV_HOOK_MIN_CONF']:
        env.pop(variable, None)
    target = stage / ('source-' + uuid.uuid4().hex + '.txt')
    target.write_text('\n'.join('ordinary source line ' + str(i) for i in range(601)))
    transcript = stage / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'Find the implementation that handles a supplied task.'}}) + '\n')
    payload = {'tool_name': 'Read', 'tool_input': {'file_path': str(target), 'pages': '2', 'custom': {'keep': True}},
               'transcript_path': str(transcript), 'cwd': str(stage)}
    try:
        for mode in ['success', '401', '422', 'malformed']:
            fault['mode'] = mode
            before = len(observed)
            result = run([str(stage / 'jev'), 'hook', 'read'], env=env, input=json.dumps(payload))
            output = json.loads(result.stdout)
            assert len(observed) == before + 1
            if mode == 'success':
                nested = output['hookSpecificOutput']
                assert nested['hookEventName'] == 'PreToolUse' and nested['additionalContext']
                assert nested['updatedInput']['pages'] == '2' and nested['updatedInput']['custom'] == {'keep': True}
            else:
                assert output == {} and 'passing through' in result.stderr
            receipt['rows'].append({'entry': 'native ReadHook', 'fault': mode, 'exit': result.returncode,
                                    'stdout': output, 'stderr': result.stderr, 'transport': observed[-1]})
        for mode, raw, overrides in [('disabled', json.dumps(payload), {'JEV_HOOK_DISABLE': '1'}),
                                      ('unreadable_stdin', '{', {}),
                                      ('explicit_window', json.dumps({**payload, 'tool_input': {**payload['tool_input'], 'offset': 1}}), {})]:
            before = len(observed)
            result = run([str(stage / 'jev'), 'hook', 'read'], env={**env, **overrides}, input=raw)
            assert json.loads(result.stdout) == {} and len(observed) == before
            receipt['rows'].append({'entry': 'native ReadHook', 'fault': mode, 'exit': 0,
                                    'stdout': {}, 'remote_requests': 0})
        # The original selector runs its real HTTP branch, then exposes fallback
        # on malformed transport. This is still a mock-server offline check.
        selector = stage / 'codex-selector/src/investigator.mjs'
        js = f"""
import {{ rankWithJev }} from {json.dumps(selector.as_uri())};
const candidates=Array.from({{length:4}},(_,i)=>({{path:'candidate-'+i+'.txt',lines:{{start:1,end:3}},excerpt:'ordinary candidate '+i,localScore:i}}));
const result=await rankWithJev('supplied task',[],candidates,{{resultLimit:2}});
console.log(JSON.stringify({{mode:result.mode,requests:result.requests,model:result.model,selected:result.selected.length}}));
"""
        fault['mode'] = 'success'
        before = len(observed)
        result = run(['node', '--import', str(assets / 'jev-local.mjs'), '--input-type=module', '-e', js], env=env)
        parsed = json.loads(result.stdout)
        assert parsed['mode'] == 'jev' and parsed['requests'] == 1 and len(observed) == before + 1
        receipt['rows'].append({'entry': 'pinned Codex selector HTTP branch', 'response': parsed, 'transport': observed[-1]})
        # Exercise the shipped, unchanged MCP bundle over real newline JSON-RPC.
        log = stage / 'application.log'
        log.write_text('\n'.join('ERROR task context operation ' + str(i) + ' ' + 'ordinary detail ' * 12 for i in range(1200)))
        responses = queue.Queue()
        stdout_records = []
        process = subprocess.Popen(['node', '--import', str(assets / 'jev-local.mjs'),
                                    str(stage / 'codex-selector/dist/server.mjs')], env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def collect():
            for line in process.stdout:
                stdout_records.append(line)
                try:
                    responses.put(json.loads(line))
                except json.JSONDecodeError:
                    responses.put({'invalid_stdout': line})
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        def rpc(identity, method, parameters):
            process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': parameters}) + '\n')
            process.stdin.flush()
            limit = time.monotonic() + 20
            while time.monotonic() < limit:
                response = responses.get(timeout=max(.01, limit - time.monotonic()))
                assert 'invalid_stdout' not in response, response
                if response.get('id') == identity:
                    assert 'error' not in response, response
                    return response['result']
            raise TimeoutError('MCP response deadline')
        try:
            initialized = rpc(1, 'initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'adapter-offline', 'version': '1'}})
            process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) + '\n')
            process.stdin.flush()
            tools = rpc(2, 'tools/list', {})
            assert {tool['name'] for tool in tools['tools']} == {'read_large_text_evidence', 'read_selected_evidence', 'search_workspace_evidence'}
            for identity, mode in [(3, 'success'), (4, 'malformed'), (5, 'success')]:
                fault['mode'] = mode
                before = len(observed)
                reply = rpc(identity, 'tools/call', {'name': 'read_large_text_evidence', 'arguments': {
                    'workspaceRoot': str(stage), 'path': log.name, 'query': 'task context operation',
                    'requirements': ['Identify operation context'], 'includeDiagnostics': True}})
                assert not reply.get('isError'), reply
                value = reply.get('structuredContent') or json.loads(reply['content'][0]['text'])
                assert len(observed) == before + 1
                assert value['mode'] == ('local-fallback' if mode == 'malformed' else 'jev'), value
                if mode == 'malformed':
                    assert value['warnings'] and value['diagnostics']['fallbackReason']
                receipt['rows'].append({'entry': 'pinned Codex selector MCP stdio', 'fault': mode,
                                        'mode': value['mode'], 'metrics': value['metrics'],
                                        'diagnostics': value['diagnostics'], 'transport': observed[-1]})
            receipt['mcp_server_info'] = initialized['serverInfo']
            receipt['mcp_tools'] = sorted(tool['name'] for tool in tools['tools'])
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
            reader.join(timeout=3)
            receipt['mcp_process_stopped'] = process.poll() is not None
            receipt['mcp_stdout_jsonrpc_only'] = all(json.loads(line).get('jsonrpc') == '2.0' for line in stdout_records)
            assert receipt['mcp_stdout_jsonrpc_only']
            receipt['mcp_stdout_lines'] = len(stdout_records)
        assert all(row['path'] == '/v1/systemone' and row['credential_is_local'] for row in observed)
        assert all(0 < int(row['timeout_ms']) <= 120000 for row in observed)
        receipt['request_count'] = len(observed)
        receipt['passed'] = True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        receipt['local_server_stopped'] = not thread.is_alive()
        receipt['completed_at_unix'] = time.time()
        with args.output.open('x') as output:
            json.dump(receipt, output, indent=2)
            output.write('\n')
    print(json.dumps({'passed': receipt['passed'], 'output': str(args.output),
                      'logical_subscription_calls': 0, 'request_count': receipt['request_count']}))


if __name__ == '__main__':
    main()
