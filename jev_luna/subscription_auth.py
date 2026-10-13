"""Reuse native Codex ChatGPT OAuth in memory; never persist a credential copy."""
import atexit
import http.client
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies
try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib=None

HOST = "chatgpt.com"
PATH = "/backend-api/codex/responses"


def auth_file():
    # Native storage namespace only; the facade does not read this file.
    return Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))).expanduser().resolve()/"auth.json"


class NativeAuthStateError(ValueError):
    """Native-manager faults or policy conflicts never start an unrelated login."""
    def __init__(self, message, *, code='native_auth_manager', status=422):
        self.code, self.status = code, status
        super().__init__(message)


class CodexLoginRequired(ValueError):
    """Only an absent native login may start the user's explicit codex login command."""


def request_parameters(timeout, *, context=None):
    """Fresh native credentials and routing metadata, with no unused SSL object.

    This is not a credential cache. Every invocation still uses the existing
    concurrent-only native lookup and its caller cancellation/deadline checks.
    """
    token, account, auth_generation = _credentials(timeout, context=context)
    proxy = getproxies().get("https")
    proxy_url = None
    if proxy:
        try:
            endpoint = urlsplit(proxy)
            if endpoint.scheme != "http" or not endpoint.hostname or endpoint.username or endpoint.password:
                raise ValueError("unsupported CONNECT proxy")
            port = endpoint.port or 80
        except ValueError:
            raise ValueError("The subscription transport requires an unauthenticated HTTP CONNECT proxy or a direct connection") from None
        host = endpoint.hostname
        authority = f"[{host}]" if ":" in host else host
        proxy_url = f"http://{authority}:{port}"
    headers = {"Authorization": "Bearer "+token,
               "chatgpt-account-id": account, "Accept": "text/event-stream",
               "Accept-Encoding": "identity", "originator": "codex_cli_rs"}
    # Only the already validated native snapshot supplies this routing header.
    override = auth_generation[2]
    if override != "NO_CONSTRAINT":
        headers["x-openai-account-routing-override"] = override
    return PATH, headers, auth_generation, proxy_url


def connection(timeout, *, context=None):
    """Legacy control-client helper. Inference uses request_parameters directly."""
    path, headers, generation, proxy = request_parameters(timeout, context=context)
    if proxy:
        endpoint = urlsplit(proxy)
        conn = http.client.HTTPSConnection(endpoint.hostname, endpoint.port, timeout=timeout)
        conn.set_tunnel(HOST, 443)
    else:
        conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    conn._jev_auth_generation = generation
    return conn, path, headers


SESSION_MAX_AGE=300  # seconds; bounds how long one native process serves lookups
REUSE_RETRY=frozenset({"native_auth_channel", "native_auth_protocol"})  # EOF, broken pipe, exit, garbled frame
STORE_REBUILDS=2  # fresh reads discarded because the store changed underneath, before failing closed
MAX_CONFIG=1_048_576
SYSTEM_LAYERS=tuple(Path(p) for p in ("/etc/codex/config.toml", "/etc/codex/requirements.toml",
                                      "/etc/codex/managed_config.toml"))
STORE_KEY="cli_auth_credentials_store"
_layer_modes={}  # str(path) -> ((ino, size, mtime_ns), file_mode): parse each config version once
_session_lock=threading.Lock()
_session=None


def _drop_session():
    global _session
    with _session_lock:
        old,_session=_session,None
    if old is not None:old.close()


def _retire(session, deadline):
    """Close a detached session, waiting for it no later than the caller's deadline."""
    closer=threading.Thread(target=session.close, name="ldm-auth-close", daemon=True)
    closer.start()
    closer.join(max(0, deadline-time.monotonic()))


def _file_mode(raw):
    """True only when this TOML layer provably leaves Codex's credential store at File.

    Codex rust-v0.159.0 defaults to File (auth.json). Keyring, Auto (keyring first) and
    Ephemeral are not observable here, and any parse doubt counts as not File.
    """
    try:
        text=raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if tomllib is None:  # Python 3.10: only the exact top-level `key = "file"` form is trusted
        top=True
        for line in text.splitlines():
            line=line.strip()
            if line.startswith("["):
                top=False
            if "\\" in line.split("=", 1)[0]:
                return False  # an escaped quoted key could spell the store key
            if STORE_KEY in line and not (top and re.fullmatch(STORE_KEY+r"""\s*=\s*("file"|'file')\s*(#.*)?""", line)):
                return False
        return True
    try:
        data=tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return False
    def nested(value):
        if isinstance(value, list):
            return any(nested(item) for item in value)
        return isinstance(value, dict) and any(key == STORE_KEY or nested(item) for key, item in value.items())
    return data.get(STORE_KEY, "file") == "file" and not any(nested(item) for item in data.values())


