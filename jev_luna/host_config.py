"""Explicit project MCP configuration; no global host edits or credential copies."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import time

from .connect import configuration
from .local import atomic_write
from .validation import loads

NAME = 'luna-decision'
LEGACY_NAME = 'jev-decisions'


@contextmanager
def _project_lock(path):
    if path.is_symlink():
        raise ValueError('Project lock must not be a symlink')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or hasattr(os, 'getuid') and info.st_uid != os.getuid()):
            raise ValueError('Project lock must be a single-link regular file owned by this user')
        if os.name == 'nt':
            import msvcrt
            def acquire(): msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            def release(): msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def acquire(): fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release(): fcntl.flock(descriptor, fcntl.LOCK_UN)
        deadline = time.monotonic()+5
        while True:
            try:
                acquire()
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('Project configuration is locked; existing files were preserved') from None
                time.sleep(.05)
        yield
    finally:
        if locked: release()
        os.close(descriptor)


def _read(path):
    if path.is_symlink():
        raise ValueError('Project configuration must not be a symlink')
    if not path.exists():
        return None
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024*1024
            or hasattr(os, 'getuid') and info.st_uid != os.getuid()):
        raise ValueError('Project configuration must be a bounded regular file owned by this user')
    raw = path.read_bytes()
    result = loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Project configuration must be a JSON object')
    return result


def _paths(project):
    project = Path(project).expanduser()
    if not project.is_absolute() or project.is_symlink() or not project.is_dir():
        raise ValueError('Choose an existing absolute project directory, without a symlink')
    if hasattr(os, 'getuid') and project.stat().st_uid != os.getuid():
        raise ValueError('Project directory must be owned by this user')
    return project / '.mcp.json', project / '.luna-decision-mcp.json'


def configure_mcp(project, host='127.0.0.1', port=20141, *, restore=False):
    path, journal = _paths(project)
    entry = configuration(host, port)['mcp']
    with _project_lock(path.parent / '.luna-decision-mcp.lock'):
        original = _read(path)
        record = _read(journal)
        config = original if original is not None else {}
        servers = config.get('mcpServers', {})
        if not isinstance(servers, dict):
            raise ValueError('Project mcpServers must be a JSON object')
        if restore:
            if record is None:
                return {'status': 'already_restored', 'path': str(path)}
            if (set(record) != {'version', 'entry_name', 'entry', 'created_file'}
                    or record['version'] != 1 or record['entry_name'] != NAME
                    or not isinstance(record['created_file'], bool)):
                raise ValueError('Unknown project MCP journal; existing files were preserved')
            if servers.get(NAME) != record['entry']:
                raise ValueError('Managed MCP entry changed; restore refused to preserve user work')
            updated = {**config, 'mcpServers': {key: value for key, value in servers.items() if key != NAME}}
            if record['created_file'] and updated == {'mcpServers': {}}:
                path.unlink()
            else:
                atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2)+'\n')
            journal.unlink()
            return {'status': 'restored', 'path': str(path), 'server_name': NAME}
        existing = [name for name in (NAME, LEGACY_NAME) if name in servers]
        if existing:
            if len(existing) == 1 and servers[existing[0]] == entry:
                return {'status': 'already_configured', 'path': str(path), 'server_name': existing[0]}
            raise ValueError('A Luna/Jev MCP entry already exists with different settings; no duplicate was installed')
        if record is not None:
            raise ValueError('An existing MCP journal needs inspection; project files were preserved')
        updated = {**config, 'mcpServers': {**servers, NAME: entry}}
        record = {'version': 1, 'entry_name': NAME, 'entry': entry, 'created_file': original is None}
        atomic_write(journal, json.dumps(record, ensure_ascii=False, indent=2)+'\n')
        try:
            atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2)+'\n')
        except BaseException:
            journal.unlink()
            raise
        return {'status': 'configured', 'path': str(path), 'server_name': NAME,
                'service_start': f'luna-decision start --host {host} --port {port}'}
