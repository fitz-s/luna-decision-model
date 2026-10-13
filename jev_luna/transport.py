"""Bounded pooled Responses I/O; no model or billing-path fallback."""
import atexit
import hashlib
import asyncio
import concurrent.futures
from dataclasses import dataclass, replace
from types import MappingProxyType
import json
import re
import sys
import threading
import uuid

import aiohttp
from multidict import CIMultiDict

from .errors import Reject
from .lifecycle import RequestContext, attempt_batch, attempt_scope
from .validation import loads

MAX_FRAME_BYTES = 4*1024*1024
MAX_RESPONSE_BYTES = 64*1024*1024
MAX_ERROR_BYTES = 64*1024
SAFE_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SAFE_PARAM = re.compile(r"^[A-Za-z0-9_.\[\]-]{1,128}$")
POOL = 4  # connections per transport; also the hedge's spare-capacity bound
# Codex rust-v0.159.0 core/src/client.rs:173-177, 1321-1324.
WS_BETA = "responses_websockets=2026-02-06"
LITE_HEADER = "x-openai-internal-codex-responses-lite"
LITE_WS_METADATA = "ws_request_header_x_openai_internal_codex_responses_lite"
LITE_IDS = uuid.uuid5(uuid.NAMESPACE_OID, "luna-decision-model")
# Capability boundaries. A handshake status here means the endpoint does not offer the WebSocket
# protocol (Codex falls back to HTTP on 426, client.rs:1920-1925); 401/403/429/5xx keep their meaning.
WS_UNSUPPORTED = {200, 400, 404, 405, 426, 501}
LITE_REJECTIONS = {"invalid_request_error", "unsupported_parameter", "unknown_parameter", "unsupported_value",
                   "invalid_value", "subscription_sharing_unsupported_capability"}
LITE_FIELDS = re.compile(r"^(?:input\[[01]\]|reasoning\.context|tool_choice|parallel_tool_calls)(?:[.\[]|$)")
unavailable, _unavailable_lock = set(), threading.Lock()


@dataclass(repr=False, frozen=True)
class Route:
    url: str
    headers: dict
    proxy: str = None
    profile: str = "subscription"
    auth_generation: tuple = None
    lite: bool = False
    websocket: bool = False

    def __post_init__(self):
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


class ObservedUsage(dict):
    """Marker for usage already entered by the stream reader, before cancellation races."""
    observed_in_context = True


def observe(usage, context, scope=None):
    if context.usage_merger is not None and (scope is None or scope.claim()):
        context.observe(usage, context.usage_merger)
    return ObservedUsage(usage) if isinstance(usage, dict) else ObservedUsage()


def disable(capability, detail):
    """Process-lifetime boundary: later decisions use the standard request without probing again."""
    with _unavailable_lock:
        if capability in unavailable:
            return
        unavailable.add(capability)
    print(f"luna-decision: {capability} unavailable ({detail}); using the standard HTTP request", file=sys.stderr, flush=True)


def active(route):
    return {"responses_lite": route.lite and "responses_lite" not in unavailable,
            "websocket": route.websocket and "websocket" not in unavailable}


def lite(payload, websocket=False):
    """Codex Responses Lite (core/src/client.rs:863-944, context/base_instructions.rs:12-14).

    Base instructions and the (empty) tool list travel as developer input items ahead of the
    unchanged state and question messages; top-level instructions and tools are omitted.
    """
    body = {key: value for key, value in payload.items() if key not in ("instructions", "tools")}
    tools = []
    prefix = [{"type": "additional_tools", "id": "at_"+str(uuid.uuid5(LITE_IDS, json.dumps(tools))),
               "role": "developer", "tools": tools}]
    if payload.get("instructions"):
        prefix.append({"type": "message", "id": "msg_"+str(uuid.uuid5(LITE_IDS, payload["instructions"])),
                       "role": "developer", "content": [{"type": "input_text", "text": payload["instructions"]}]})
    body["input"] = prefix+list(payload["input"])
    body["tool_choice"], body["parallel_tool_calls"] = "auto", False
    body["reasoning"] = {**payload["reasoning"], "context": "all_turns"}
    if websocket:
        body["client_metadata"] = {**payload.get("client_metadata", {}), LITE_WS_METADATA: "true"}
    return body