def _managed_preferences():
    # macOS forced preferences can carry config/requirements TOML; their content is not checked here.
    if sys.platform!="darwin":
        return ()
    import pwd
    root=Path("/Library/Managed Preferences")
    return (root/"com.openai.codex.plist", root/pwd.getpwuid(os.getuid()).pw_name/"com.openai.codex.plist")


def _store_state():
    """Identity of a file-mode native store and its binary, or None when lookups must not share a process.

    Reuse requires the resolved codex executable (as native_rpc launches it), auth.json in
    the same CODEX_HOME, and every readable config layer that can set the store mode
    ($CODEX_HOME/config.toml and /etc/codex) to leave it at File; forced macOS preferences
    disable reuse. auth.json is stat'ed only, never read. Login, logout, rotation, config
    edits and a codex upgrade change size, mtime_ns or inode. Not observed, and bounded by
    SESSION_MAX_AGE: enterprise cloud requirements and same-size rewrites within one mtime
    tick on coarse-timestamp filesystems.
    """
    from .native_rpc import codex_executable
    executable=codex_executable()
    if executable is None:
        return None
    home=auth_file().parent
    state=[executable, str(home)]
    try:
        for path in (executable, home/"auth.json"):
            st=os.stat(path)
            state.append((st.st_ino, st.st_size, st.st_mtime_ns))
        for path in _managed_preferences():
            if os.path.lexists(path):
                return None
        for path in (home/"config.toml",)+SYSTEM_LAYERS:
            try:
                with open(path, "rb") as layer:
                    st=os.fstat(layer.fileno())
                    identity=(st.st_ino, st.st_size, st.st_mtime_ns)
                    mode=_layer_modes.get(str(path))
                    if mode is None or mode[0]!=identity:
                        raw=layer.read(MAX_CONFIG+1)
                        mode=_layer_modes[str(path)]=(identity, len(raw)<=MAX_CONFIG and _file_mode(raw))
            except FileNotFoundError:
                state.append(None)
                continue
            if not mode[1]:
                return None
            state.append(identity)
    except (OSError, KeyError):
        return None
    return tuple(state)


def _reusable(session, state):
    return not (session.closed or session.broken.is_set() or session.proc.poll() is not None
                or not session.inbox.empty() or state is None or session.store_state != state
                or time.monotonic()-session.started>SESSION_MAX_AGE)


def _shared_credential(deadline):
    """Fresh native read per lookup over one live app-server.

    Spawning `codex app-server` costs ~100 ms plus ~220 ms of cold account discovery,
    so a per-request process dominated local latency. Each call still performs the full
    guarded getAuthStatus/account/read sequence; no completed credential is retained.
    A live process may cache auth in memory, so it is reused only for a file-mode store
    (see _store_state) and retired whenever it could be stale: the store, config or binary
    changed, it emitted any unsolicited message while idle, it broke, or it aged out.

    Every read, fresh or reused, is fenced: store state is taken before and after it and a
    read that straddles a change is discarded (at most STORE_REBUILDS times, then 409).
    Only a channel failure of a reused process (REUSE_RETRY) is retried, once, on a fresh
    process bound to the owner and routing the reused one had established; every other
    error, including 409 account/policy changes, propagates.
    """
    global _session
    from .native_rpc import Session, NativeRpcError
    def expired():
        return NativeRpcError("native_auth_deadline", "Native credential lookup exceeded its budget", 504)
    left=deadline-time.monotonic()
    if left<=0 or not _session_lock.acquire(timeout=left):
        raise expired()
    try:
        binding, rebuilds = None, 0
        while True:
            state=_store_state()
            session,_session=_session,None  # republished only after a fenced success
            if session is not None and not _reusable(session, state):
                _retire(session, deadline)
                session=None
            reused=session is not None
            if not reused:
                if time.monotonic()>=deadline:
                    raise expired()
                session=Session(deadline)
            session.deadline=deadline
            try:
                value=session.credential(deadline, expected_account=binding[0] if binding else None)
                if binding is not None and value.routing_override!=binding[1]:
                    raise NativeRpcError("native_policy_changed", "Native routing changed during lookup", 409)
            except NativeRpcError as error:
                _retire(session, deadline)
                if reused and binding is None and error.code in REUSE_RETRY:
                    binding=session.binding
                    continue
                raise
            except BaseException:
                _retire(session, deadline)
                raise
            if _store_state()!=state:
                _retire(session, deadline)
                rebuilds+=1
                if rebuilds>STORE_REBUILDS:
                    raise NativeRpcError("native_account_changed",
                                         "Native credential store kept changing during lookup; start a new request", 409)
                continue
            session.binding=(value.account_id, value.routing_override)
            if state is not None:
                session.store_state, _session = state, session
            else:
                _retire(session, deadline)
            return value
    finally:
        _session_lock.release()


