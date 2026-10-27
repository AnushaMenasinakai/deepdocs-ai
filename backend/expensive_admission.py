"""Isolated process-local admission; no executor, route or global service.

Own one Admission instance per future application lifespan. Acquire before
side effects, and use reservations in a ``with`` or ``finally`` block. A split
transfers a subset of an atomic reservation so workflow and CPU lifetimes can
differ. Never share a request-owned reservation with an untracked task.

``Reservation.run(submit, ...)`` transfers ownership to a concurrent Future.
The injected submitter must be non-blocking and return a standard concurrent
Future, or raise BEFORE dispatching work. It must not hide a queue behind an
unreserved job. An asyncio Task is not a worker-completion signal for native
work. No executor is constructed here. Calling close() seals admission but
does not drain workers; the future lifecycle owner must drain its executor
before closing resources. Local release never authorizes database unlocking.
"""
import asyncio
from concurrent.futures import Future
from enum import Enum
import threading

from fastapi import HTTPException
from config import ExpensiveAdmissionSettings


class Resource(str, Enum):
    UPLOAD = 'upload'
    DOCUMENT = 'document'
    QUERY = 'query'
    CPU = 'cpu'
    GEMINI = 'gemini'


class CapacityExceeded(Exception):
    """No resource details or user data are attached to a rejection."""


def capacity_unavailable():
    return HTTPException(503, 'Service is busy. Please try again later.',
                         headers={'Cache-Control': 'no-store', 'Retry-After': '1'})


def _resources(values):
    if not values or any(not isinstance(value, Resource) for value in values) or len(set(values)) != len(values):
        raise ValueError('Provide distinct named resource classes.')
    return frozenset(values)


class Admission:
    def __init__(self, settings: ExpensiveAdmissionSettings):
        self._limits = dict(zip(Resource, (settings.upload_max_inflight,
            settings.document_operation_max_inflight, settings.query_max_inflight,
            settings.expensive_cpu_max_outstanding, settings.gemini_max_inflight)))
        self._used = dict.fromkeys(Resource, 0)
        self._lock = threading.RLock()
        self._closed = False
        self._changed = lambda: None

    @property
    def outstanding(self):
        """Copy for lifecycle diagnostics/tests; never include in HTTP errors."""
        with self._lock:
            return dict(self._used)

    def acquire(self, *resources):
        names = _resources(resources)
        with self._lock:
            if self._closed or any(self._used[name] >= self._limits[name] for name in names):
                raise CapacityExceeded()
            reservation = Reservation(self, names)
            for name in names:
                self._used[name] += 1
            return reservation

    def close(self):
        """Idempotently reject new admissions/dispatch; retain all owned slots."""
        with self._lock:
            self._closed = True


class Reservation:
    def __init__(self, owner, resources):
        # Construct only via Admission.acquire or Reservation.split.
        self._owner = owner
        self._resources = resources
        self._state = 'held'
        self._future = None
        self._group = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

    def split(self, *resources):
        names = _resources(resources)
        with self._owner._lock:
            if self._state != 'held' or not names <= self._resources:
                raise ValueError('Reservation cannot transfer those resources.')
            child = Reservation(self._owner, names)
            child._group = self._group
            if self._group is not None:
                self._group.add(child)
            self._resources = self._resources - names
            if not self._resources:
                self._state = 'released'
                if self._group is not None:
                    self._group.discard(self)
            return child

    def _release(self):
        if self._state != 'released':
            for name in self._resources:
                self._owner._used[name] -= 1
            self._state = 'released'
            self._resources = frozenset()
            if self._group is not None:
                self._group.discard(self)
            self._owner._changed()

    def release(self):
        """Release request ownership only; cannot release worker-owned capacity."""
        with self._owner._lock:
            if self._state == 'held':
                self._release()

    def cancel(self):
        """Successful pre-start cancellation releases through the done callback."""
        with self._owner._lock:
            if self._state == 'held':
                self._release()
            elif self._state == 'worker' and self._future is not None:
                self._future.cancel()

    def _finished(self, future):
        # Consume abandoned worker errors without logging sensitive arguments.
        try:
            if not future.cancelled():
                future.exception()
        finally:
            with self._owner._lock:
                self._release()
                self._future = None

    def submit(self, submitter, *args, **kwargs):
        with self._owner._lock:
            if self._state != 'held':
                raise RuntimeError('Reservation is no longer request-owned.')
            if self._owner._closed:
                self._release()
                raise CapacityExceeded()
            try:
                future = submitter(*args, **kwargs)
            except BaseException:
                self._release()
                raise
            # A broken submitter may already have dispatched work: retain the
            # reservation rather than pretending that work has stopped.
            self._state = 'worker'
            if not isinstance(future, Future):
                raise TypeError('Submitter must return a concurrent Future.')
            self._future = future
            future.add_done_callback(self._finished)
            return future

    async def run(self, submitter, *args, **kwargs):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self.release()
            raise asyncio.CancelledError
        future = self.submit(submitter, *args, **kwargs)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            self.cancel()
            raise
