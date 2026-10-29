"""Isolated CPU lanes. No model imports, PDF parsing or route integration.

Use a session context inside its owning workflow context. Sessions keep one CPU
slot and their lane until close AND actual native completion. A single-slot
mailbox hands accepted embedding work to one thread; it is not an admission
waiting queue. Excess sessions/batches are rejected before submission.

The PDF lane uses an application-owned lazy spawn supervisor. Its submitter must
return a concurrent Future representing genuine completion (including process
exit/reaping on failure). It may raise SubmissionRejected ONLY before dispatch.
Other submission failures retain ownership: completion cannot be invented.
No business operation uses the PDF supervisor yet. No crash recovery is supplied.
"""
import asyncio
from concurrent.futures import Future
from contextlib import contextmanager
from fastapi import Request
from enum import Enum
from queue import Queue, Full
from threading import Thread

from expensive_admission import CapacityExceeded, Resource, capacity_unavailable
from admission_lifecycle import Workflow


def get_cpu_workers(request: Request):
    workers = getattr(request.app.state, 'cpu_workers', None)
    if workers is None:
        raise capacity_unavailable()
    return workers


@contextmanager
def embedding_capacity_errors():
    try:
        yield
    except CapacityExceeded:
        raise capacity_unavailable() from None


class Lane(str, Enum):
    EMBEDDING = 'embedding'
    PDF = 'pdf'


class SubmissionRejected(RuntimeError):
    """Trusted adapter guarantees no underlying work was dispatched."""


class WorkerInfrastructureFailure(RuntimeError):
    def __init__(self):
        super().__init__('CPU worker execution is unavailable.')


class PDFParsingFailure(ValueError):
    """Recognized parser failure, distinct from lost worker/IPC completion.

    Future adapters must translate only verified, sanitized parser outcomes.
    This foundation does not classify or parse PDF input.
    """


def _invoke(function, args, kwargs):
    try:
        return function(*args, **kwargs)
    except StopIteration:
        raise WorkerInfrastructureFailure() from None


class CPUWorkers:
    def __init__(self, admission):
        self._admission = admission
        self._lock = admission._admission._lock
        self._loop = asyncio.get_running_loop()
        self._lanes = dict.fromkeys(Lane)
        self._closing = False
        self._draining = False
        self._idle = self._loop.create_future()
        self._idle_scheduled = False
        self._join_task = None
        self._pdf_supervisor = None
        self._mailbox = Queue(maxsize=1)
        self._thread = Thread(target=self._work, name='deepdocs-embedding', daemon=False)
        try:
            self._thread.start()
        except Exception:
            raise WorkerInfrastructureFailure() from None

    def _work(self):
        while True:
            item = self._mailbox.get()
            if item is None:
                return
            future, function, args, kwargs = item
            if future.set_running_or_notify_cancel():
                try:
                    result = _invoke(function, args, kwargs)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)

    def _submit_embedding(self, function, *args, **kwargs):
        future = Future()
        try:
            self._mailbox.put_nowait((future, function, args, kwargs))
        except Full:
            raise SubmissionRejected('CPU work was not dispatched.') from None
        return future

    def _submit_pdf(self, path, settings):
        from pdf_subprocess import PDFSupervisor, PDFSubmissionRejected
        try:
            if self._pdf_supervisor is None:
                self._pdf_supervisor = PDFSupervisor()
            return self._pdf_supervisor.submit(path, settings)
        except PDFSubmissionRejected:
            raise SubmissionRejected('PDF work was not dispatched.') from None

    def session(self, workflow, lane=Lane.EMBEDDING):
        """Dispatch authority belongs to a live registered workflow of this app.

        Allowed during graceful drain, never after the final worker seal.
        Workflow cleanup closes this session and retains its workflow resources
        until the session's native work genuinely completes.
        """
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if not isinstance(lane, Lane):
            raise ValueError('Provide a named CPU lane.')
        with self._lock:
            if getattr(workflow, '_service', None) is not self._admission:
                raise ValueError('Workflow belongs to another application.')
            if not isinstance(workflow, Workflow) or workflow not in self._admission._owners:
                raise CapacityExceeded()
            workflow._authorize(self._admission)
            if self._closing or self._lanes[lane] is not None:
                raise CapacityExceeded()
            reservation = workflow.acquire(Resource.CPU)
            session = ExecutionSession(self, lane)
            # Terminal ownership is marked running: request cancellation cannot
            # cancel this signal while a native operation is still alive.
            reservation.submit(lambda: session._terminal)
            self._lanes[lane] = session
            workflow._own_session(session)
            return session

    def workflow(self, *resources):
        """Register with the same per-application owner used by worker handoff."""
        return self._admission.workflow(*resources)

    def _check_idle(self):
        if self._closing and not any(self._lanes.values()) and not self._idle_scheduled:
            self._idle_scheduled = True
            self._loop.call_soon_threadsafe(self._idle.set_result, None)

    @contextmanager
    def pdf_processing_session(self):
        """Atomic DOCUMENT/CPU/lane admission before any document claim.

        Both resources remain worker-owned after cancellation until reaping.
        The session spans publication too; no second CPU acquisition is needed.
        """
        with self._lock:
            if self._closing or self._draining or self._lanes[Lane.PDF] is not None:
                raise CapacityExceeded()
            workflow = self._admission.workflow(Resource.DOCUMENT, Resource.CPU)
            session = ExecutionSession(self, Lane.PDF)
            workflow.reservation.submit(lambda: session._terminal)
            self._lanes[Lane.PDF] = session
        try:
            yield session
        finally:
            session.close()
            workflow.finish()

    @contextmanager
    def embedding_session(self):
        """CPU-only workflow: no query or document admission is enabled here."""
        with self._lock:
            if self._closing or self._draining or self._lanes[Lane.EMBEDDING] is not None:
                raise CapacityExceeded()
            workflow = self._admission.workflow(Resource.CPU)
            session = ExecutionSession(self, Lane.EMBEDDING)
            workflow.reservation.submit(lambda: session._terminal)
            self._lanes[Lane.EMBEDDING] = session
        try:
            yield session
        finally:
            session.close()
            workflow.finish()

    def begin_drain(self):
        """Reject unrelated sessions; live workflow owners may still dispatch.

        Do not latch idle here: an admitted workflow may not need CPU until later.
        The lifespan must drain workflows before calling aclose's final seal.
        """
        with self._lock:
            self._admission.begin_shutdown()
            self._draining = True

    def begin_shutdown(self):
        with self._lock:
            self._closing = True
            self._check_idle()

    async def _join(self):
        await self._idle
        # There can still be a cancelled, never-started mailbox item. This
        # blocking handoff and join run outside the event loop.
        def join():
            if self._pdf_supervisor is not None:
                self._pdf_supervisor.close()
            self._mailbox.put(None)
            self._thread.join()
        await asyncio.to_thread(join)

    async def aclose(self):
        self.begin_shutdown()
        if self._join_task is None:
            self._join_task = asyncio.create_task(self._join())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self._join_task)
                break
            except asyncio.CancelledError:
                if self._join_task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


