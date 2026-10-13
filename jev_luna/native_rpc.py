"""Native Codex credential broker. No turns, login, registration or credential files.

Source-reviewed protocol: rust-v0.159.0 / 687a119f0fcaace47e1f1abcc77cec6c813fd6da.
workspaceRouting is experimental. A missing field is an explicit unsupported profile,
not permission to guess an account from email/JWT or to read another credential store.
Adapted from the Pro implementation. One live session serves successive lookups (bounded age,
retired on any fault or refresh); every lookup re-reads native state and no completed token is cached.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from .validation import loads
from . import __version__

MAX_LINE = 1_048_576
MAX_NOTICES = 64
ALLOWED_RPC = frozenset({"initialize", "getAuthStatus", "account/read"})
ORIGIN = "https://chatgpt.com"


class NativeRpcError(ValueError):
    def __init__(self, code: str, message: str, status: int = 401):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class Credential:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)
    backend_origin: str
    routing_override: str = "NO_CONSTRAINT"

    @property
    def generation(self) -> tuple:
        # Private, in-memory comparison only. Never include this in traces/doctor output.
        return ("native", hashlib.sha256(
            (self.account_id + "\0" + self.access_token).encode("utf-8")).hexdigest(), self.routing_override)


def validate_pair(status: dict, account: dict) -> Credential:
    if status.get("requiresOpenaiAuth") is not True:
        raise NativeRpcError("native_provider_profile", "Native provider does not expose OpenAI auth", 422)
    if status.get("authMethod") != "chatgpt":
        raise NativeRpcError("native_managed_login", "An existing Codex-managed ChatGPT login is required")
    token = status.get("authToken")
    if not isinstance(token, str) or not token or len(token) > MAX_LINE // 2 or not token.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise NativeRpcError("native_token_unavailable", "Codex did not provide a usable managed access token")
    if account.get("requiresOpenaiAuth") is not True or not isinstance(account.get("account"), dict) or account["account"].get("type") != "chatgpt":
        raise NativeRpcError("native_account_unavailable", "Codex managed-account metadata is unavailable")
    routing = account.get("workspaceRouting")
    if not isinstance(routing, dict):
        raise NativeRpcError("native_routing_capability", "Codex did not expose account/read.workspaceRouting; keep an explicit supported profile", 422)
    account_id = routing.get("chatgptAccountId")
    if not isinstance(account_id, str) or not account_id or len(account_id) > 512 or not account_id.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in account_id):
        raise NativeRpcError("native_account_binding", "Native workspace identity is missing or invalid", 422)
    override = routing.get("accountRoutingOverride")
    if routing.get("backendOrigin") != ORIGIN or override not in ("NO_CONSTRAINT", "us", "us_cr"):
        raise NativeRpcError("native_regional_route", "This release supports only native same-origin workspace routing", 422)
    return Credential(token, account_id, ORIGIN, override)


def codex_executable() -> str | None:
    """The resolved codex binary a Session launches, or None when none is configured or on PATH."""
    executable = os.environ.get("LDM_CODEX_BIN") or shutil.which("codex")
    return str(Path(executable).resolve()) if executable else None


class Session:
    def __init__(self, deadline: float):
        executable = codex_executable()
        if not executable:
            raise NativeRpcError("codex_missing", "Install Codex and use its own ChatGPT login", 422)
        self.home = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))).expanduser().resolve()
        self.neutral = tempfile.TemporaryDirectory(prefix="ldm-native-auth-")
        env = dict(os.environ)
        env["CODEX_HOME"] = str(self.home)
        for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_AUTH_JWT", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
            env.pop(name, None)
        # Process-local provider selection only; never changes config.toml or the store mode.
        command = [executable, "-c", 'model_provider="openai"', "app-server"]
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, cwd=self.neutral.name, env=env)
        except OSError:
            self.neutral.cleanup()
            raise NativeRpcError("codex_start", "Could not start the native Codex auth process", 422) from None
        self.deadline = deadline
        try:
            # Use raw nonblocking writes, never buffered writes that can outlive the watchdog.
            os.set_blocking(self.proc.stdin.fileno(), False)
        except (OSError, ValueError, AttributeError):
            try:
                self.proc.kill()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (self.proc.stdin, self.proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            self.neutral.cleanup()
            raise NativeRpcError("native_pipe_capability", "Native auth requires nonblocking pipe support", 422) from None
        self.started = time.monotonic()
        self.inbox = queue.Queue(maxsize=MAX_NOTICES)
        self.broken = threading.Event()
        self.sequence = 0
        self.account_epoch = 0
        self.closed = False
        self.reader = threading.Thread(target=self._pump, name="ldm-auth-read", daemon=True)
        self.reader.start()
        try:
            self.rpc("initialize", {"clientInfo": {"name": "luna-decision-model", "version": __version__},
                                    "capabilities": {"experimentalApi": True}}, deadline)
            self._write({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise

    def _pump(self):
        try:
            while True:
                line = self.proc.stdout.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                    self.broken.set()
                    return
                try:
                    self.inbox.put_nowait(line)
                except queue.Full:
                    self.broken.set()
                    return
        except (OSError, ValueError):
            self.broken.set()

    def _write(self, payload):
        # The protocol peer receives only fixed, small control messages, never model input.
        raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()+b"\n"
        if len(raw) > 8192:
            raise NativeRpcError("native_auth_frame", "Native control request exceeded its budget", 422)
        remaining = memoryview(raw)
        while remaining:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise NativeRpcError("native_auth_deadline", "Native credential lookup exceeded its budget", 504)
            if self.proc.poll() is not None:
                raise NativeRpcError("native_auth_channel", "Native credential channel ended", 502)
            try:
                written = os.write(self.proc.stdin.fileno(), remaining)
                if written <= 0:
                    raise NativeRpcError("native_auth_channel", "Native credential channel ended", 502)
                remaining = remaining[written:]
            except BlockingIOError:
                time.sleep(min(.01, left))

    def rpc(self, method, params, deadline):
        if method not in ALLOWED_RPC:
            raise NativeRpcError("native_auth_method", "Only native auth metadata RPCs are permitted", 422)
        if self.closed or time.monotonic() >= deadline:
            raise NativeRpcError("native_auth_deadline", "Native credential lookup exceeded its budget", 504)
        self.sequence += 1
        seq = self.sequence
        watchdog = threading.Timer(max(0, deadline-time.monotonic()), self._terminate)
        watchdog.daemon = True
        watchdog.start()
        try:
            self._write({"id": seq, "method": method, "params": params})
            while True:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise NativeRpcError("native_auth_deadline", "Native credential lookup exceeded its budget", 504)
                if self.broken.is_set():
                    raise NativeRpcError("native_auth_channel", "Native credential channel ended", 502)
                try:
                    message = loads(self.inbox.get(timeout=min(left, .05)))
                except queue.Empty:
                    continue
                if not isinstance(message, dict):
                    raise NativeRpcError("native_auth_protocol", "Invalid native credential response", 502)
                if message.get("method") == "account/updated":
                    self.account_epoch += 1
                if "method" in message and "id" in message:
                    # Never service server-initiated login, external-token, tool or approval requests.
                    self._write({"id": message["id"], "error": {"code": -32601, "message": "Auth-only client"}})
                    raise NativeRpcError("native_auth_profile", "Native process requested an unsupported host-owned auth operation", 422)
                if message.get("id") != seq:
                    continue
                if "error" in message:
                    error = message["error"]
                    # Exact, pinned CLI messages only. Never echo arbitrary native error bodies.
                    known = {
                        "account changed during workspace routing discovery":
                            ("native_account_changed", "Native account changed during lookup", 409),
                        "configuration changed during workspace routing discovery; retry account/read":
                            ("native_policy_changed", "Native policy changed during lookup", 409),
                        "workspace routing discovery timed out":
                            ("native_auth_deadline", "Native workspace discovery exceeded its budget", 504),
                    }
                    key = error.get("message") if isinstance(error, dict) else None
                    if isinstance(error, dict) and error.get("code") == -32603 and isinstance(key, str) and key in known:
                        raise NativeRpcError(*known[key])
                    raise NativeRpcError("native_auth_rpc", "Native credential RPC was rejected; inspect Codex locally", 422)
                result = message.get("result")
                if not isinstance(result, dict):
                    raise NativeRpcError("native_auth_protocol", "Native credential RPC omitted a result", 502)
                if self.broken.is_set():
                    raise NativeRpcError("native_auth_channel", "Native credential channel ended or lost messages", 502)
                return result
        except (OSError, ValueError) as error:
            if isinstance(error, NativeRpcError):
                raise
            raise NativeRpcError("native_auth_protocol", "Native credential channel failed", 502) from None
        finally:
            watchdog.cancel()

    def credential(self, deadline, *, force_refresh=False, expected_account=None,
                   expected_generation=None):
        # A guarded native read, not an atomic token+account transaction.
        metadata = self.rpc("getAuthStatus", {"includeToken": False, "refreshToken": False}, deadline)
        if metadata.get("requiresOpenaiAuth") is not True:
            raise NativeRpcError("native_provider_profile", "Native provider does not expose OpenAI auth", 422)
        if metadata.get("authMethod") is None:
            # getAuthStatus alone also returns null on token-read failures.
            # account/read's explicit empty account is the independent absence proof.
            epoch = self.account_epoch
            account = self.rpc("account/read", {"refreshToken": False}, deadline)
            empty_status = {"authMethod": None, "authToken": None, "requiresOpenaiAuth": True}
            empty_account = {"account": None, "requiresOpenaiAuth": True, "workspaceRouting": None}
            if (all(key in metadata and metadata[key] is value for key, value in empty_status.items())
                    and all(key in account and account[key] is value for key, value in empty_account.items())):
                confirmation = self.rpc("getAuthStatus", {"includeToken": False, "refreshToken": False}, deadline)
                confirmed_account = self.rpc("account/read", {"refreshToken": False}, deadline)
                stable = (all(key in confirmation and confirmation[key] is value for key, value in empty_status.items())
                          and all(key in confirmed_account and confirmed_account[key] is value for key, value in empty_account.items())
                          and epoch == self.account_epoch and not self.broken.is_set())
                if stable and expected_account is None and expected_generation is None:
                    raise NativeRpcError("native_login_required", "No native Codex ChatGPT login exists; run codex login")
            raise NativeRpcError("native_login_unavailable",
                                 "Codex did not establish usable auth; inspect native login before explicitly running codex login", 422)
        if metadata.get("authMethod") != "chatgpt":
            raise NativeRpcError("native_managed_login", "An existing Codex-managed ChatGPT login is required")

        owner = expected_account
        routing_policy = None

        def sample(refresh=False):
            nonlocal owner, routing_policy
            status = self.rpc("getAuthStatus", {"includeToken": True, "refreshToken": refresh}, deadline)
            account = self.rpc("account/read", {"refreshToken": False}, deadline)
            value = validate_pair(status, account)
            if owner is None:
                owner = value.account_id
            elif owner != value.account_id:
                raise NativeRpcError("native_account_changed", "Native account changed during lookup; start a new request", 409)
            if routing_policy is None:
                routing_policy = value.routing_override
            elif routing_policy != value.routing_override:
                raise NativeRpcError("native_policy_changed", "Native routing changed during lookup", 409)
            return value

        def consistent(refresh=False):
            for trial in range(2):
                first = sample(refresh and trial == 0)
                epoch = self.account_epoch
                second = sample()
                if (epoch == self.account_epoch and first.backend_origin == second.backend_origin
                        and hmac.compare_digest(first.access_token, second.access_token)):
                    if self.broken.is_set():
                        raise NativeRpcError("native_auth_channel", "Native credential channel lost messages", 502)
                    return second
            raise NativeRpcError("native_account_changed", "Native credentials did not stabilize; start a new request", 409)

        current = consistent()
        if expected_generation is not None and len(expected_generation) == 3 and current.routing_override != expected_generation[2]:
            raise NativeRpcError("native_policy_changed", "Native routing changed during this request", 409)
        # Compare and refresh in THIS session, after binding its authoritative account.
        # A fresh token from the same owner needs no extra forced refresh.
        if force_refresh and (expected_generation is None or current.generation == expected_generation):
            current = consistent(refresh=True)
        return current

    def _terminate(self):
        self.broken.set()
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except OSError:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._terminate()
        try:
            self.proc.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
                self.proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                # A stuck OS child must not consume an unbounded response budget.
                return
        except OSError:
            pass
        finally:
            if self.proc.poll() is not None:
                for stream in (self.proc.stdin, self.proc.stdout):
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass
                self.neutral.cleanup()
