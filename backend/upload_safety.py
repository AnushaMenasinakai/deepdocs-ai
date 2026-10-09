"""Upload acknowledgement verification and ownership of an existing save task."""
import asyncio
import logging
import threading
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
    Finalization runs synchronously on the request or existing worker thread;
    it needs no surviving event loop or additional background task.
    """
    def __init__(self, upload):
        self.stream = upload.file
        self.lock = threading.Lock()
        self.state = "pending"
        self.close_requested = False
        self.close_attempted = False
        self.completed = threading.Event()

    def _close(self):
        # Caller holds the lock and has proved no worker can use the stream.
        if self.close_attempted:
            return
        self.close_attempted = True
        try:
            self.stream.close()
        except Exception:
            logger.warning("Upload stream finalization requires review.")

    def run(self, save, *args):
        with self.lock:
            if self.state != "pending":
                raise UploadUncertain()
            self.state = "running"
        try:
            return save(*args)
        finally:
            with self.lock:
                self.state = "finished"
                self.completed.set()
                if self.close_requested:
                    self._close()

    def close_when_finished(self):
        with self.lock:
            self.close_requested = True
            if self.state == "pending":
                # A queued wrapper might still dispatch. It must not start save.
                self.state = "sealed"
            if self.state != "running":
                self._close()


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
        lifetime.close_when_finished()