def lite_rejected(error):
    """A pre-output rejection naming the Lite representation (or nothing): never auth, quota or a later field."""
    status, param = getattr(error, "upstream_status", None), getattr(error, "param", None)
    return (not getattr(error, "accounting_recorded", False) and not getattr(error, "stream_started", False)
            and status in (200, 400, 422)
            and (error.code in LITE_REJECTIONS or error.code == "upstream_failure" and status != 200)
            and (param is None or bool(LITE_FIELDS.match(param))))


class Unsupported(Exception):
    """The WebSocket endpoint refused the protocol before any request was sent."""
    def __init__(self, detail, permanent=True):
        super().__init__(detail)
        self.detail, self.permanent = detail, permanent


def prepare(settings, context):
    profile = settings["transport"]
    if profile != "subscription" or any(name in settings for name in ("responses_url", "upstream", "proxy_key")):
        raise Reject(422, "transport_config", "Only native Codex ChatGPT subscription transport is supported")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", "Accept-Encoding": "identity"}
    if profile == "subscription":
        from . import subscription_auth
        try:
            path, authorization, generation, proxy = subscription_auth.request_parameters(context.remaining(), context=context)
        except subscription_auth.NativeAuthStateError as error:
            context.remaining()  # Preserve the caller's expired/cancelled request status.
            raise Reject(error.status, error.code, str(error)) from None
        except ValueError as error:
            context.remaining()
            code = "proxy_config" if "proxy" in str(error).lower() else "subscription_auth"
            raise Reject(422 if code == "proxy_config" else 401, code,
                         "Invalid CONNECT proxy configuration" if code == "proxy_config" else
                         "Codex ChatGPT login is required; run codex login") from None
        headers.update(authorization)
        with context.lock:
            account = authorization.get("chatgpt-account-id")
            if context.auth_registration is None:
                context.auth_registration = account
            elif context.auth_registration != account:
                raise Reject(409, "account_changed", "The Codex ChatGPT account changed during this request; start a new request")
            policy = authorization.get("x-openai-account-routing-override", "NO_CONSTRAINT")
            if not hasattr(context, "auth_workspace_policy"):
                context.auth_workspace_policy = policy
            elif context.auth_workspace_policy != policy:
                raise Reject(409, "native_policy_changed", "Native routing changed during this request; start a new request")
        context.remaining()
        return Route("https://chatgpt.com"+path, headers, proxy, profile, generation,
                     settings.get("responses_lite", False), settings.get("upstream_transport") == "websocket")


def provider_error(status, body=None, headers=None):
    container = body if isinstance(body, dict) else {}
    if isinstance(container.get("response"), dict):
        container = container["response"]
    obj = container.get("error")
    if not isinstance(obj, dict) and container.get("type") == "error":
        obj = container
    code = obj.get("code") if isinstance(obj, dict) else None
    code = code if isinstance(code, str) and SAFE_CODE.fullmatch(code) else "upstream_failure"
    mapped = {"model_not_found":404,"unsupported_model":400,"invalid_request_error":400,
              "context_length_exceeded":413,"rate_limit_exceeded":429,"usage_limit_reached":429,
              "insufficient_quota":429,"server_error":503,"temporarily_unavailable":503,
              "token_expired":401,"invalid_token":401,"invalid_access_token":401,"expired_token":401,
              "subscription_sharing_unsupported_capability": 400,
              "subscription_sharing_user_not_eligible": 403,
              "subscription_sharing_route_not_supported": 403,
              "chatpass_v2_scope_not_authorized": 403,
              "chatpass_v2_invalid_authorization_context": 403,
              "subscription_sharing_invalid_user": 401,
              "subscription_sharing_usage_limit_exceeded": 429,
              "subscription_sharing_usage_unavailable": 503,
              "subscription_sharing_user_unavailable": 503}
    # A terminal SSE error arrived over HTTP 200. Preserve that transport status,
    # but expose a failure that does not make the native Jev client retry unchanged.
    error = Reject(mapped.get(code, 422 if status == 200 else status), code, f"Luna upstream rejected the request ({code})")
    error.retryable = code == "upstream_failure" and status in (502, 504)
    permanent_auth = {"subscription_sharing_invalid_user", "account_deactivated", "account_disabled", "permission_denied", "invalid_api_key"}
    error.auth_recoverable = (status == 401 or error.status == 401) and code not in permanent_auth and mapped.get(code, 401) == 401
    param = obj.get("param") if isinstance(obj, dict) else None
    if isinstance(param, str) and SAFE_PARAM.fullmatch(param):
        error.param = param
    error.upstream_status = status
    error.category, error.stage, error.reason = 'upstream_provider', 'upstream_rejection', 'provider_rejection'
    error.upstream_completed = False
    if headers:
        for name in ("Retry-After", "Retry-After-Ms", "x-request-id"):
            value = headers.get(name)
            if isinstance(value, str) and len(value) <= 256 and "\n" not in value and "\r" not in value:
                error.headers["X-Upstream-Request-Id" if name == "x-request-id" else name] = value
    if isinstance(container.get("usage"), dict):
        error.usage = container["usage"]
    return error


