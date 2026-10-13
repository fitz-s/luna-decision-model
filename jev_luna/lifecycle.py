"""One deadline, cancellation signal and observed-usage ledger per request."""
import contextlib
import contextvars
import copy
import threading
import time
import sys
import uuid

from .errors import Reject

current_context = contextvars.ContextVar("jev_luna_request", default=None)
attempt_route = contextvars.ContextVar("jev_luna_attempt_route", default=None)
attempt_batch = contextvars.ContextVar("jev_luna_attempt_batch", default=None)
physical_attempt = contextvars.ContextVar("jev_luna_physical_attempt", default=None)


class RequestContext:
    def __init__(self, deadline, *, started=None, observer=None):
        self.started = time.monotonic() if started is None else started
        self.deadline = deadline
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.reason = None
        self.callbacks = set()
        self.usage = {}
        self.observations = 0
        self.unknown_attempts = 0
        self.active_attempts = 0
        self.attempt_sequence = 0
        self.missing_details = set()
        self.partial_usage_attempts = []
        self.auth_registration = None
        self.usage_merger = None
        self.request_id = 'req_'+uuid.uuid4().hex
        self.observer = observer

    def event(self, name, **fields):
        with self.lock:
            if self.observer is None: return
            identifier = physical_attempt.get()
            if identifier is not None:
                fields.setdefault('physical_attempt_id', identifier)
            try:
                self.observer(name, fields)
            except Exception:
                # Diagnostic disk failure must not change selection or deadlines.
                self.observer = None
                print('jev-luna: request trace could not be saved; observer disabled', file=sys.stderr)

    def remaining(self):
        if self.cancelled.is_set():
            raise self.reason
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            self.cancel("deadline", "request deadline exceeded", 504)
            raise self.reason
        return remaining

    def cancel(self, code="cancelled", message="caller cancelled the request", status=499):
        with self.lock:
            if self.cancelled.is_set():
                return
            self.reason = Reject(status, code, message)
            self.cancelled.set()
            callbacks = list(self.callbacks)
            self.event('cancelled', code=code, accounting=self.snapshot())
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # Cancellation must reach other resources even if one is already closed.
                pass

    def register(self, callback):
        with self.lock:
            if self.cancelled.is_set():
                callback()
                raise self.reason
            self.callbacks.add(callback)

        def remove():
            with self.lock:
                self.callbacks.discard(callback)
        return remove

    def observe(self, usage, merger):
        with self.lock:
            self.active_attempts = max(0, self.active_attempts-1)
            if not usage:
                self.unknown_attempts += 1
                self.event('usage', batch=attempt_batch.get(), status='unavailable')
                return
            try:
                validated = {}
                merger(validated, usage)
            except Reject as error:
                self.unknown_attempts += 1
                error.accounting_recorded = True
                self.event('usage', batch=attempt_batch.get(), status='invalid')
                raise
            self.observations += 1
            if not all(key in validated for key in ("input_tokens", "output_tokens")):
                # Keep actual partial fields separately: mixing them into a complete
                # subtotal would either invent missing counts or contradict its total.
                self.partial_usage_attempts.append(validated)
                self.unknown_attempts += 1
                self.event('usage', batch=attempt_batch.get(), status='partial', observed=validated)
                return
            merger(self.usage, validated)
            self.event('usage', batch=attempt_batch.get(), status='observed', observed=validated)
            for key, fields in (("input_tokens_details", ("cached_tokens", "cache_write_tokens")),
                                ("output_tokens_details", ("reasoning_tokens",))):
                for field in fields:
                    if not isinstance(usage.get(key), dict) or field not in usage[key]:
                        self.missing_details.add((key, field))

    def snapshot(self):
        with self.lock:
            usage = copy.deepcopy(self.usage)
            for key, field in self.missing_details:
                if key in usage:
                    usage[key].pop(field, None)
            unknown = self.unknown_attempts+self.active_attempts
            result = {"observed_usage": usage,
                    "usage_status": "unavailable" if not self.observations else "partial" if unknown or self.missing_details else "observed",
                    "unknown_usage_attempts": unknown}
            if self.partial_usage_attempts:
                result["partial_usage_attempts"] = copy.deepcopy(self.partial_usage_attempts)
            return result

    def begin_attempt(self):
        with self.lock:
            self.remaining()
            self.active_attempts += 1
            self.attempt_sequence += 1
            return self.attempt_sequence

    def attach(self, error):
        error.accounting = self.snapshot()
        return error


@contextlib.contextmanager
def bind(context, *, route=None, batch=None, physical=None):
    token = current_context.set(context)
    route_token = attempt_route.set(route)
    batch_token = attempt_batch.set(batch) if batch is not None else None
    physical_token = physical_attempt.set(physical) if physical is not None else None
    try:
        yield
    finally:
        attempt_route.reset(route_token)
        if batch_token is not None: attempt_batch.reset(batch_token)
        if physical_token is not None: physical_attempt.reset(physical_token)
        current_context.reset(token)
