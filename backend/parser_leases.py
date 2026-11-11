"""Application-owned materialization boundary on the existing CPU PDF lane.

Only the application CPU lane supplies supervisor completion.
Requests cannot supply futures or authorize cleanup after dispatch.
"""
import asyncio
from concurrent.futures import Future
from dataclasses import dataclass
from storage_contract import MaterializedPDF, StorageUncertain
from storage_workers import StorageRejected
from cpu_workers import SubmissionRejected


@dataclass
class _Job:
    lease: object = None
    abandoned: bool = False
    task: object = None
    stage: str = 'materializing'
    session: object = None
    context: object = None


class ParserLeases:
    def __init__(self, storage_workers, cpu_workers):
        self.workers = storage_workers
        self.loop = asyncio.get_running_loop()
        self.cpu_workers = cpu_workers
        self.jobs = {}
        self.sealed = False
        self.idle = asyncio.Event()
        self.idle.set()
        self.closing = None

    def unresolved(self):
        return tuple(job.stage for job in self.jobs.values())

    async def parse(self, materialize, settings):
        if asyncio.get_running_loop() is not self.loop:
            raise StorageRejected('Parser belongs to another application loop.')
        if asyncio.current_task().cancelling():
            raise asyncio.CancelledError
        if self.sealed or self.jobs:
            raise StorageRejected('Parser lease capacity unavailable.')
        key, job = object(), _Job()
        job.context = self.cpu_workers.pdf_processing_session()
        job.session = job.context.__enter__()
        job.session._retain_parser_lease(job)
        self.jobs[key] = job  # CPU and application ownership precede materialization
        self.idle.clear()
        job.task = asyncio.create_task(self._execute(key, job, materialize, settings))
        job.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        try:
            return await asyncio.shield(job.task)
        except asyncio.CancelledError:
            job.abandoned = True
            raise

    async def _execute(self, key, job, materialize, settings):
        safe = True
        try:
            # Store the lease inside the application-owned job in the worker,
            # before returning it; abandoned request results cannot lose it.
            def download():
                value = materialize()
                if not isinstance(value, MaterializedPDF):
                    raise TypeError('MaterializedPDF required.')
                job.lease = value
            try:
                await self.workers.run(download)
            except (StorageUncertain, asyncio.CancelledError):
                safe = False  # dispatch may have happened without a handle
                job.stage = 'materialization_unresolved'
                raise
            if job.abandoned:
                return None
            job.stage = 'parser_dispatched'
            safe = False
            try:
                native = job.session._submit_leased_pdf(str(job.lease.path), settings)
            except SubmissionRejected:
                safe = True  # trusted positive evidence of no dispatch
                raise
            except BaseException:
                job.stage = 'dispatch_unresolved'
                raise StorageUncertain('Parser dispatch requires review.') from None
            if not isinstance(native, Future):
                job.stage = 'dispatch_unresolved'
                raise StorageUncertain('Parser completion handle is invalid.')
            # No await between dispatch and acquisition of trusted completion.
            wrapped = asyncio.wrap_future(native)
            try:
                result = await asyncio.shield(wrapped)
            except asyncio.CancelledError:
                # Native PDF futures are marked running. An unexpected direct
                # cancellation of this owned task must not authorize cleanup.
                job.stage = 'completion_unresolved'
                raise
            except BaseException:
                safe = native.done() and not native.cancelled()
                raise
            else:
                safe = True  # supervisor publishes only after reaping/IPC close
                return result
        finally:
            if safe:
                if job.lease is not None:
                    job.stage = 'cleanup'
                    try:
                        # Cleanup off-loop; ownership remains until completion.
                        await asyncio.to_thread(job.lease.close)
                        if not job.lease.cleanup_complete:
                            raise StorageUncertain('Temporary PDF still has an owner.')
                    except BaseException:
                        job.stage = 'cleanup_failed'
                        raise StorageUncertain('Temporary PDF cleanup requires review.') from None
                job.session._release_parser_lease()
                job.context.__exit__(None, None, None)
                self.jobs.pop(key, None)
                if not self.jobs:
                    self.idle.set()

    def begin_shutdown(self):
        self.sealed = True

    async def _drain(self):
        await self.idle.wait()

    async def aclose(self):
        self.begin_shutdown()
        if self.closing is None:
            self.closing = asyncio.create_task(self._drain())
        cancelled = False
        while True:
            try:
                await asyncio.shield(self.closing)
                break
            except asyncio.CancelledError:
                if self.closing.cancelled(): raise
                cancelled = True
        if cancelled: raise asyncio.CancelledError
