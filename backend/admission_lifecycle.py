"""Application-owned workflow drain; no executors, queues or recovery.

Register with ``with service.workflow(Resource.QUERY) as workflow`` and split
workflow.reservation for worker dispatch. Exit releases held reservations only;
real concurrent Future completion retains ownership after request cancellation.
Existing workflows may acquire additional stage reservations during drain;
acquisition is always non-waiting and never promises future capacity.
Submitters must obey Reservation.submit's contract. Never register an asyncio
Task as proof of native completion. A broken submitter deliberately blocks drain.
"""
import asyncio

from expensive_admission import Admission, CapacityExceeded


class AdmissionService:
    def __init__(self, settings):
        self._admission = Admission(settings)
        self._loop = asyncio.get_running_loop()
        self._drained = self._loop.create_future()
        self._closing = False
        self._drained_scheduled = False
        self._active = 0
        # Even workflows which split/release all slots remain bounded.
        self._max_workflows = sum(self._admission._limits.values())
        self._admission._changed = self._check_drained

    def workflow(self, *resources):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        with self._admission._lock:
            if self._closing or self._active >= self._max_workflows:
                raise CapacityExceeded()
            reservation = self._admission.acquire(*resources)
            self._active += 1
            return Workflow(self, reservation)

    def _check_drained(self):
        # Called under the admission lock, including from worker threads.
        if self._closing and not self._active and not any(self._admission._used.values()):
            self._admission.close()
            if not self._drained_scheduled:
                self._drained_scheduled = True
                self._loop.call_soon_threadsafe(self._drained.set_result, None)

    def begin_shutdown(self):
        with self._admission._lock:
            if not self._closing:
                self._closing = True
            self._check_drained()

    async def aclose(self):
        """One shared notification; cancellation propagates only after real drain."""
        self.begin_shutdown()
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._drained)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


class Workflow:
    def __init__(self, service, reservation):
        self._service = service
        self.reservation = reservation
        self._reservations = {reservation}
        reservation._group = self._reservations
        self._finished = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()

    def acquire(self, *resources):
        """Non-waiting stage admission for this still-owned workflow."""
        with self._service._admission._lock:
            if self._finished:
                raise CapacityExceeded()
            reservation = self._service._admission.acquire(*resources)
            reservation._group = self._reservations
            self._reservations.add(reservation)
            return reservation

    def finish(self):
        """End request ownership without cancelling or releasing running work."""
        with self._service._admission._lock:
            if self._finished:
                return
            self._finished = True
            for reservation in tuple(self._reservations):
                reservation.release()
            self._service._active -= 1
            self._service._check_drained()
