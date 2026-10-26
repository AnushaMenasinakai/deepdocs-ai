"""ASGI byte boundary and registered FastAPI route selection.

``select(scope)`` must match method/path before routing; scope['route'] is not
available here. JSONRouteSelector derives its predicate from ordered effective
APIRoute metadata, including prefixes and dynamic paths. Do not select by
Content-Type: FastAPI buffers unsuitable media types too. Multipart and bodyless
routes are excluded. Metadata is revalidated when routes are subsequently added.

The component buffers no body. Downstream may buffer up to the limit, and the
server has already allocated each incoming event. This is not a global memory
or slow-client limit. Cancellation/disconnection are never converted to EOF.
"""
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from fastapi.params import Body, Form
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Match, Route, WebSocketRoute
from config import load_json_body_settings

DETAIL = "JSON request body exceeds the configured size limit."


class JSONRouteSelector:
    """Use effective registered routes, including prefixes and hidden endpoints.

    Validate before serving; rederive contexts on requests so later additions
    cannot silently bypass a frozen inventory. Unknown route types fail closed.
    The same first full match as the router wins, including bodyless shadows.
    """
    def __init__(self, router):
        self.router = router
        self.entries()

    def entries(self):
        try:
            result = []
            for context in iter_route_contexts(self.router.routes):
                original = context.original_route
                if isinstance(original, WebSocketRoute):
                    continue
                if not isinstance(original, Route) or not context.methods:
                    raise ValueError
                protected = False
                if isinstance(original, APIRoute):
                    field = context.body_field
                    if field is not None:
                        info = field.field_info
                        if isinstance(info, Form):
                            pass
                        elif isinstance(info, Body) and (info.media_type == 'application/json' or
                                info.media_type.startswith('application/') and info.media_type.endswith('+json')):
                            protected = True
                        else:
                            raise ValueError
                    else:
                        # Manual multipart parsing has no body_field. Do not
                        # silently accept a manually documented JSON endpoint.
                        content = (context.openapi_extra or {}).get('requestBody', {}).get('content', {})
                        if any(key != 'multipart/form-data' for key in content):
                            raise ValueError
                elif context.methods - {'GET', 'HEAD', 'OPTIONS'}:
                    raise ValueError
                result.append((context, protected))
            return result
        except (AttributeError, TypeError, ValueError):
            raise RuntimeError('JSON request boundary route metadata is unsupported.') from None

    def __call__(self, scope):
        for context, protected in self.entries():
            if context.matches(scope)[0] == Match.FULL:
                return protected and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}
        return False


class RegisteredJSONBodyBoundary:
    """Lazy middleware construction validates config/routes before lifespan work."""
    def __init__(self, app, *, router):
        settings = load_json_body_settings()
        self.boundary = JSONBodyBoundary(app, max_bytes=settings.max_body_bytes,
                                         select=JSONRouteSelector(router))

    async def __call__(self, scope, receive, send):
        await self.boundary(scope, receive, send)


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