def _native_login(timeout, *, refresh=False, expected_generation=None, expected_account=None):
    """Read managed access + authoritative native workspace routing, without any turn.

    Refresh uses its own short-lived process, then retires the shared one so the next
    lookup observes the rotated native store.
    """
    from .native_rpc import Session, NativeRpcError
    deadline=time.monotonic()+timeout
    session=None
    try:
        if not refresh:
            value=_shared_credential(deadline)
        else:
            session=Session(deadline)
            value=session.credential(deadline, force_refresh=refresh,
                                     expected_generation=expected_generation, expected_account=expected_account)
        return value.access_token,value.account_id,value.generation
    except NativeRpcError as error:
        if error.code=='native_login_required':raise CodexLoginRequired(error.message) from None
        raise NativeAuthStateError(error.message, code=error.code, status=error.status) from None
    finally:
        if session is not None:
            session.close()
            _drop_session()


atexit.register(_drop_session)


_lookup_lock=threading.Lock()
_lookups={}


def _credentials(timeout, *, context=None):
    """Share only concurrent native lookups, never cache completed credentials.

    The bounded manager work can finish credential rotation after a caller's
    deadline. Fresh later requests load the current native store and policy.
    """
    deadline=time.monotonic()+timeout
    if context is not None:
        context.remaining()
        deadline=min(deadline, context.deadline)
    key=(str(auth_file().parent.absolute()), os.environ.get('PATH',''), os.environ.get('LDM_CODEX_BIN',''))
    with _lookup_lock:
        flight=_lookups.get(key)
        if flight is None:
            flight={'event':threading.Event()}
            _lookups[key]=flight
            def run():
                try:flight['result']=_native_login(30)
                except Exception as error:flight['error']=error
                finally:
                    with _lookup_lock:
                        if _lookups.get(key) is flight:_lookups.pop(key)
                    flight['event'].set()
            threading.Thread(target=run,daemon=True).start()
    while not flight['event'].is_set():
        if context is not None:
            context.remaining()
        left=deadline-time.monotonic()
        if left<=0:
            raise NativeAuthStateError("Native Codex OAuth deadline exceeded", code="native_auth_deadline", status=504)
        flight['event'].wait(min(.05,left))
    if context is not None:
        context.remaining()
    if time.monotonic()>=deadline:
        raise NativeAuthStateError("Native Codex OAuth deadline exceeded", code="native_auth_deadline", status=504)
    if 'error' in flight:raise flight['error']
    return flight['result']


def _refresh_native(timeout, *, expected_generation=None, expected_account=None):
    """Codex owns refresh; comparison and rotation use one bound native session."""
    _native_login(timeout, refresh=True, expected_generation=expected_generation,
                  expected_account=expected_account)


_refresh_lock = threading.Lock()
_refreshes = {}


def refresh(timeout, *, expected_generation=None, context=None):
    """Coalesce native-manager refresh; rotating credential persistence outlives a cancelled caller."""
    from filelock import FileLock, Timeout as LockTimeout
    from .lifecycle import RequestContext
    context=context or RequestContext(time.monotonic()+timeout)
    context.remaining()
    path=str(auth_file().absolute())
    with context.lock:
        expected_account=context.auth_registration
    key=(path, expected_account, expected_generation)
    with _refresh_lock:
        now=time.monotonic()
        for old_key, old in list(_refreshes.items()):
            if old['event'].is_set() and now-old.get('finished', now)>=1:
                _refreshes.pop(old_key, None)
        flight=_refreshes.get(key)
        reusable=flight and flight['event'].is_set() and not flight.get('error') and flight['expected']==expected_generation and time.monotonic()-flight['finished']<1
        if flight is None or flight['event'].is_set() and not reusable:
            flight={'event':threading.Event(),'expected':expected_generation}
            _refreshes[key]=flight
            def run():
                lock=None
                try:
                    deadline=time.monotonic()+30
                    lock=FileLock(str(auth_file().parent/'.jev-luna-auth-refresh.lock'))
                    while True:
                        left=deadline-time.monotonic()
                        if left<=0:
                            raise NativeAuthStateError('Native auth refresh coordination deadline exceeded',
                                                       code='native_auth_deadline', status=504)
                        try:lock.acquire(timeout=0);break
                        except LockTimeout:threading.Event().wait(min(.05,left))
                    _refresh_native(max(.001,deadline-time.monotonic()),
                                    expected_generation=expected_generation, expected_account=expected_account)
                except NativeAuthStateError as error:
                    flight['error']=error
                except Exception:
                    flight['error']=NativeAuthStateError('Native renewal could not complete; inspect Codex locally',
                                                         code='native_refresh_failed', status=502)
                finally:
                    if lock and lock.is_locked:lock.release()
                    flight['finished']=time.monotonic();flight['event'].set()
            threading.Thread(target=run,daemon=True).start()
    while not flight['event'].wait(min(.05,context.remaining())):pass
    context.remaining()
    if flight.get('error'):raise flight['error']
