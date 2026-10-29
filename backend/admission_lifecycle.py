"""Application-owned workflow drain; no executors, queues or recovery.

Register with ``with service.workflow(Resource.QUERY) as workflow`` and split
workflow.reservation for worker dispatch. CPUWorkers.session(workflow) explicitly
authorizes staged dispatch and attaches genuine session completion ownership.
Exit closes attached sessions and defers held reservation release until they
complete; real concurrent Future completion survives request cancellation.
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
        self._owners = set()
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
            workflow = Workflow(self, reservation)
            self._owners.add(workflow)
            return workflow

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
        self._native = {}
        self._released = False

    def _authorize(self, service):
        # Identity membership, not just a matching service attribute.
        if service is not self._service or self not in service._owners or self._finished:
            raise CapacityExceeded()

    def _own_session(self, session):
        """Internal handoff of a non-cancellable genuine session terminal.

        Request cleanup closes sessions, but never fabricates native completion.
        Only worker adapters may supply these signals; asyncio tasks are not proof.
        """
        with self._service._admission._lock:
            self._authorize(self._service)
            terminal = session._terminal
            self._native[terminal] = session
            terminal.add_done_callback(self._native_finished)

    def _native_finished(self, terminal):
        with self._service._admission._lock:
            self._native.pop(terminal, None)
            self._release_if_complete()

    def _release_if_complete(self):
        if not self._finished or self._native or self._released:
            return
        self._released = True
        for reservation in tuple(self._reservations):
            reservation.release()
        self._service._owners.remove(self)
        self._service._active -= 1
        self._service._check_drained()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.finish()

    def acquire(self, *resources):
        """Non-waiting stage admission for this still-owned workflow."""
        with self._service._admission._lock:
            self._authorize(self._service)
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
            for session in tuple(self._native.values()):
                session.close()
            self._release_if_complete()
