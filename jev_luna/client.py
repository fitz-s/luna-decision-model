"""Authenticated loopback clients sharing the caller's original deadline."""
import asyncio
import json
import math
import time

import aiohttp

from .local import service_key, loopback_url


class DecisionResult(dict):
    """Original JSON contract, with response diagnostics available separately."""
    def __init__(self, payload, headers):
        super().__init__(payload)
        self.diagnostics = {"usage_status": headers.get("X-Decide-D-Usage-Status", "unavailable"),
                            "unknown_usage_attempts": int(headers.get("X-Decide-D-Unknown-Usage-Attempts", "0"))}


class DecisionError(ValueError):
    def __init__(self, status, payload, headers):
        self.status, self.payload = status, payload
        self.error = payload.get("error", {}) if isinstance(payload, dict) else {}
        self.accounting = {key: payload.get(key) for key in ("observed_usage", "usage_status", "unknown_usage_attempts")} if isinstance(payload, dict) else {}
        if isinstance(payload, dict) and "partial_usage_attempts" in payload:
            self.accounting["partial_usage_attempts"] = payload["partial_usage_attempts"]
        self.headers = {key: value for key, value in headers.items() if key.lower() in
                        ("retry-after", "retry-after-ms", "x-upstream-request-id")}
        super().__init__(f"Decision request failed: HTTP {status} {self.error.get('code', 'unknown_error')}")


def report_diagnostics(result):
    import sys
    if result.diagnostics["usage_status"] != "observed":
        print(json.dumps({"decision_diagnostics": result.diagnostics}), file=sys.stderr)


async def request(body, *, protocol="jev", url="http://127.0.0.1:20141", timeout=120):
    deadline = time.monotonic()+timeout
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    if protocol not in ("jev", "openai"):
        raise ValueError("Select jev or openai protocol")
    url = loopback_url(url)
    key = service_key()
    path = "/v1/systemone" if protocol == "jev" else "/v1/decisions"
    headers = {"Authorization": "Bearer "+key, "Content-Type": "application/json",
               "X-Decide-D-Timeout-Ms": str(max(1, int((deadline-time.monotonic())*1000)))}
    raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    async def call():
        async with aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                                         timeout=aiohttp.ClientTimeout(total=None)) as session:
            async with session.post(url.rstrip("/")+path, data=raw, headers=headers, allow_redirects=False) as response:
                chunks = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    chunks.extend(chunk)
                    if len(chunks) > 64*1024*1024: raise ValueError("Local response exceeded its operational byte budget")
                from .validation import loads
                payload = loads(bytes(chunks))
                if response.status != 200:
                    raise DecisionError(response.status, payload, response.headers)
                return DecisionResult(payload, response.headers)
    return await asyncio.wait_for(call(), timeout=max(0, deadline-time.monotonic()))
