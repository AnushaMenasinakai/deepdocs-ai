"""Process-local authentication protection owned by an application lifespan.

Client addresses must come from a separately verified ASGI/proxy trust policy.
Account strings must use the application's validated email normalization. No
forwarded headers are accepted here. Limits reset with the owning process.
"""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import math
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request

from config import AuthProtectionSettings, load_auth_protection_settings


def normalize_client_address(address):
    try:
        if not isinstance(address, str) or "%" in address:
            return "unknown"
        value = ipaddress.ip_address(address.strip())
        if isinstance(value, ipaddress.IPv6Address) and value.ipv4_mapped:
            value = value.ipv4_mapped
        return str(value)
    except ValueError:
        return "unknown"


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    retry_after: int = 0


@dataclass
class _Bucket:
    tokens: float
    updated: float
    used: float
    capacity: int
    rate: float

    def available(self, now):
        return min(self.capacity, self.tokens + max(0, now - self.updated) * self.rate)


class AuthRateLimiter:
    """Atomic combined token buckets, bounded state and round-robin cleanup.

Rejected requests do not consume tokens or extend bucket retention. Depleted
entries are never evicted to admit new keys. No account-only lockout exists.
"""
    def __init__(self, settings=None, *, clock=time.monotonic, key_secret=None):
        self.settings = settings or AuthProtectionSettings()
        self._clock = clock
        self._secret = secrets.token_bytes(32) if key_secret is None else key_secret
        if not isinstance(self._secret, bytes) or len(self._secret) < 32:
            raise ValueError("A private limiter key of at least 32 bytes is required.")
        self._buckets = OrderedDict()
        self._lock = threading.Lock()

    @property
    def entry_count(self):
        with self._lock:
            return len(self._buckets)

    def account_key(self, normalized_account):
        if not isinstance(normalized_account, str) or not normalized_account or len(normalized_account) > 254:
            raise ValueError("A validated account identifier is required.")
        return hmac.new(self._secret, normalized_account.encode("utf-8"), hashlib.sha256).digest()

    def _cleanup(self, now):
        for _ in range(min(32, len(self._buckets))):
            key, bucket = self._buckets.popitem(last=False)
            if not (now - bucket.used >= self.settings.limiter_idle_seconds
                    and bucket.available(now) >= bucket.capacity):
                self._buckets[key] = bucket

    def check(self, action, address, normalized_account=None):
        ip = normalize_client_address(address)
        s = self.settings
        if action == "login":
            rules = [((action, "ip", ip), s.login_ip_burst, s.login_ip_refill_per_minute / 60),
                     ((action, "account", ip, self.account_key(normalized_account)),
                      s.login_account_burst, s.login_account_refill_per_minute / 60)]
        elif action == "register":
            rules = [((action, "ip", ip), s.register_ip_burst, s.register_ip_refill_per_hour / 3600)]
        else:
            raise ValueError("Unsupported authentication action.")
        with self._lock:
            now = self._clock()
            self._cleanup(now)
            previews = []
            retry = 0
            for key, capacity, rate in rules:
                bucket = self._buckets.get(key)
                tokens = bucket.available(now) if bucket else capacity
                previews.append((key, capacity, rate, tokens))
                if tokens < 1:
                    retry = max(retry, max(1, math.ceil((1 - tokens) / rate)))
            if retry:
                return LimitDecision(False, retry)
            missing = sum(key not in self._buckets for key, _, _, _ in previews)
            if len(self._buckets) + missing > s.limiter_max_entries:
                return LimitDecision(False, 60)
            for key, capacity, rate, tokens in previews:
                self._buckets[key] = _Bucket(tokens - 1, now, now, capacity, rate)
            return LimitDecision(True)


class PasswordCapacityExceeded(Exception):
    def __init__(self):
        super().__init__("Password operation capacity is unavailable.")


class PasswordExecutor:
    """Explicitly owned executor with at most N outstanding jobs and no waiters.

Use within one application lifespan. Cancellation cannot stop a running native
call: its slot remains occupied until the concurrent future actually completes.
    The owner must await aclose(). Cancellation is propagated only after draining
    the executor, including when the shutdown waiter is cancelled repeatedly.
"""
    def __init__(self, max_outstanding=2, *, executor_factory=ThreadPoolExecutor):
        if type(max_outstanding) is not int or not 1 <= max_outstanding <= 8:
            raise ValueError("Password capacity must be between 1 and 8.")
        self._limit = max_outstanding
        self._executor = executor_factory(max_workers=max_outstanding, thread_name_prefix="auth-password")
        self._lock = threading.RLock()
        self._outstanding = 0
        self._closed = False
        self._shutdown_task = None

    @property
    def outstanding(self):
        with self._lock:
            return self._outstanding

    def _finished(self, future):
        # Retrieve exceptions even if the request has disappeared. Never log
        # exception text or function arguments, which may contain credentials.
        if not future.cancelled():
            future.exception()
        with self._lock:
            self._outstanding -= 1

    async def run(self, function, *args, **kwargs):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        with self._lock:
            if self._closed or self._outstanding >= self._limit:
                raise PasswordCapacityExceeded()
            self._outstanding += 1
            try:
                future = self._executor.submit(function, *args, **kwargs)
            except BaseException:
                self._outstanding -= 1
                raise
            future.add_done_callback(self._finished)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            future.cancel()  # Only succeeds before execution; never frees a running slot.
            raise

    async def aclose(self):
        with self._lock:
            self._closed = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(asyncio.to_thread(
                self._executor.shutdown, wait=True, cancel_futures=True))
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._shutdown_task)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


def too_many_requests(retry_after):
    return HTTPException(429, "Too many authentication requests. Please try again later.",
                         headers={"Retry-After": str(max(1, retry_after)), "Cache-Control": "no-store"})


class AuthProtection:
    def __init__(self, settings, *, clock=time.monotonic):
        self.limiter = AuthRateLimiter(settings, clock=clock)
        self.passwords = PasswordExecutor(settings.password_max_concurrent)
        self.closed = False

    def admit(self, action, request, account):
        if self.closed:
            raise too_many_requests(1)
        # ASGI identity only. Deployment must verify Uvicorn/proxy trust;
        # forwarded headers are never interpreted by this application.
        address = request.client.host if request.client else None
        decision = self.limiter.check(action, address, account)
        if not decision.allowed:
            raise too_many_requests(decision.retry_after)

    async def aclose(self):
        self.closed = True
        await self.passwords.aclose()


def get_auth_protection(request: Request):
    service = getattr(request.app.state, "auth_protection", None)
    if service is None:
        raise HTTPException(503, "Authentication service is temporarily unavailable.")
    return service


@asynccontextmanager
async def auth_protection_lifespan(app):
    app.state.auth_protection = None
    try:
        service = AuthProtection(load_auth_protection_settings())
    except Exception:
        # Do not start serving authentication without validated protection.
        raise RuntimeError("Authentication protection initialization failed.") from None
    app.state.auth_protection = service
    try:
        yield
    finally:
        try:
            await service.aclose()
        finally:
            app.state.auth_protection = None
