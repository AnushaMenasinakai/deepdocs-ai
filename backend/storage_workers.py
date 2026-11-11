"""Disconnected opt-in synchronous storage ownership. No routes or config reads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pymongo import MongoClient
from storage_contract import StorageUncertain


class StorageRejected(RuntimeError):
    """Positive rejection before submission: no operation dispatched."""


class StorageWorkers:
    def __init__(self, client, *, workers=1, queued=1):
        if type(workers) is not int or not 1 <= workers <= 4 or type(queued) is not int or not 0 <= queued <= 4:
            raise ValueError('Invalid storage worker bounds.')
        self.client = client
        self.loop = asyncio.get_running_loop()
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='deepdocs-storage')
        self.limit = workers + queued
        self.pending = {}
        self.sealed = False
        self.closing = None
        self.idle = asyncio.Event()
        self.idle.set()

    def unresolved(self):
        """Safe count only: no arguments, results, credentials or document data."""
        return len(self.pending)

    async def run(self, operation):
        if asyncio.get_running_loop() is not self.loop:
            raise StorageRejected('Storage owner belongs to another event loop.')
        task = asyncio.current_task()
        if task.cancelling():
            raise asyncio.CancelledError
        if self.sealed or len(self.pending) >= self.limit:
            raise StorageRejected('Storage work was not admitted.')
        token = object()
        self.pending[token] = None  # register before dispatch, without an await
        self.idle.clear()
        try:
            future = self.executor.submit(operation)
        except BaseException:
            # An unexpected submit failure may follow dispatch: retain ownership.
            self.sealed = True
            raise StorageUncertain('Storage dispatch outcome requires review.') from None
        self.pending[token] = future
        future.add_done_callback(lambda done: self.loop.call_soon_threadsafe(self._done, token, done))
        wrapped = asyncio.wrap_future(future, loop=self.loop)
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(wrapped)
        finally:
            # A fast completed future can resume this task before its scheduled
            # bookkeeping callback. Reap it now; the callback is idempotent.
            if future.done():
                self._done(token, future)

    def _done(self, token, future):
        # Observe abandoned errors without logging payloads. Cancellation of the
        # request never cancels queued/native work or changes durable intent state.
        if not future.cancelled():
            future.exception()
        self.pending.pop(token, None)
        if not self.pending:
            self.idle.set()

    def begin_shutdown(self):
        self.sealed = True

    async def _drain(self):
        await self.idle.wait()
        await asyncio.to_thread(self.executor.shutdown, wait=True)
        try:
            await asyncio.to_thread(self.client.close)
        except Exception:
            raise StorageUncertain('Storage client cleanup requires review.') from None

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
                if self.closing.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError


@asynccontextmanager
async def optional_storage_lifespan(*, enabled=False, uri=None, client_factory=MongoClient,
                                    workers=1, queued=1):
    """Future application lifespan component; deliberately not installed in main.

    Construction is lazy (connect=False); operations must open/use/close GridFS
    streams entirely inside their callable. Never return a live stream.
    """
    if not enabled:
        yield None
        return
    if type(workers) is not int or not 1 <= workers <= 4 or type(queued) is not int or not 0 <= queued <= 4:
        raise ValueError('Invalid storage worker bounds.')
    try:
        creation = asyncio.create_task(asyncio.to_thread(client_factory, uri, connect=False, tls=True,
            tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
            serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000,
            waitQueueTimeoutMS=5000, timeoutMS=5000, maxPoolSize=workers, minPoolSize=0,
            retryWrites=False, retryReads=False))
        cancelled = False
        while True:
            try:
                client = await asyncio.shield(creation)
                break
            except asyncio.CancelledError:
                if creation.cancelled():
                    raise
                cancelled = True
        if cancelled:
            cleanup = asyncio.create_task(asyncio.to_thread(client.close))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise asyncio.CancelledError
    except Exception:
        raise StorageUncertain('Storage client configuration is unavailable.') from None
    try:
        owner = StorageWorkers(client, workers=workers, queued=queued)
    except BaseException:
        await asyncio.to_thread(client.close)
        raise
    try:
        yield owner
    finally:
        await owner.aclose()
