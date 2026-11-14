"""HTTP-level protection around every API request: rate limits, security headers, a request ID, one log
line per request, and a plain error instead of a stack trace.

Plain ASGI (not BaseHTTPMiddleware) so the decision trace's server-sent events stream through untouched.

- Rate limits per client, per minute, in three buckets: routes that take the PIN, chat (each message
  can call the model), and everything else. STOP is never limited: freezing the shop must always work.
  Limits are per process and in memory, which suits one shop per backend; several processes behind a
  load balancer would need a shared store.
- Security headers on API responses: no sniffing, no framing, no referrer, a CSP that allows nothing
  (the API only returns data), no caching of the shop's data, and HSTS when served over HTTPS.
- Every response carries `X-Request-ID` (the caller's, if it sent a sane one), and the log line and any
  error response carry it too, so a complaint can be matched to the audit log.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from app.core.ids import new_id

log = logging.getLogger("khatapilot.http")

PIN_ROUTE = re.compile(r"^/api/(?:approvals/[^/]+/approve|owner/(?!stop$|status$|block$)[\w-]+(?:/[\w-]+)*)$")
CHAT_ROUTE = re.compile(r"^/api/(?:chat(?:/.*)?|sim/inbound)$")
EXEMPT = re.compile(r"^/api/(?:owner/stop|health|stream.*)$")
SANE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
API_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cache-control", b"no-store"),
]


class RateLimiter:
    """A sliding one-minute window per (client, bucket)."""

    def __init__(self, per_minute: dict[str, int], clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, client: str, bucket: str) -> float | None:
        """None if allowed (and counted), else the seconds until the next request would be."""
        limit = self.per_minute.get(bucket, 0)
        if limit <= 0:
            return None
        now, window = self.clock(), self.hits[(client, bucket)]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= limit:
            return max(1.0, 60 - (now - window[0]))
        window.append(now)
        return None


def bucket_of(method: str, path: str) -> str | None:
    if not path.startswith("/api/") or EXEMPT.match(path):
        return None
    if method in ("POST", "PUT") and PIN_ROUTE.match(path):
        return "pin"
    if method == "POST" and CHAT_ROUTE.match(path):
        return "chat"
    return "other"


def _metrics_of(scope):
    """The process's metrics, once the app's container exists (it is built when the app starts)."""
    container = getattr(getattr(scope.get("app"), "state", None), "container", None)
    return getattr(container, "metrics", None)


class HttpGuards:
    def __init__(self, app, *, per_minute: dict[str, int], reraise: bool = False) -> None:
        self.app = app
        self.limiter = RateLimiter(per_minute)
        self.reraise = reraise  # tests: let the server error reach the test client

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        client = (scope.get("client") or ("unknown", 0))[0]
        given = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-request-id"), "")
        request_id = given if SANE_ID.match(given) else new_id("http")
        api = path.startswith("/api/")
        https = scope.get("scheme") == "https"
        started, status = time.perf_counter(), {"code": 500, "sent": False}

        def headers_for(extra: list[tuple[bytes, bytes]] | None = None) -> list[tuple[bytes, bytes]]:
            out = [(b"x-request-id", request_id.encode())]
            if api:
                out += API_HEADERS
            if https:
                out.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
            return out + (extra or [])

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                status["code"], status["sent"] = message["status"], True
                taken = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    h for h in headers_for() if h[0] not in taken]
            await send(message)

        bucket = bucket_of(method, path)
        wait = self.limiter.check(client, bucket) if bucket else None
        try:
            if wait is not None:
                await self._json(send_with_headers, 429, {
                    "detail": ("Too many requests in a minute; wait a moment and try again. STOP always works, "
                               "whatever the limit."),
                    "request_id": request_id}, [(b"retry-after", str(int(wait)).encode())])
                return
            await self.app(scope, receive, send_with_headers)
        except Exception:
            log.exception("unhandled error on %s %s (request %s)", method, path, request_id)
            if self.reraise:
                raise
            if not status["sent"]:
                await self._json(send_with_headers, 500, {
                    "detail": ("Something went wrong on our side. Check the audit log before you try a payment "
                               "again, so nothing is done twice."),
                    "request_id": request_id})
        finally:
            if (metrics := _metrics_of(scope)) is not None:
                metrics.observe_request(method, path, status["code"], time.perf_counter() - started, bucket,
                                        limited=wait is not None)
            log.info(json.dumps({"request_id": request_id, "method": method, "path": path, "status": status["code"],
                                 "ms": round((time.perf_counter() - started) * 1000, 1), "client": client,
                                 **({"limited": bucket} if wait is not None else {})}))

    @staticmethod
    async def _json(send: Callable[[dict], Awaitable[None]], code: int, body: dict,
                    extra: list[tuple[bytes, bytes]] | None = None) -> None:
        payload = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": code,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode())] + (extra or [])})
        await send({"type": "http.response.body", "body": payload})
