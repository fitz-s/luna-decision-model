"""Reuse native Codex ChatGPT OAuth in memory; never persist a credential copy."""
import http.client
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies

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


def _native_login(timeout, *, refresh=False, expected_generation=None, expected_account=None):
    """Read managed access + authoritative native workspace routing, without any turn."""
    from .native_rpc import Session, NativeRpcError
    deadline=time.monotonic()+timeout
    session=None
    try:
        session=Session(deadline)
        value=session.credential(deadline, force_refresh=refresh,
                                 expected_generation=expected_generation, expected_account=expected_account)
        generation=value.generation
        return value.access_token,value.account_id,generation
    except NativeRpcError as error:
        if error.code=='native_login_required':raise CodexLoginRequired(error.message) from None
        raise NativeAuthStateError(error.message, code=error.code, status=error.status) from None
    finally:
        if session is not None:session.close()


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
