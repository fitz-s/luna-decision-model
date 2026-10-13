"""Exclusive, crash-safe diagnostic receipts; no credential collection."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class AtomicReceipt:
    def __init__(self, path, initial):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._publish(initial, initial=True)

    def _publish(self, content, *, initial=False):
        raw = (json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2)+'\n').encode()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.receipt-', delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if initial:
                # Publish complete bytes atomically, without replacing another run.
                os.link(temporary, self.path)
            else:
                os.replace(temporary, self.path)
            if os.name != 'nt':
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, content):
        self._publish(content)


# A single bounded writer keeps slow diagnostic storage out of HTTP and model I/O.
# No result cache and no sampling of model outcomes. Missing trace events are explicit.
import copy
import queue

_TRACE_QUEUE = queue.Queue(maxsize=2048)
_TRACE_START_LOCK = threading.Lock()
_TRACE_WORKER = None
_MAX_EVENT_BYTES = 128 * 1024
_MAX_EVENTS_PER_REQUEST = 4096


def _trace_writer():
    while True:
        trace, name, fields, elapsed = _TRACE_QUEUE.get()
        try:
            trace._persist(name, fields, elapsed)
        except Exception:
            # No credential/content-bearing exception is logged. An unwritten or
            # incomplete receipt is never accepted by the trace verification gate.
            trace.write_error = True
        finally:
            _TRACE_QUEUE.task_done()


def _start_trace_writer():
    global _TRACE_WORKER
    with _TRACE_START_LOCK:
        if _TRACE_WORKER is None:
            _TRACE_WORKER = threading.Thread(target=_trace_writer, name="ldm-trace-writer", daemon=True)
            _TRACE_WORKER.start()


def flush_traces(timeout=2.0):
    """Bounded diagnostics drain for tests/report collection, NOT the response hot path."""
    deadline = time.monotonic() + timeout
    with _TRACE_QUEUE.all_tasks_done:
        while _TRACE_QUEUE.unfinished_tasks:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            _TRACE_QUEUE.all_tasks_done.wait(left)
    return True


class RequestTrace:
    """Nonblocking metadata observer; persistence is bounded and best-effort.

    A report requires phase=finished AND diagnostics_complete=true. Losing metadata
    must never change a model answer, caller deadline, or confidence threshold.
    """
    def __init__(self, directory, request_id, identity):
        self.started = time.monotonic()
        self.path = Path(directory)/(request_id + '.json')
        self.data = {'request_id': request_id, **copy.deepcopy(identity),
                     'phase': 'admitted', 'diagnostics_complete': False, 'events': []}
        self.receipt = None
        self.dropped = 0
        self.write_error = False
        self.event_count = 0
        _start_trace_writer()
        self('admitted', {})

    def __call__(self, name, fields):
        self.event_count += 1
        if self.write_error or self.event_count > _MAX_EVENTS_PER_REQUEST:
            self.dropped += 1
            return
        # Metadata only: never supply headers, OAuth responses, or model state here.
        raw = json.dumps(fields, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(raw.encode('utf-8')) > _MAX_EVENT_BYTES:
            self.dropped += 1
            return
        try:
            _TRACE_QUEUE.put_nowait((self, name, json.loads(raw), time.monotonic()-self.started))
        except queue.Full:
            self.dropped += 1

    def _persist(self, name, fields, elapsed):
        if self.write_error:
            return
        self.data['phase'] = name
        self.data['events'].append({'event': name, 'seconds': elapsed, **fields})
        self.data['dropped_events'] = self.dropped
        self.data['diagnostics_complete'] = name == 'finished' and not self.dropped
        if self.receipt is None:
            self.receipt = AtomicReceipt(self.path, self.data)
        else:
            self.receipt.save(self.data)

    def flush(self, timeout=2.0):
        if not flush_traces(timeout) or self.write_error or self.dropped:
            return False
        try:
            require_complete_trace(self.data)
        except ValueError:
            return False
        return True



def require_complete_trace(data):
    """Validate completeness, not HTTP success, inference quality, or durable-fsync proof."""
    if not isinstance(data, dict):
        raise ValueError("Trace must be an object")
    events = data.get("events")
    if (data.get("phase") != "finished" or data.get("diagnostics_complete") is not True
            or type(data.get("dropped_events")) is not int or data["dropped_events"] != 0
            or not isinstance(events, list) or not events
            or any(not isinstance(e, dict) for e in events)
            or events[0].get("event") != "admitted" or events[-1].get("event") != "finished"
            or sum(e.get("event") == "finished" for e in events) != 1):
        raise ValueError("Trace is incomplete; do not use it as full execution evidence")
    return data
