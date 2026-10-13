"""Native adapter preparer and Node preload: process-local faults; no subscription, host config or paid route."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class StandalonePreparation(unittest.TestCase):
    def test_copied_public_tool_runs_from_foreign_directory_without_repo_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            tool = Path(directory) / 'prepare.py'
            shutil.copy2(ROOT / 'scripts/prepare_adapters.py', tool)
            result = subprocess.run([sys.executable, '-I', str(tool), '--help'], cwd=directory,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--asset-root', result.stdout)
            self.assertIn('--prepare-only', result.stdout)

    def test_wheel_asset_mismatch_rejects_before_build_even_under_python_optimization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = root / 'prepare.py'
            shutil.copy2(ROOT / 'scripts/prepare_adapters.py', tool)
            assets = root / 'installed/jev_luna/assets'
            shutil.copytree(ROOT / 'jev_luna/assets', assets)
            wheel = root / 'frozen.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                for path in assets.rglob('*'):
                    if path.is_file():
                        archive.write(path, 'jev_luna/assets/' + path.relative_to(assets).as_posix())
            (assets / 'jev-local.mjs').write_bytes((assets / 'jev-local.mjs').read_bytes() + b'changed')
            work = root / 'native-output'
            result = subprocess.run([sys.executable, '-I', '-O', str(tool), '--asset-root', str(assets),
                                     '--wheel', str(wheel), '--work-dir', str(work), '--upstream-root',
                                     str(root / 'not-needed'), '--prepare-only', '--output', str(root / 'receipt.json')],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Installed asset differs', result.stderr)
            self.assertFalse(work.exists())
            self.assertFalse((root / 'receipt.json').exists())

    def test_standalone_source_match_rejects_one_byte_or_symlink_change(self):
        spec = importlib.util.spec_from_file_location('prepare_adapters', ROOT / 'scripts/prepare_adapters.py')
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'LICENSE').write_text('original')
            profile = {'kind': 'go', 'files': prepare.files(root, 'go')}
            lock = {'profiles': {'native': profile}}
            self.assertEqual(prepare.match(root, 'go', lock)[0], 'native')
            (root / 'LICENSE').write_text('originalx')
            with self.assertRaises(ValueError):
                prepare.match(root, 'go', lock)
            (root / 'LICENSE').unlink()
            (root / 'target').write_text('original')
            (root / 'LICENSE').symlink_to(root / 'target')
            with self.assertRaises(ValueError):
                prepare.match(root, 'go', lock)


@unittest.skipUnless(shutil.which('node'), 'Node is required for the native preload')
class Preload(unittest.TestCase):
    def run_node(self, script):
        with tempfile.TemporaryDirectory(prefix='native-preload-') as directory:
            home = Path(directory)
            (home / 'service.key').write_text('jvl_local_' + 'fixture' * 8)
            (home / 'service.key').chmod(0o600)
            setup = f"""
import assert from 'node:assert/strict';
import {{ writeFileSync, chmodSync, renameSync, unlinkSync, symlinkSync }} from 'node:fs';
process.env.JEV_LUNA_HOME={json.dumps(directory)};
process.env.DECIDE_D_URL='http://127.0.0.1:29999/v1/systemone';
const keyPath=process.env.JEV_LUNA_HOME+'/service.key';
const preload={json.dumps((ROOT / 'jev_luna/assets/jev-local.mjs').as_uri())};
const calls=[];
globalThis.fetch=async(input,options)=>{{calls.push({{input,options}});return new Response('{{}}');}};
await import(preload);
"""
            result = subprocess.run(['node', '--input-type=module', '-e', setup + script],
                                    cwd=ROOT, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_exact_upstream_decision_targets_are_adapted(self):
        self.run_node("""
