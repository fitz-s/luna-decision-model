"""Bound HTTP admission before parsing; supported deployments are loopback only."""
from http.server import ThreadingHTTPServer
import json
import socket
import threading
import time


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler, *, local_key, max_requests=16):
        if address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("The supported service binds only to loopback")
        self.local_key = local_key
        self.admission = threading.BoundedSemaphore(max_requests)
        self.max_requests = max_requests
        self.contexts, self.contexts_lock = set(), threading.Lock()
        self.admitted = {}
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.admission.acquire(blocking=False):
            body = json.dumps({"error": {"code": "admission_limit", "message": "Local request capacity reached"}}).encode()
            try:
                request.settimeout(.2)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nConnection: close\r\nRetry-After: 1\r\nContent-Length: "+str(len(body)).encode()+b"\r\n\r\n"+body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            self.admitted[request] = time.monotonic()
            super().process_request(request, address)
        except Exception:
            self.admission.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.admitted.pop(request, None)
            self.admission.release()

    def cancel_requests(self):
        with self.contexts_lock:
            contexts = list(self.contexts)
        for context in contexts:
            context.cancel("shutdown", "Local service is stopping", 503)


class IPv6HTTPServer(BoundedHTTPServer):
    address_family = socket.AF_INET6
