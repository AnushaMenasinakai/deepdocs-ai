"""Internal synchronous storage boundary; implementations run in owned workers.

Not wired into routes. A materialization must be retained by the supervisor's
native completion future, never by the cancellable request task.
"""
from concurrent.futures import Future
from pathlib import Path
from threading import Lock
from typing import Protocol
from enum import Enum
from bson import ObjectId
from document_storage import DocumentStorage, StorageError, StorageCleanupError


class StorageUncertain(StorageCleanupError):
    """Remote mutation may have committed. Do not compensate or release quota."""


class DeletionOutcome(Enum):
    DELETED = 'confirmed_deleted'
    ABSENT = 'confirmed_absent'
    UNCONFIRMED = 'unconfirmed'

    def __bool__(self):
        raise TypeError('Compare deletion outcomes explicitly; never use truthiness.')


class DeletionUncertain(StorageUncertain):
    outcome = DeletionOutcome.UNCONFIRMED


def validate_scope(document, owner_id, knowledge_base_id):
    if (not isinstance(document, dict)
            or any(not isinstance(document.get(key), ObjectId)
                   for key in ('_id', 'owner_id', 'knowledge_base_id'))
            or document['owner_id'] != owner_id
            or document['knowledge_base_id'] != knowledge_base_id):
        raise StorageError('Invalid authorized storage identity.')
    return document['_id']


def compensation_document(document_id, owner_id, knowledge_base_id, stored):
    """Internal only: caller supplies already-authorized IDs and a confirmed receipt.

    This constructs identity, not permission to compensate an uncertain operation.
    """
    document = dict(stored)
    for key, value in (('_id', document_id), ('owner_id', owner_id),
                       ('knowledge_base_id', knowledge_base_id)):
        if key in document and document[key] != value:
            raise StorageError('Conflicting compensation identity.')
        document[key] = value
    validate_scope(document, owner_id, knowledge_base_id)
    return document


class MaterializedPDF:
    def __init__(self, path, cleanup=lambda: None):
        self.path = Path(path)
        self._cleanup = cleanup
        self._lock = Lock()
        self._pending = 0
        self._closing = False
        self._closed = False

    @property
    def cleanup_complete(self):
        with self._lock:
            return self._closed

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
    def reference(self, document): ...
    def save(self, upload, document_id, max_bytes): ...
    def exists(self, document): ...
    def materialize(self, document) -> MaterializedPDF: ...
    def delete_confirmed(self, document) -> DeletionOutcome: ...
    def compensate(self, document) -> DeletionOutcome: ...


class LocalPDFStorage(DocumentStorage):
    """Opt-in facade; active DocumentStorage and dependency remain unchanged."""
    def __init__(self, root, *, owner_id, knowledge_base_id):
        if not isinstance(owner_id, ObjectId) or not isinstance(knowledge_base_id, ObjectId):
            raise StorageError('Server-owned scope required.')
        super().__init__(root)
        self.owner, self.base = owner_id, knowledge_base_id

    def save(self, upload, document_id, max_bytes):
        if not isinstance(document_id, ObjectId):
            raise StorageError('Server-generated document identifier required.')
        return super().save(upload, document_id, max_bytes)

    def reference(self, document):
        validate_scope(document, self.owner, self.base)
        if document.get('storage_backend', 'local') != 'local':
            raise StorageError('Invalid local storage backend.')
        return super().reference(document)

    def delete_confirmed(self, document):
        path = self.reference(document)
        try:
            path.unlink()
        except FileNotFoundError:
            return DeletionOutcome.ABSENT
        except OSError:
            raise DeletionUncertain('Local PDF deletion could not be confirmed.') from None
        return DeletionOutcome.DELETED

    def compensate(self, document):
        return self.delete_confirmed(document)

    def exists(self, document):
        return self.reference(document).is_file()

    def materialize(self, document):
        return MaterializedPDF(self.processing_path(document))