class ExecutionSession:
    def __init__(self, owner, lane):
        self._owner = owner
        self._lane = lane
        self._terminal = Future()
        self._terminal.set_running_or_notify_cancel()
        self._closed = False
        self._inflight = False
        self._native = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _finish(self):
        if self._closed and not self._inflight and not self._terminal.done():
            self._owner._lanes[self._lane] = None
            self._terminal.set_result(None)
            self._owner._check_idle()

    def close(self):
        with self._owner._lock:
            self._closed = True
            self._finish()

    def _completed(self, native, result):
        # Retrieve abandoned worker exceptions without logging arguments.
        error = None if native.cancelled() else native.exception()
        value = None if native.cancelled() or error is not None else native.result()
        if isinstance(error, StopIteration):
            error = WorkerInfrastructureFailure()
        with self._owner._lock:
            if self._native is not native:
                return  # Defensive duplicate/stale completion protection.
            self._native = None
            self._inflight = False
            self._finish()
        if native.cancelled():
            result.set_exception(asyncio.CancelledError())
        elif error is not None:
            result.set_exception(error)
        else:
            result.set_result(value)

    def _submit(self, submitter, *args, **kwargs):
        with self._owner._lock:
            if self._closed or self._inflight:
                raise CapacityExceeded()
            self._inflight = True
            try:
                native = submitter(*args, **kwargs)
            except SubmissionRejected:
                self._inflight = False
                self._closed = True
                self._finish()
                raise
            except BaseException:
                # The adapter may have dispatched before raising. Fail closed.
                self._closed = True
                raise WorkerInfrastructureFailure() from None
            if not isinstance(native, Future):
                self._closed = True
                raise WorkerInfrastructureFailure()
            self._native = native
            result = Future()
            result.set_running_or_notify_cancel()
            native.add_done_callback(lambda done: self._completed(done, result))
            return result

    def submit(self, function, *args, **kwargs):
        if self._lane is not Lane.EMBEDDING:
            raise ValueError('PDF sessions require a completion adapter.')
        return self._submit(self._owner._submit_embedding, function, *args, **kwargs)

    def submit_pdf(self, submitter, *args, **kwargs):
        """Trusted completion adapter; never accepts an asyncio Task."""
        if self._lane is not Lane.PDF:
            raise ValueError('This session is not a PDF lane.')
        return self._submit(submitter, *args, **kwargs)

    async def run_pdf(self, path, settings):
        """Use the application supervisor; input settings must be primitives."""
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self.close()
            raise asyncio.CancelledError
        result = self.submit_pdf(self._owner._submit_pdf, path, settings)
        wrapped = asyncio.wrap_future(result)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            self.close()  # completion future remains owned until reap/IPC close
            raise

    async def run(self, function, *args, **kwargs):
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            self.close()
            raise asyncio.CancelledError
        result = self.submit(function, *args, **kwargs)
        wrapped = asyncio.wrap_future(result)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            with self._owner._lock:
                self.close()
                if self._native is not None:
                    self._native.cancel()  # Only pre-execution cancellation succeeds.
            raise
