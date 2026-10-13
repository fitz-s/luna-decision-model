"""Credential-free project guidance for existing SDKs and pinned Jev callers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from .connect import base_url
from .host_config import _paths, _project_lock, _read
from .local import atomic_write, config_home, service_key

TARGETS = ('typesafe', 'openai', 'native-jev', 'codex-selector')
RULE = 'luna-decision-connection.md'


def configuration(target, host='127.0.0.1', port=20141, *, jev_bin=None, selector_bundle=None, node=None, preparation=None):
    if target not in TARGETS:
        raise ValueError('Unknown integration target; choose ' + ', '.join(TARGETS))
    root = base_url(host, port)
    home = str(config_home())
    assets = Path(__file__).parent / 'assets'
    prepared = None
    if preparation:
        if target not in ('native-jev', 'codex-selector'):
            raise ValueError('--preparation belongs to native Jev or selector integrations')
        prepared = _preparation(preparation, assets)
        jev_bin = jev_bin or prepared['adapters']['jev']
        selector_bundle = selector_bundle or prepared['adapters']['codex_bundle']
    result = {'target': target, 'root_url': root, 'credential_file': home + '/service.key',
              'credential_kind': 'Local service credential only; native Codex owns OAuth',
              'start': f'luna-decision start --host {host} --port {port}',
              'doctor': f'luna-decision doctor --url {root}',
              'writes_host_configuration': False}
    if target in ('typesafe', 'openai'):
        endpoint = root + ('/v1' if target == 'openai' else '')
        factory = 'openai_client' if target == 'openai' else 'typesafe_client'
        sdk = 'openai' if target == 'openai' else '@typesafe-ai/sdk'
        klass = 'OpenAI' if target == 'openai' else 'TypeSafeClient'
        js_factory = 'openaiClient' if target == 'openai' else 'typesafeClient'
        result.update({'sdk_base_url': endpoint, 'env': {'JEV_LUNA_HOME': home},
                       'python': f'from luna_decision_model import {factory}; client = {factory}(base_url={endpoint!r})',
                       'python_async': f'from luna_decision_model import async_{factory}; client = async_{factory}(base_url={endpoint!r})',
                       'javascript': f'import {{ {js_factory} }} from {json.dumps((assets / "sdk-local.mjs").resolve().as_uri())};\n'
                                     + (f'import OpenAI from "{sdk}";\n' if target == 'openai' else f'import {{ TypeSafeClient }} from "{sdk}";\n')
                                     + f'const client = {js_factory}({klass}, {{baseURL: {json.dumps(endpoint)}}});',
                       'prerequisites': ['Original Python SDK or original Node SDK installed in your application',
                                         'For TypeSafe: typesafe-sdk; for OpenAI: luna-decision-model[openai]',
                                         'Node SDK factory requires Node with node:fs and native fetch'],
                       'boundary': 'Use this explicit constructor connection. Constructor URLs override SDK environment defaults; a hardcoded remote constructor needs this connection edit.'})
    elif target == 'native-jev':
        binary = _executable(jev_bin) if jev_bin else '<absolute prepared Jev binary>'
        if prepared and _sha(binary) != prepared['profiles']['jev-upstream']['binary_sha256']:
            raise ValueError('Native binary differs from the checked preparation receipt')
        result.update({'env': {'JEV_URL': root + '/v1/systemone', 'JEV_LUNA_HOME': home},
                       'command': binary, 'prerequisites': ['Pinned source prepared with the delivered integration preparer and installed assets; Go is a build prerequisite'],
                       'boundary': 'Use the prepared binary. An upstream plugin that hardcodes its own unpatched bin/jev must be explicitly configured to that binary; changing PATH alone cannot replace an absolute command.'})
    else:
        bundle = _file(selector_bundle) if selector_bundle else '<absolute pinned dist/server.mjs>'
        if prepared and _sha(bundle) != prepared['profiles']['codex-selector']['bundle_sha256']:
            raise ValueError('Selector bundle differs from the checked preparation receipt')
        executable = _executable(node or shutil.which('node')) if node or shutil.which('node') else '<absolute node>'
        result.update({'env': {'DECIDE_D_URL': root + '/v1/systemone', 'JEV_LUNA_HOME': home},
                       'command': executable, 'args': ['--import', str((assets / 'jev-local.mjs').resolve()), bundle],
                       'prerequisites': ['Original pinned selector bundle and Node; HTTP service starts separately'],
                       'boundary': 'Only locked exact System One origins are routed. Native OpenAI Decision URLs fail closed. Unrelated paths are untouched; do not use this preload for the OpenAI SDK.'})
    if prepared:
        result['preparation'] = {'receipt_file': str(Path(preparation).resolve()), 'receipt_sha256': _sha(preparation),
                                 'adapter_sha256': _sha(binary if target == 'native-jev' else bundle),
                                 'asset_lock_sha256': _sha(assets / 'lock.json')}
    return result


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _preparation(path, assets):
    path = _file(path)
    record = _read(Path(path))
    lock = json.loads((assets / 'lock.json').read_text())
    if not record or record.get('passed') is not True or not isinstance(record.get('asset_hashes'), dict):
        raise ValueError('Use a successful preparation receipt for the installed assets')
    if record['asset_hashes'].get('lock.json') != _sha(assets / 'lock.json'):
        raise ValueError('Preparation belongs to a different installed asset lock')
    for name, expected in record['asset_hashes'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not (assets / relative).is_file() or _sha(assets / relative) != expected:
            raise ValueError('Preparation asset identities differ from the installed package')
    for profile in ('jev-upstream', 'codex-selector'):
        if record.get('profiles', {}).get(profile, {}).get('commit') != lock['profiles'][profile]['commit']:
            raise ValueError('Preparation does not select the locked upstream commits')
    if record['profiles']['codex-selector'].get('bundle_sha256') != lock['profiles']['codex-selector']['files']['dist/server.mjs']:
        raise ValueError('Preparation selector bundle is not the locked original artifact')
    if not isinstance(record.get('adapters'), dict):
        raise ValueError('Preparation lacks concrete adapter paths')
    binary = Path(_file(record['adapters'].get('jev', '')))
    bundle = Path(_file(record['adapters'].get('codex_bundle', '')))
    if _sha(binary) != record['profiles']['jev-upstream'].get('binary_sha256') or _sha(bundle) != record['profiles']['codex-selector'].get('bundle_sha256'):
        raise ValueError('Prepared binary or bundle changed; rerun the public preparer')
    roots = {'jev-upstream': binary.parent / 'jev-upstream', 'codex-selector': bundle.parent.parent}
    for name, source in roots.items():
        if source.is_symlink() or not source.is_dir():
            raise ValueError('Retain the regular prepared source directory; rerun the public preparer')
        profile = lock['profiles'][name]
        evidence = record['profiles'][name]
        if evidence.get('source_files') != profile['files'] or set(evidence.get('changed_files', [])) != set(profile['patched_files']):
            raise ValueError('Preparation source/patch identities differ from the installed lock')
        expected = {**profile['files'], **profile['patched_files']}
        if name == 'jev-upstream':
            expected.update({'internal/typesafe/jev_luna_local_test.go': lock['contracts']['contracts/local_test.go'],
                             'internal/run/jev_luna_hook_test.go': lock['contracts']['contracts/hook_test.go']})
        for relative, expected_sha in expected.items():
            candidate = source / relative
            if any(parent.is_symlink() for parent in [candidate, *candidate.parents[:len(Path(relative).parts)]]):
                raise ValueError('Prepared source must not contain symlinks')
            if not candidate.is_file() or _sha(candidate) != expected_sha:
                raise ValueError('Actual prepared source differs from locked patch output; rerun the public preparer')
        selected = {}
        for candidate in source.rglob('*'):
            relative = candidate.relative_to(source)
            if any(part in ('.git', 'node_modules') for part in relative.parts): continue
            used = (candidate.suffix == '.go' or candidate.name in ('go.mod', 'go.sum', 'LICENSE')) if name == 'jev-upstream' else (
                relative.parts[0] in ('src', 'dist') or candidate.name in ('package.json', 'package-lock.json', 'LICENSE'))
            if used and candidate.is_symlink():
                raise ValueError('Prepared source must not contain symlinks')
            if used and candidate.is_file(): selected[relative.as_posix()] = _sha(candidate)
        if selected != expected:
            raise ValueError('Unknown prepared source inputs; rerun the public preparer')
    return record


def _file(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Integration paths must be existing absolute regular files without symlinks')
    return str(path)


def _executable(value):
    import os
    if not Path(value).is_absolute():
        raise ValueError('Integration command must be an absolute path')
    path = _file(str(Path(value).resolve()))
    if not os.access(path, os.X_OK):
        raise ValueError('Integration command must be executable')
    return path


def _rule(settings):
    return ('# Local Decision connection for this project\n\n'
            'Use the existing TypeSafe/OpenAI typed Decision workflow when semantic judgment is needed. '
            'Keep exact rules, retrieval, actions, thresholds, retry limits and original skill policy in application code. '
            'This connection does not request a model call for every event.\n\n'
            'When writing or connecting code with the official TypeSafe development skill, use the local SDK constructor below. '
            'The vendor plugin is guidance, not a running backend. Start the local service separately. '
            'OpenAI SDK base URL includes /v1; TypeSafe SDK uses the root; native adapters use the full /v1/systemone path. '
            'Read the protected service.key at runtime through the supplied factory. Never paste its contents or OAuth tokens into notes, code, or host settings. '
            'No paid TypeSafe/OpenAI API key is needed for this local connection.\n\n'
            'On connection/authentication errors, report the error and inspect doctor for this target. '
            'Do not silently use a paid endpoint, accept selector local-fallback as model success, or add a different model. '
            'Preserve the original caller recovery policy. This guidance does not override the existing skill business policy.\n\n'
            '```json\n' + json.dumps(settings, ensure_ascii=False, indent=2) + '\n```\n')


def project_configuration(project, settings, *, action='dry-run'):
    if action not in ('dry-run', 'write', 'check', 'restore'):
        raise ValueError('Unknown integration configuration action')
    root = _paths(project)[0].parent
    directory = root / '.claude' / 'rules'
    for parent in (root / '.claude', directory):
        if parent.is_symlink() or parent.exists() and not parent.is_dir():
            raise ValueError('Project guidance directory must not be a symlink or file')
    rule = directory / RULE
    journal = root / '.luna-decision-connect.json'
    lock = root / '.luna-decision-connect.lock'
    text = _rule(settings)
    result = {'status': 'dry-run', 'target': settings['target'], 'rule_file': str(rule), 'journal_file': str(journal),
              'lock_file': str(lock), 'configuration': settings,
              'writes': [str(rule), str(journal), str(lock)] if action in ('dry-run', 'write') else [str(lock)],
              'creates_directories': [str(root / '.claude'), str(directory)] if action in ('dry-run', 'write') else [],
              'removes': [str(rule), str(journal)] if action == 'restore' else [],
              'retained_after_restore': [str(lock), str(root / '.claude'), str(directory)],
              'effects_are_planned': action == 'dry-run', 'model_calls': 0}
    if action == 'dry-run':
        return result
    if action in ('write', 'check') and settings['target'] in ('native-jev', 'codex-selector') and 'preparation' not in settings:
        raise ValueError('Use --preparation to bind the prepared binary/bundle before writing or checking this connection')
    if action in ('write', 'check') and settings['target'] == 'codex-selector' and settings['command'].startswith('<'):
        raise ValueError('Install Node or supply its absolute interpreter with --node')
    with _project_lock(lock):
        record = _read(journal)
        if rule.is_symlink() or rule.exists() and not rule.is_file():
            raise ValueError('Project connection rule must be a regular file without symlinks')
        if rule.exists():
            info = rule.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024*1024 or info.st_nlink != 1
                    or hasattr(os, 'getuid') and info.st_uid != os.getuid()):
                raise ValueError('Project connection rule must be a bounded single-link file owned by this user')
        current = rule.read_text() if rule.exists() else None
        if record is not None and (set(record) != {'version', 'target', 'rule_sha256', 'configuration'} or record['version'] != 1):
            raise ValueError('Unknown connection journal; project files were preserved')
        if action in ('restore', 'check'):
            if record is None:
                if action == 'restore': return {**result, 'status': 'already_restored'}
                raise ValueError('No managed project connection exists')
            if current is None or hashlib.sha256(current.encode()).hexdigest() != record['rule_sha256']:
                raise ValueError('Project connection rule changed; existing user work was preserved')
            if action == 'restore':
                rule.unlink()
                journal.unlink()
                return {**result, 'status': 'restored'}
            if record['configuration'] != settings:
                raise ValueError('Project connection differs from the requested target or settings')
            service_key()  # Local-only credential validation; never login, HTTP or inference.
            return {**result, 'status': 'verified', 'credential_available': True}
        if record is not None:
            if record['configuration'] == settings and current == text:
                return {**result, 'status': 'already_configured'}
            raise ValueError('A project connection already exists with different settings; restore it before changing target')
        if current is not None:
            raise ValueError('A user connection rule already exists; no file was overwritten')
        directory.mkdir(parents=True, exist_ok=True)
        record = {'version': 1, 'target': settings['target'], 'rule_sha256': hashlib.sha256(text.encode()).hexdigest(), 'configuration': settings}
        atomic_write(journal, json.dumps(record, ensure_ascii=False, indent=2)+'\n')
        try:
            atomic_write(rule, text)
        except BaseException:
            journal.unlink()
            raise
        return {**result, 'status': 'configured'}
