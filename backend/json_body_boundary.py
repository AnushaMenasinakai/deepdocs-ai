"""Opt-in ASGI byte boundary, not yet installed in the application.

``select(scope)`` must match method/path before routing; scope['route'] is not
available here. Integration must derive its predicate after route registration
from ordered APIRoute metadata (body_field, excluding Form), including dynamic
paths, and validate coverage when the route table is finalized. Do not select
by Content-Type: FastAPI buffers unsuitable media types too. Exclude multipart
and bodyless routes. Rebuild/validate the matcher if routes are subsequently
added. A coverage test must compare all JSON-body routes with the matcher.

The component buffers no body. Downstream may buffer up to the limit, and the
server has already allocated each incoming event. This is not a global memory
or slow-client limit. Cancellation/disconnection are never converted to EOF.
"""
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DETAIL = "JSON request body exceeds the configured size limit."


class BodyLimitExceeded(Exception):
    """Abort downstream reading; deliberately contains no request data."""


class JSONBodyBoundary:
    def __init__(self, app: ASGIApp, *, max_bytes: int, select: Callable[[Scope], bool]):
        # Configuration enforces deployment bounds. Small positive limits also
        # support deterministic isolated harnesses and reuse outside FastAPI.
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("A positive integer body limit is required.")
        self.app = app
        self.max_bytes = max_bytes
        self.select = select

    def declared_oversize(self, scope: Scope) -> bool:
        values = [value for name, value in scope.get("headers", [])
                  if name.lower() == b"content-length"]
        if len(values) != 1:
            return False
        value = values[0].strip(b" \t")
        if not value or not value.isdigit():
            return False
        # Avoid integer conversion limits for arbitrarily long decimal headers.
        value = value.lstrip(b"0") or b"0"
        limit = str(self.max_bytes).encode("ascii")
        return len(value) > len(limit) or (len(value) == len(limit) and value > limit)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}
                or not self.select(scope)):
            return await self.app(scope, receive, send)

        async def reject():
            await JSONResponse({"detail": DETAIL}, status_code=413,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)

        if self.declared_oversize(scope):
            await reject()
            return

        size = 0
        rejected = False
        started = False

        async def bounded_receive():
            nonlocal size, rejected
            if rejected:
                raise BodyLimitExceeded()
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > self.max_bytes:
                    rejected = True
                    raise BodyLimitExceeded()
            return message

        async def guarded_send(message):
            nonlocal started
            # FastAPI can turn receive exceptions into parsing errors. Do not
            # forward that response (or any response from code swallowing the
            # exception); the outer boundary owns the sole 413 response.
            if rejected:
                raise BodyLimitExceeded()
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, guarded_send)
        except BodyLimitExceeded:
            if not rejected or started:
                # A response already on the wire cannot safely be replaced.
                # Abort instead; never emit a second response start.
                raise
        if rejected:
            if started:
                raise BodyLimitExceeded()
            await reject()
