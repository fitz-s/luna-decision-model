"""Protected app-local state; never reads or modifies another application's login."""
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit


def loopback_url(value, *, paths=("", "/")):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                 and parsed.port is not None and parsed.port > 0 and parsed.path in paths
                 and not (parsed.username or parsed.password or parsed.query or parsed.fragment))
    except (ValueError, TypeError):
        valid = False
    if not valid or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        raise ValueError("Use a loopback HTTP URL with an explicit port and the required local API path")
    return value.rstrip("/")


def config_home():
    explicit = os.environ.get("JEV_LUNA_HOME", "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            raise ValueError("JEV_LUNA_HOME must be an absolute app-state directory")
    elif os.name == "nt":
        path = Path(os.environ.get("APPDATA", str(Path.home()/"AppData/Roaming")))/"jev-luna"
    elif __import__("sys").platform == "darwin":
        path = Path.home()/"Library/Application Support/jev-luna"
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home()/".config")))/"jev-luna"
    if not path.is_absolute():
        raise ValueError("App-state directory must be absolute")
    return path


def ensure_directory():
    path = config_home()
    if path.is_symlink():
        raise ValueError("App-state directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise ValueError("App-state directory is not owned by this user")
    if os.name != "nt":
        path.chmod(0o700)
    return path


def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to replace a linked app-state file")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".write-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content.encode("utf-8") if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def service_key(*, create=False):
    path = config_home()/"service.key"
    if create:
        from filelock import FileLock
        ensure_directory()
        with FileLock(str(path.parent/"service.lock"), timeout=5):
            if not path.exists():
                atomic_write(path, "jvl_local_"+secrets.token_urlsafe(32)+"\n")
    if path.is_symlink():
        raise ValueError("Local service key must not be a symlink")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
                raise ValueError("Local service key must be a bounded regular file")
            if os.name != "nt" and (info.st_mode & 0o077 or hasattr(os, "getuid") and info.st_uid != os.getuid()):
                raise ValueError("Local service key must be owned by this user and mode 0600")
            raw = os.read(descriptor, 1025)
            if len(raw) > 1024 or os.read(descriptor, 1):
                raise ValueError("Local service key exceeded its byte limit")
            value = raw.decode("ascii").strip()
        finally:
            os.close(descriptor)
    except OSError:
        raise ValueError("Start jev-luna serve first; no local service credential is available") from None
    if not value.startswith("jvl_local_") or len(value) < 40 or not value.isascii():
        raise ValueError("Invalid local service credential")
    return value