def _tier(response):
    """The tier the upstream reports serving, for traces only."""
    tier = response.get("service_tier") if isinstance(response, dict) else None
    return tier if isinstance(tier, str) and SAFE_CODE.fullmatch(tier) else None


def _stream_failure(message, reason):
    error = Reject(502, 'invalid_output', message)
    error.category, error.stage, error.reason = 'stream_protocol_error', 'responses_stream', reason
    error.upstream_completed = False
    return error


def _connection_trace():
    """Observe pool activity, never request headers, URLs, bodies or credentials."""
    trace = aiohttp.TraceConfig()
    signals = {
        "on_connection_create_start": "connection_create_start",
        "on_connection_create_end": "connection_create_end",
        "on_connection_reuseconn": "connection_reused",
        "on_connection_queued_start": "connector_queue_start",
        "on_connection_queued_end": "connector_queue_end",
        "on_dns_resolvehost_start": "dns_start",
        "on_dns_resolvehost_end": "dns_end",
        "on_request_headers_sent": "request_headers_sent",
        "on_request_chunk_sent": "request_body_chunk_sent",
    }
    def callback(label):
        async def observe_connection(session, trace_context, params):
            item = getattr(trace_context, "trace_request_ctx", None)
            if isinstance(item, dict) and label == "request_headers_sent" and item.get("scope") is not None:
                item["scope"].mark_sent()  # the hedge timer starts at actual upstream send
            if isinstance(item, dict) and item.get("context") is not None:
                item["context"].event(label, batch=item.get("batch"))
        return observe_connection
    for signal, label in signals.items():
        getattr(trace, signal).append(callback(label))
    return trace


class _SSELines:
    """Physical CR, LF, or CRLF lines, including delimiters split across chunks.

    https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream
    A trailing unterminated line never dispatches a successful terminal event.
    """
    def __init__(self):
        self.pending = bytearray()
        self.skip_lf = False

    def feed(self, chunk):
        if self.skip_lf and chunk:
            if chunk[:1] == b"\n":
                chunk = chunk[1:]
            self.skip_lf = False
        self.pending.extend(chunk)
        while self.pending:
            newline = self.pending.find(b"\n")
            carriage = self.pending.find(b"\r", 0, newline if newline >= 0 else len(self.pending))
            index = carriage if carriage >= 0 else newline
            if index < 0:
                return
            line = bytes(self.pending[:index])
            consumed = index + 1
            if self.pending[index] == 13:
                if consumed == len(self.pending):
                    self.skip_lf = True
                elif self.pending[consumed] == 10:
                    consumed += 1
            del self.pending[:consumed]
            yield line


