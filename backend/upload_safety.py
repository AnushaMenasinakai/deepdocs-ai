"""Upload acknowledgement verification and ownership of an existing save task."""
import asyncio
import logging
import threading
from concurrent.futures import Future
from starlette.concurrency import run_in_threadpool
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference

VERIFICATION_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


class UploadUncertain(PyMongoError):
    def __init__(self):
        super().__init__("Upload outcome could not be confirmed.")


def require_acknowledged(collection):
    concern = getattr(collection, "write_concern", None)
    if concern is not None and not concern.acknowledged:
        raise UploadUncertain()


def check_cancellation():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()


async def verify(collection, scope):
    try:
        async with asyncio.timeout(VERIFICATION_TIMEOUT_SECONDS):
            view = collection.with_options(read_preference=ReadPreference.PRIMARY,
                                           read_concern=ReadConcern("majority"))
            row = await view.find_one(scope)
        check_cancellation()
        if row is None or any(row.get(key) != value for key, value in scope.items()):
            raise UploadUncertain()
        return row
    except asyncio.CancelledError:
        raise
    except Exception:
        raise UploadUncertain() from None


class SaveLifetime:
    """Thread-owned completion, independent of cancellation of its async wrapper.

    The lock also seals a not-yet-started worker before closing its stream.
    Owned finalization is dispatched once; running saves may finalize on their
    own thread. Only actual closure and native completion settle ownership.
    """
    def __init__(self, upload):
        self.stream = upload.file
        self.lock = threading.Lock()
        self.state = "pending"
        self.close_requested = False
        self.close_attempted = False
        self.completed = threading.Event()
        self.finalized = threading.Event()
        self.finalization_succeeded = False
        self.compensation_completed = threading.Event()
        self.compensation_state = 'unused'
        self.save_error = None
        self.finalization_error = None
        self.compensation_error = None
        self._terminal = Future()
        self._terminal.set_running_or_notify_cancel()
        self.owned = False
        self._loop = asyncio.get_running_loop()
        self._finalizer = None

    def _finish(self):
        if (self.finalization_succeeded and self.state in ('finished', 'sealed')
                and self.compensation_state in ('unused', 'finished', 'sealed')
                and not self._terminal.done()):
            self._terminal.set_result(None)

    def _close(self):
        # Caller holds the lock and has proved no worker can use the stream.
        if self.close_attempted:
            return
        self.close_attempted = True
        try:
            self.stream.close()
            self.finalization_succeeded = bool(self.stream.closed)
        except Exception as error:
            self.finalization_error = error
            logger.warning("Upload stream finalization requires review.")
        finally:
            self.finalized.set()  # Attempt finished; success is separate.
            self._finish()

    def run(self, save, *args):
        with self.lock:
            if self.state != "pending":
                raise UploadUncertain()
            self.state = "running"
        try:
            return save(*args)
        except BaseException as error:
            self.save_error = error
            raise
        finally:
            with self.lock:
                self.state = "finished"
                self.completed.set()
                if self.close_requested:
                    self._close()
                self._finish()

    def close_when_finished(self):
        with self.lock:
            self.close_requested = True
            if self.state == "pending":
                # A queued wrapper might still dispatch. It must not start save.
                self.state = "sealed"
            if self.compensation_state == 'pending':
                self.compensation_state = 'sealed'
                self.compensation_completed.set()
            if self.state != "running":
                self._close()
            self._finish()

    def prepare_compensation(self):
        with self.lock:
            if self.state != 'finished' or self.compensation_state != 'unused' or self.close_requested:
                raise UploadUncertain()
            self.compensation_state = 'pending'

    def compensate(self, delete, *args):
        with self.lock:
            if self.compensation_state != 'pending':
                raise UploadUncertain()
            self.compensation_state = 'running'
        try:
            return delete(*args)
        except BaseException as error:
            self.compensation_error = error
            raise
        finally:
            with self.lock:
                self.compensation_state = 'finished'
                self.compensation_completed.set()
                self._finish()

    def _start_finalizer(self):
        if self._finalizer is None and not self._terminal.done():
            self._finalizer = asyncio.create_task(run_in_threadpool(self.close_when_finished))
            self._finalizer.add_done_callback(observe_save_result)

    def close(self):
        # Workflow.finish may call this repeatedly. Native completion, never
        # this wrapper's cancellation, decides terminal completion.
        if not self._terminal.done():
            self._loop.call_soon_threadsafe(self._start_finalizer)

    async def finalize(self):
        self._start_finalizer()
        if self._finalizer is not None:
            await asyncio.shield(self._finalizer)


def observe_save_result(task):
    if not task.cancelled():
        task.exception()  # Consume abandoned errors without exposing details.


async def close_upload_form(form):
    lifetime = getattr(form["file"], "_deepdocs_save_lifetime", None)
    if lifetime is None:
        await form.close()
    else:
        # parse_upload accepts exactly one file. Its actual stream is finalized
        # once, without an await that shutdown/request cancellation can interrupt.
        if lifetime.owned:
            await lifetime.finalize()
        else:
            lifetime.close_when_finished()
