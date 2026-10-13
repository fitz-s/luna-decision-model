import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jev_luna.integration_config import configuration, project_configuration
from jev_luna.local import service_key


class IntegrationConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {'JEV_LUNA_HOME': str(self.root / 'state')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temporary.cleanup)

    def test_default_dry_run_creates_nothing_and_carries_no_credential(self):
        settings = configuration('typesafe', port=20491)
        result = project_configuration(self.root, settings)
        self.assertEqual(result['status'], 'dry-run')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(result['effects_are_planned'])
        self.assertEqual(set(result['writes']), {str(self.root/'.claude/rules/luna-decision-connection.md'),
                                               str(self.root/'.luna-decision-connect.json'), str(self.root/'.luna-decision-connect.lock')})
        self.assertEqual(set(result['creates_directories']), {str(self.root/'.claude'), str(self.root/'.claude/rules')})
        self.assertIn('typesafe_client', settings['python'])
        self.assertEqual(settings['sdk_base_url'], 'http://127.0.0.1:20491')
        self.assertNotIn('api_key', json.dumps(result))
        written=project_configuration(self.root,settings,action='write')
        actual_files={str(p) for p in self.root.rglob('*') if p.is_file()}
        actual_directories={str(p) for p in self.root.rglob('*') if p.is_dir()}
        self.assertEqual(actual_files,set(written['writes']))
        self.assertEqual(actual_directories,set(written['creates_directories']))

    def test_write_check_duplicate_conflict_restore_preserves_user_settings(self):
        claude = self.root / '.claude'; claude.mkdir()
        user_settings = claude / 'settings.local.json'
        raw = '{"env":{"USER_SETTING":"keep"},"hooks":{"Read":["original"]}}\n'
        user_settings.write_text(raw)
        key = service_key(create=True)
        settings = configuration('openai', port=20491)
        self.assertEqual(project_configuration(self.root, settings, action='write')['status'], 'configured')
        lock = self.root/'.luna-decision-connect.lock'
        self.assertTrue(lock.is_file() and not lock.is_symlink())
        self.assertEqual(lock.read_bytes(), b'')
        self.assertEqual(project_configuration(self.root, settings, action='write')['status'], 'already_configured')
        self.assertEqual(project_configuration(self.root, settings, action='check')['status'], 'verified')
        self.assertTrue(lock.is_file() and not lock.is_symlink())
        self.assertNotIn(key.encode(), lock.read_bytes())
        self.assertNotIn(key, (self.root / '.luna-decision-connect.json').read_text())
        self.assertNotIn(key, (claude / 'rules/luna-decision-connection.md').read_text())
        with self.assertRaisesRegex(ValueError, 'different settings'):
            project_configuration(self.root, configuration('typesafe', port=20491), action='write')
        restored = project_configuration(self.root, settings, action='restore')
        self.assertEqual(restored['status'], 'restored')
        self.assertEqual(restored['writes'], [str(lock)])
        self.assertEqual(set(restored['removes']), {str(self.root/'.claude/rules/luna-decision-connection.md'), str(self.root/'.luna-decision-connect.json')})
        self.assertTrue(lock.is_file() and not lock.is_symlink())
        self.assertEqual(lock.read_bytes(), b'')
        self.assertEqual(list((claude/'rules').iterdir()), [])
        self.assertEqual(project_configuration(self.root, settings, action='restore')['status'], 'already_restored')
        self.assertEqual(user_settings.read_text(), raw)

    def test_check_and_restore_can_create_retained_lock_without_touching_user_files(self):
        import shutil
        service_key(create=True)
        settings=configuration('typesafe')
        project_configuration(self.root,settings,action='write')
        for action in ('check','restore'):
            project=self.root/action;rules=project/'.claude/rules';rules.mkdir(parents=True)
            shutil.copyfile(self.root/'.luna-decision-connect.json',project/'.luna-decision-connect.json')
            shutil.copyfile(self.root/'.claude/rules/luna-decision-connection.md',rules/'luna-decision-connection.md')
            user=project/'.claude/settings.local.json';user.write_text('{"user":"keep"}\n')
            before={p.relative_to(project) for p in project.rglob('*') if p.is_file()}
            result=project_configuration(project,settings,action=action)
            after={p.relative_to(project) for p in project.rglob('*') if p.is_file()}
            declared={Path(p).relative_to(project) for p in result['writes']}
            removed={Path(p).relative_to(project) for p in result['removes']}
            self.assertEqual(after,(before|declared)-removed)
            lock=project/'.luna-decision-connect.lock'
            self.assertTrue(lock.is_file() and not lock.is_symlink())
            self.assertEqual(lock.read_bytes(),b'')
            self.assertEqual(user.read_text(),'{"user":"keep"}\n')
            if action=='restore': self.assertEqual(list(rules.iterdir()),[])

    def test_restore_refuses_user_edited_rule(self):
        settings = configuration('typesafe')
        project_configuration(self.root, settings, action='write')
        rule = self.root / '.claude/rules/luna-decision-connection.md'
        rule.write_text(rule.read_text()+'user edit\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            project_configuration(self.root, settings, action='restore')
        self.assertTrue(rule.read_text().endswith('user edit\n'))

    def test_user_rule_and_linked_directory_not_overwritten(self):
        outside = self.root / 'outside'; outside.mkdir()
        (self.root / '.claude').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            project_configuration(self.root, configuration('typesafe'), action='write')
        self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_journal_refused(self):
        (self.root / '.luna-decision-connect.json').write_text('{"user":"keep"}')
        with self.assertRaisesRegex(ValueError, 'Unknown connection journal'):
            project_configuration(self.root, configuration('typesafe'), action='write')

    def test_unknown_target_and_remote_url_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown integration'):
            configuration('all-skills')
        with self.assertRaises(ValueError):
            configuration('typesafe', host='api.typesafe.ai')
        with self.assertRaises(ValueError):
            configuration('openai', port=70000)

    def test_native_requires_real_prepared_command_and_exact_endpoint(self):
        settings = configuration('native-jev', port=20491)
        self.assertEqual(settings['env']['JEV_URL'], 'http://127.0.0.1:20491/v1/systemone')
        with self.assertRaisesRegex(ValueError, '--preparation'):
            project_configuration(self.root, settings, action='write')

    def test_typesafe_factory_preserves_policy_options_and_rejects_remote_or_key_override(self):
        try:
            import typesafe_sdk
        except ImportError:
            self.skipTest('TypeSafe extra is exercised by the installed-artifact contract tool')
        from jev_luna.connect import typesafe_client, async_typesafe_client
        key = service_key(create=True)
        custom = object(); retry = object()
        with patch.object(typesafe_sdk, 'TypeSafeClient') as factory:
            typesafe_client(base_url='http://127.0.0.1:20491', timeout=7, retry=retry, http_client=custom)
            self.assertEqual(factory.call_args.kwargs, {'base_url':'http://127.0.0.1:20491','api_key':key,'timeout':7,'retry':retry,'http_client':custom})
        with patch.object(typesafe_sdk, 'AsyncTypeSafeClient') as factory:
            async_typesafe_client(base_url='http://127.0.0.1:20491', timeout=8, retry=retry, http_client=custom)
            self.assertIs(factory.call_args.kwargs['http_client'], custom)
            self.assertEqual(factory.call_args.kwargs['timeout'], 8)
        for options in ({'base_url':'https://api.typesafe.ai'}, {'api_key':'native-paid-key'}):
            with self.assertRaises(ValueError): typesafe_client(**options)

    def test_js_factory_rejects_remote_and_missing_key_without_constructing_sdk(self):
        import shutil
        node = shutil.which('node')
        if not node: self.skipTest('Node is an advanced integration prerequisite')
        helper = (Path(__file__).resolve().parents[1]/'jev_luna/assets/sdk-local.mjs').as_uri()
        code = 'import {typesafeClient,openaiClient} from '+json.dumps(helper)+';let calls=0;class SDK{constructor(){calls++;}}'
        code += "for(const create of [()=>typesafeClient(SDK,{baseURL:'https://api.typesafe.ai'}),()=>openaiClient(SDK,{baseURL:'http://127.0.0.1:20491/v1'})]){try{create();throw new Error('unexpected success');}catch(error){if(error.message==='unexpected success')throw error;}}if(calls)throw new Error('credential or URL guard bypassed');console.log('passed');"
        run = subprocess.run([node,'--input-type=module','-e',code],env=os.environ,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.strip(),'passed')

    def test_preparation_checks_actual_locked_source_binary_and_links(self):
        from jev_luna.integration_config import _preparation
        assets=self.root/'assets';assets.mkdir();stage=self.root/'stage';stage.mkdir()
        source=stage/'jev-upstream';selector=stage/'codex-selector'
        blobs={'internal/typesafe/client.go':b'local patched transport',
               'internal/typesafe/jev_luna_local_test.go':b'contract',
               'internal/run/jev_luna_hook_test.go':b'hook contract'}
        for name,data in blobs.items():
            path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        bundle=selector/'dist/server.mjs';bundle.parent.mkdir(parents=True);bundle.write_bytes(b'original selector')
        binary=stage/'jev';binary.write_bytes(b'compiled local transport')
        sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
        original={'internal/typesafe/client.go':'original-input-sha'};patched={'internal/typesafe/client.go':sha(source/'internal/typesafe/client.go')}
        lock={'profiles':{'jev-upstream':{'commit':'go-commit','files':original,'patched_files':patched},
                          'codex-selector':{'commit':'js-commit','files':{'dist/server.mjs':sha(bundle)},'patched_files':{}}},
              'contracts':{'contracts/local_test.go':sha(source/'internal/typesafe/jev_luna_local_test.go'),
                           'contracts/hook_test.go':sha(source/'internal/run/jev_luna_hook_test.go')}}
        (assets/'lock.json').write_text(json.dumps(lock))
        record={'passed':True,'asset_hashes':{'lock.json':sha(assets/'lock.json')},
                'profiles':{'jev-upstream':{'commit':'go-commit','source_files':original,'changed_files':list(patched),'binary_sha256':sha(binary)},
                            'codex-selector':{'commit':'js-commit','source_files':lock['profiles']['codex-selector']['files'],'changed_files':[],'bundle_sha256':sha(bundle)}},
                'adapters':{'jev':str(binary),'codex_bundle':str(bundle)}}
        receipt=self.root/'prep.json';receipt.write_text(json.dumps(record))
        self.assertTrue(_preparation(receipt,assets)['passed'])
        binary.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'binary or bundle changed'): _preparation(receipt,assets)
        binary.write_bytes(b'compiled local transport')
        (source/'internal/typesafe/client.go').write_bytes(b'original paid transport')
        with self.assertRaisesRegex(ValueError,'Actual prepared source'): _preparation(receipt,assets)
        (source/'internal/typesafe/client.go').write_bytes(blobs['internal/typesafe/client.go'])
        extra=source/'injected.go';extra.write_text('unknown source')
        with self.assertRaisesRegex(ValueError,'Unknown prepared source'): _preparation(receipt,assets)
        extra.unlink()
        record['profiles']['jev-upstream']['commit']='unknown-commit';receipt.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'locked upstream commits'): _preparation(receipt,assets)
        record['profiles']['jev-upstream']['commit']='go-commit';receipt.write_text(json.dumps(record))
        linked=self.root/'linked.json';linked.symlink_to(receipt)
        with self.assertRaisesRegex(ValueError,'symlinks'): _preparation(linked,assets)

    def test_cli_dry_run_unknown_target_and_legacy_configuration(self):
        from jev_luna import connect
        legacy = connect.configuration('127.0.0.1', 20491)
        self.assertEqual(legacy['mcp']['env']['DECIDE_D_URL'], 'http://127.0.0.1:20491/v1/systemone')
        run = subprocess.run([sys.executable, '-m', 'luna_decision_model', 'config', '--integration', 'typesafe',
                              '--port', '20491', '--project', str(self.root)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'dry-run')
        self.assertEqual(list(self.root.iterdir()), [])
        run = subprocess.run([sys.executable, '-m', 'luna_decision_model', 'config', '--integration', 'all-skills'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)


if __name__ == '__main__':
    unittest.main()
