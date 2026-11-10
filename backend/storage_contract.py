"""Internal synchronous storage boundary; implementations run in owned workers.

Not wired into routes. A materialization must be retained by the supervisor's
native completion future, never by the cancellable request task.
"""
from concurrent.futures import Future
from pathlib import Path
from threading import Lock
from typing import Protocol
from document_storage import DocumentStorage, StorageError


class StorageUncertain(StorageError):
    """Remote mutation may have committed. Do not compensate or release quota."""


class MaterializedPDF:
    def __init__(self, path, cleanup=lambda: None):
        self.path = Path(path)
        self._cleanup = cleanup
        self._lock = Lock()
        self._pending = 0
        self._closing = False
        self._closed = False

    def retain_until(self, completion):
        if not isinstance(completion, Future):
            raise TypeError('A native supervisor completion future is required.')
        with self._lock:
            if self._closing:
                raise StorageError('Materialization is closing.')
            self._pending += 1
        completion.add_done_callback(self._finished)

    def _finished(self, _):
        with self._lock:
            self._pending -= 1
        self._try_cleanup()

    def close(self):
        with self._lock:
            self._closing = True
        self._try_cleanup()

    def _try_cleanup(self):
        with self._lock:
            if not self._closing or self._pending or self._closed:
                return
            self._cleanup()
            self._closed = True


class PDFStorage(Protocol):
    def save(self, upload, document_id, max_bytes): ...
    def exists(self, document): ...
    def materialize(self, document) -> MaterializedPDF: ...
    def delete(self, document): ...


class LocalPDFStorage(DocumentStorage):
    """Opt-in facade; active DocumentStorage and dependency remain unchanged."""
    def exists(self, document):
        return self.reference(document).is_file()

    def materialize(self, document):
        return MaterializedPDF(self.processing_path(document))