class _Events:
    """Responses events in stream order; SSE and WebSocket share every terminal check."""
    def __init__(self, context, scope, status, headers, at_eof=None, earlier=()):
        self.context, self.scope, self.status, self.headers, self.at_eof = context, scope, status, headers, at_eof
        self.done_items, self.seen, self.earlier = {}, set(), earlier

    def feed(self, event):
        context = self.context
        if not isinstance(event, dict):
            raise _stream_failure("Responses event must be an object", 'invalid_event')
        if self.earlier and _response_id(event) in self.earlier:
            return None  # a late frame of an earlier response on a reused WebSocket
        kind = event.get("type")
        if kind == "response.created" and "created" not in self.seen:
            self.seen.add("created")
            context.event('response_created', batch=attempt_batch.get())
        if kind in ("response.output_text.delta", "response.reasoning_text.delta",
                    "response.reasoning_summary_text.delta") and event.get("delta"):
            label = "first_output_delta" if kind == "response.output_text.delta" else "first_reasoning_delta"
            if label == "first_output_delta" and self.scope is not None:
                self.scope.output.set()
            if label not in self.seen:
                self.seen.add(label)
                context.event(label, batch=attempt_batch.get())
        if kind == "response.output_item.done":
            index, item = event.get("output_index"), event.get("item")
            if type(index) is not int or index < 0 or not isinstance(item, dict):
                raise _stream_failure("Invalid completed output item", 'invalid_output_item')
            if index in self.done_items and self.done_items[index] != item:
                raise _stream_failure("Conflicting completed output items", 'conflicting_output_item')
            self.done_items[index] = item
        at_eof = self.at_eof() if self.at_eof is not None else None
        if kind in ("response.completed", "response.done"):
            completed = event.get("response")
            context.event('terminal_event', batch=attempt_batch.get(), kind=kind,
                          http_body_at_eof=at_eof, service_tier=_tier(completed))
            if not isinstance(completed, dict):
                raise _stream_failure("Missing completed response object", 'missing_response_object')
            try:
                recorded = observe(completed.get("usage"), context, self.scope)
            except Reject as error:
                error.category, error.stage, error.reason = 'upstream_contract', 'completed_usage', 'invalid_usage'
                error.upstream_completed = True
                context.event('upstream_contract_rejected', batch=attempt_batch.get(),
                              code=error.code, category=error.category, stage=error.stage,
                              reason=error.reason, upstream_completed=True)
                raise
            return completed, self.done_items, recorded
        if kind in ("error", "response.failed", "response.incomplete"):
            failed = event.get("response") if isinstance(event.get("response"), dict) else event
            context.event('terminal_event', batch=attempt_batch.get(), kind=kind,
                          http_body_at_eof=at_eof, service_tier=_tier(failed))
            if kind == "response.incomplete":
                error = Reject(422, "incomplete_response", "Luna response was incomplete; no partial decision is accepted")
                error.usage = failed.get("usage") or {}
            else:
                error = provider_error(self.status, failed, self.headers)
            error.stream_started = bool(self.seen)  # created or any delta: the request was admitted
            if error.usage:
                observe(error.usage, context, self.scope)
                error.accounting_recorded = True
            raise error
        return None


def _response_id(event):
    response = event.get("response")
    value = response.get("id") if isinstance(response, dict) else event.get("response_id")
    return value if isinstance(value, str) else None


class _Stale(Exception):
    """A reused WebSocket closed before any event; one fresh handshake is allowed."""