const body='{"state":{"precise":1234567890123456789012345},"questions":{}}';
for(const url of ['https://api.typesafe.ai/v1/systemone','https://openrouter.ai/api/v1/systemone',
                  'https://ai-gateway.vercel.sh/typesafe/v1/systemone',process.env.DECIDE_D_URL]) {
  await fetch(url,{method:'POST',body,headers:{authorization:'Bearer paid',cookie:'private',host:'remote'}});
  const call=calls.at(-1);
  assert.equal(call.input,process.env.DECIDE_D_URL);
  assert.equal(call.options.body.toString(),body);
  assert.equal(call.options.headers.get('authorization'),'Bearer jvl_local_'+('fixture'.repeat(8)));
  assert.equal(call.options.headers.get('cookie'),null);
  assert.equal(call.options.headers.get('host'),null);
  assert.equal(call.options.redirect,'error');
}
for(const url of ['https://evil.example/v1/systemone',
                  'https://api.typesafe.ai/v1/models','http://127.0.0.1:29999/health']) {
  await fetch(url);assert.equal(calls.at(-1).input,url);assert.equal(new Headers(calls.at(-1).options?.headers).get('authorization'),null);
}
const before=calls.length;
for(const url of ['https://api.typesafe.ai/v1/systemone?x=1','https://api.typesafe.ai/v1/systemone/',
                  'http://127.0.0.1:29999/v1/systemone?x=1','https://api.openai.com/v1/decisions','https://api.openai.com/v1/decisions?x=1',
                  'http://api.typesafe.ai/v1/systemone','https://api.typesafe.ai:444/v1/systemone',
                  'https://api.typesafe.ai/V1/SystemOne','https://api.typesafe.ai/v1/decisions']) {
  await assert.rejects(fetch(url),/Unknown Decision endpoint/);
}
assert.equal(calls.length,before);
""")

    def test_duplicate_import_is_idempotent_and_conflicting_config_is_rejected(self):
        self.run_node("""
const wrapper=globalThis.fetch;
await import(preload+'?duplicate=1');assert.equal(globalThis.fetch,wrapper);
await fetch('https://api.typesafe.ai/v1/systemone',{method:'POST',body:'{}'});
assert.equal(calls.length,1);
process.env.DECIDE_D_URL='http://127.0.0.1:29998/v1/systemone';
await assert.rejects(import(preload+'?conflict=1'),/already installed/);
assert.equal(globalThis.fetch,wrapper);assert.equal(calls.length,1);
process.env.DECIDE_D_URL='http://127.0.0.1:29999/v1/systemone';
process.env.JEV_LUNA_HOME+='/other';
await assert.rejects(import(preload+'?home-conflict=1'),/already installed/);
process.env.JEV_LUNA_HOME=process.env.JEV_LUNA_HOME.slice(0,-6);
globalThis.fetch=async()=>new Response('{}');
await assert.rejects(import(preload+'?fetch-conflict=1'),/already installed/);
""")

    def test_each_call_reopens_local_key_and_rejects_unprotected_or_symlink_key(self):
        self.run_node("""
writeFileSync(keyPath+'.new','jvl_local_'+('rotated'.repeat(8)),{mode:0o600});
renameSync(keyPath+'.new',keyPath);
await fetch('https://api.typesafe.ai/v1/systemone',{method:'POST',body:'{}'});
assert.equal(calls.at(-1).options.headers.get('authorization'),'Bearer jvl_local_'+('rotated'.repeat(8)));
chmodSync(keyPath,0o644);
await assert.rejects(fetch('https://api.typesafe.ai/v1/systemone'),/protected regular/);
unlinkSync(keyPath);writeFileSync(keyPath+'.target','jvl_local_'+('fixture'.repeat(8)),{mode:0o600});
symlinkSync(keyPath+'.target',keyPath);
await assert.rejects(fetch('https://api.typesafe.ai/v1/systemone'),/protected regular/);
assert.equal(calls.length,1);
""")

    def test_caller_abort_and_stalled_request_body_are_bounded(self):
        self.run_node("""
const controller=new AbortController();controller.abort();
await assert.rejects(fetch('https://api.typesafe.ai/v1/systemone',{signal:controller.signal}));
assert.equal(calls.length,0);
const keepAlive=setInterval(()=>{},1000);
const body=new ReadableStream({start(){}});
const before=performance.now();
await assert.rejects(fetch('https://api.typesafe.ai/v1/systemone',{method:'POST',body,duplex:'half',
  headers:{'x-decide-d-timeout-ms':'30'}}));
clearInterval(keepAlive);assert(performance.now()-before<1000);assert.equal(calls.length,0);
const parent=new AbortController();
await fetch('https://api.typesafe.ai/v1/systemone',{method:'POST',body:'{}',signal:parent.signal});
const downstream=calls.at(-1).options.signal;parent.abort();assert(downstream.aborted);
""")

    def test_transport_patch_scope_and_asset_locks_match(self):
        assets = ROOT / 'jev_luna/assets'
        lock = json.loads((assets / 'lock.json').read_text())
        for name, digest in {**lock['patches'], **lock['contracts']}.items():
            self.assertEqual(hashlib.sha256((assets / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(set(lock['profiles']['jev-upstream']['patched_files']),
                         {'internal/typesafe/client.go', 'internal/run/hook.go'})
        self.assertEqual(lock['profiles']['codex-selector']['patched_files'], {})

if __name__ == '__main__':
    unittest.main()