class Client:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.session = self.ws_session = None
        self.busy = {"http": 0, "websocket": 0}
        self.idle, self.closing = [], set()  # idle: [(partition key, socket, completed response ids)], oldest first
        self.thread = threading.Thread(target=self._start, name="jev-luna-io", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("Responses I/O runtime did not start")

    def _start(self):
        asyncio.set_event_loop(self.loop)
        async def initialize():
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=POOL, limit_per_host=POOL),
                cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, auto_decompress=False,
                read_bufsize=16384, max_line_size=8190, max_field_size=8190,
                timeout=aiohttp.ClientTimeout(total=None), trace_configs=[_connection_trace()])
            # Sockets are bounded below (POOL active, POOL open in all); the connector must not queue behind idle ones.
            self.ws_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=0), cookie_jar=aiohttp.DummyCookieJar(), trust_env=False,
                timeout=aiohttp.ClientTimeout(total=None))
            self.ws_slots = asyncio.Semaphore(POOL)
        self.loop.run_until_complete(initialize())
        self.ready.set()
        self.loop.run_forever()

    def spare(self, route):
        """True when the transport this route uses has a free connection (hedges never queue)."""
        return self.busy["websocket" if active(route)["websocket"] else "http"] < POOL

    async def _read(self, route, raw, context, scope):
        self.busy["http"] += 1
        try:
            return await self._read_sse(route, raw, context, scope)
        finally:
            self.busy["http"] -= 1

    async def _read_sse(self, route, raw, context, scope):
        async with self.session.post(route.url, data=raw, headers=route.headers,
                                     proxy=route.proxy, allow_redirects=False,
                                     trace_request_ctx={"context": context, "batch": attempt_batch.get(),
                                                        "scope": scope}) as response:
            context.event('upstream_headers', batch=attempt_batch.get(), status=response.status)
            if response.status != 200:
                error_body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    if len(error_body)+len(chunk) > MAX_ERROR_BYTES:
                        raise Reject(422, "response_budget", "Upstream error exceeded the operational byte budget")
                    error_body.extend(chunk)
                try:
                    body = loads(bytes(error_body))
                except ValueError:
                    body = None
                raise provider_error(response.status, body, response.headers)
            coding = response.headers.get("Content-Encoding", "identity")
            if coding not in ("", "identity"):
                raise Reject(422, "response_encoding", "Unexpected compressed upstream response")
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type and not (route.profile == "subscription" and not content_type):
                raise _stream_failure("Upstream did not return a Responses event stream", 'content_type')
            framer = _SSELines()
            events = _Events(context, scope, response.status, response.headers, response.content.at_eof)
            lines, frame_bytes, total = [], 0, 0
            first = True
            async for chunk in response.content.iter_chunked(16384):
                if first:
                    context.event('first_event_bytes', batch=attempt_batch.get())
                    first = False
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise Reject(422, "response_budget", "Responses stream exceeded the operational byte budget")
                for line in framer.feed(chunk):
                    if line:
                        frame_bytes += len(line)
                        if frame_bytes > MAX_FRAME_BYTES:
                            raise Reject(422, "response_budget", "Responses frame exceeded the operational byte budget")
                        if line.startswith(b"data:"):
                            value = line[5:]
                            lines.append(value[1:] if value.startswith(b" ") else value)
                        continue
                    data, lines, frame_bytes = b"\n".join(lines), [], 0
                    if not data:
                        continue
                    if data == b"[DONE]":
                        raise _stream_failure("Stream ended without response.completed", 'missing_terminal')
                    try:
                        event = loads(data)
                    except ValueError:
                        raise _stream_failure("Invalid Responses event JSON", 'invalid_event_json') from None
                    result = events.feed(event)
                    if result is not None:
                        return result
                if len(framer.pending)+frame_bytes > MAX_FRAME_BYTES:
                    raise Reject(422, "response_budget", "Responses frame exceeded the operational byte budget")
            raise _stream_failure("Stream ended without response.completed", 'missing_terminal')

    def _discard(self, ws):
        # Never await a close handshake on the request path; a stalled peer cannot hold the caller.
        if not ws.closed:
            task = self.loop.create_task(ws.close())
            self.closing.add(task)
            task.add_done_callback(lambda done: (self.closing.discard(done), done.cancelled() or done.exception()))

    async def _idle(self, ws):
        """An idle socket is reusable only with nothing buffered: a pending close or late frame discards it."""
        if not ws.closed:
            try:
                await ws.receive(timeout=.001)
            except asyncio.TimeoutError:
                return True
        self._discard(ws)
        return False

    async def _connect(self, url, headers, route, context):
        while self.idle and len(self.idle)+self.busy["websocket"] > POOL:
            self._discard(self.idle.pop(0)[1])
        context.event('websocket_connect_start', batch=attempt_batch.get())
        try:
            ws = await self.ws_session.ws_connect(url, headers=headers, proxy=route.proxy, compress=15,
                                                  max_msg_size=MAX_FRAME_BYTES,
                                                  timeout=aiohttp.ClientWSTimeout(ws_close=1.0))
        except aiohttp.WSServerHandshakeError as error:
            context.event('upstream_headers', batch=attempt_batch.get(), status=error.status, transport='websocket')
            if error.status in WS_UNSUPPORTED:
                raise Unsupported(f"HTTP {error.status} at the WebSocket handshake") from None
            raise provider_error(error.status, None, error.headers) from None
        context.event('upstream_headers', batch=attempt_batch.get(), status=101, transport='websocket')
        return ws

    async def _read_ws(self, route, message, headers, context, scope):
        """One response.create per socket at a time; a socket is reused only after a clean completion and only
        for byte-identical handshake headers (account, policy, token, tier hint)."""
        url = re.sub(r"^http(s?)://", r"ws\1://", route.url)
        key = hashlib.sha256(json.dumps([url, route.proxy, sorted(headers.items())]).encode()).hexdigest()
        async with self.ws_slots:
            self.busy["websocket"] += 1
            try:
                for fresh in (False, True):
                    ws, earlier = None, ()
                    while not fresh and ws is None and any(entry[0] == key for entry in self.idle):
                        index = max(i for i, entry in enumerate(self.idle) if entry[0] == key)
                        _, candidate, earlier = self.idle.pop(index)
                        ws = candidate if await self._idle(candidate) else None
                    reused = ws is not None
                    if reused:
                        context.event('websocket_reused', batch=attempt_batch.get())
                    else:
                        ws, earlier = await self._connect(url, headers, route, context), ()
                    result = None
                    try:
                        result = await self._exchange(ws, message, context, scope, reused, earlier)
                        return result
                    except _Stale:
                        continue
                    finally:
                        if result is not None and not ws.closed:
                            done = result[0].get("id")
                            self.idle.append((key, ws, (*earlier, done) if isinstance(done, str) else earlier))
                        else:
                            self._discard(ws)
            finally:
                self.busy["websocket"] -= 1

    async def _exchange(self, ws, message, context, scope, reused, earlier):
        try:
            await ws.send_str(message)
        except (aiohttp.ClientError, ConnectionError):
            if reused:
                raise _Stale() from None
            raise
        if scope is not None:
            scope.mark_sent()
        events, total, first = _Events(context, scope, 200, None, earlier=earlier), 0, True
        while True:
            frame = await ws.receive()
            if frame.type == aiohttp.WSMsgType.TEXT:
                if first:
                    context.event('first_event_bytes', batch=attempt_batch.get())
                    first = False
                total += len(frame.data)
                if total > MAX_RESPONSE_BYTES:
                    raise Reject(422, "response_budget", "Responses stream exceeded the operational byte budget")
                try:
                    event = loads(frame.data)
                except ValueError:
                    raise _stream_failure("Invalid Responses event JSON", 'invalid_event_json') from None
                if isinstance(event, dict) and event.get("type") == "error":
                    # Wrapped errors carry the HTTP status (codex-api responses_websocket.rs:596-662).
                    inner = event.get("error") if isinstance(event.get("error"), dict) else {}
                    if inner.get("code") == "websocket_connection_limit_reached" and "created" not in events.seen:
                        raise Unsupported("websocket_connection_limit_reached", permanent=False)
                    status = event.get("status", event.get("status_code"))
                    if type(status) is int and 400 <= status <= 599:
                        events.status = status
                    if isinstance(event.get("headers"), dict):
                        events.headers = CIMultiDict((str(k), str(v)) for k, v in event["headers"].items())
                result = events.feed(event)
                if result is not None:
                    return result
            elif frame.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                # Not resent here: the server may have admitted the request; like a dropped HTTP stream, the attempt
                # fails retryably and the attempt policy decides.
                error = _stream_failure("WebSocket closed before response.completed", 'websocket_closed')
                error.retryable = True
                raise error
            elif frame.type == aiohttp.WSMsgType.ERROR:
                raise _stream_failure("WebSocket protocol error", 'websocket_error')
            else:
                raise _stream_failure("Unexpected binary WebSocket event", 'websocket_binary')

    async def _bounded(self, coroutine, remaining):
        # wait_for preserves the unrounded outer budget even if library timers round.
        return await asyncio.wait_for(coroutine, timeout=remaining)

    def call(self, route, payload, context):
        """Lite and WebSocket fall back to the standard HTTP request on a capability boundary only;
        model, account, tier and billing route never change."""
        flags = active(route)
        while True:
            try:
                return self._call(route, payload, context, flags["responses_lite"], flags["websocket"])
            except Unsupported as boundary:
                context.event('capability_fallback', batch=attempt_batch.get(), capability='websocket',
                              detail=boundary.detail, permanent=boundary.permanent)
                if boundary.permanent:
                    disable("websocket", boundary.detail)
                flags["websocket"] = False
            except Reject as error:
                if not (flags["responses_lite"] and lite_rejected(error)):
                    raise
                context.event('capability_fallback', batch=attempt_batch.get(), capability='responses_lite',
                              status=error.upstream_status, code=error.code, permanent=True)
                disable("responses_lite", f"HTTP {error.upstream_status} {error.code}")
                flags["responses_lite"] = False

    def _call(self, route, payload, context, use_lite, use_websocket):
        context.remaining()
        body = lite(payload, use_websocket) if use_lite else payload
        tier = payload.get("service_tier")
        if use_websocket:
            body = {"type": "response.create", **body}
            headers = {key: value for key, value in route.headers.items()
                       if key.lower() not in ("content-type", "accept", "accept-encoding")}
            # Codex sends the routing hint in every handshake (core/src/client.rs:1158-1174, 1315-1317).
            headers["x-codex-routing-hint"] = f"model={payload.get('model')}"+(f";tier={tier}" if tier is not None else "")
            headers["OpenAI-Beta"] = WS_BETA
        else:
            headers = dict(route.headers)
            if tier is not None:
                # Codex sends this hint with every tiered ChatGPT-backend request (codex-rs core/src/client.rs).
                headers["x-codex-routing-hint"] = f"model={payload['model']};tier={tier}"
            if use_lite:
                headers[LITE_HEADER] = "true"
        try:
            raw = json.dumps(body, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            raise Reject(422, "invalid_request", "Input cannot be encoded as strict JSON") from None
        remaining = context.remaining()
        if context.observer is not None:
            context.event('upstream_request', batch=attempt_batch.get(), bytes=len(raw),
                          payload_sha256=hashlib.sha256(raw).hexdigest(),
                          transport="websocket" if use_websocket else "http", responses_lite=use_lite)
        scope = attempt_scope.get()
        reader = (self._read_ws(route, raw.decode("utf-8"), headers, context, scope) if use_websocket
                  else self._read(replace(route, headers=headers), raw, context, scope))
        future = asyncio.run_coroutine_threadsafe(self._bounded(reader, remaining), self.loop)
        remove = context.register(future.cancel)
        try:
            drop = scope.register(future.cancel) if scope is not None else None
        except Reject:
            remove()
            raise
        try:
            return future.result(timeout=context.remaining())
        except concurrent.futures.CancelledError:
            context.remaining()
            raise Reject(499, "cancelled", "Request cancelled") from None
        except (TimeoutError, asyncio.TimeoutError, concurrent.futures.TimeoutError):  # distinct classes before 3.11
            future.cancel()
            context.cancel("deadline", "request deadline exceeded", 504)
            raise context.reason from None
        except aiohttp.ClientConnectorCertificateError:
            raise Reject(422, "tls_configuration", "Official upstream TLS verification failed") from None
        except (aiohttp.ClientError, OSError):
            context.remaining()
            error = Reject(502, "upstream_failure", "Responses connection failed", retryable=True)
            error.category, error.stage, error.reason = 'transport', 'connection', 'connection_failure'
            error.upstream_completed = False
            raise error from None
        finally:
            remove()
            if drop is not None:
                drop()

    def close(self):
        if self.loop.is_closed() or not self.loop.is_running():
            return
        async def shutdown():
            for _, ws, _ in self.idle:
                self._discard(ws)
            await asyncio.gather(*self.closing, return_exceptions=True)
            await self.session.close()
            await self.ws_session.close()
        try:
            asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(timeout=3)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)
            if not self.thread.is_alive():
                self.loop.close()


_client, _lock = None, threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = Client()
            atexit.register(_client.close)
        return _client
