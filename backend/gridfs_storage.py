"""Disconnected GridFS foundation. No client creation, settings or route wiring."""
import tempfile
from pathlib import Path
from bson import ObjectId
from gridfs import GridFSBucket, NoFile
from pymongo import timeout
from document_storage import DocumentStorage, StorageError, CHUNK_SIZE
from storage_contract import MaterializedPDF, StorageUncertain


class GridFSPDFStorage:
    def __init__(self, database, *, owner_id, knowledge_base_id, temp_root,
                 max_bytes=5 * 1024 * 1024, timeout_seconds=5, bucket=None):
        if not isinstance(owner_id, ObjectId) or not isinstance(knowledge_base_id, ObjectId):
            raise ValueError('Server-owned scope required.')
        if type(max_bytes) is not int or not 1 <= max_bytes <= 5 * 1024 * 1024:
            raise ValueError('Invalid isolated storage size limit.')
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30:
            raise ValueError('Invalid storage deadline.')
        self.owner, self.base = owner_id, knowledge_base_id
        self.root = Path(temp_root).absolute()
        if not self.root.is_dir() or self.root.resolve() != self.root:
            raise StorageError('Private temporary storage is unavailable.')
        self.limit, self.deadline = max_bytes, timeout_seconds
        # Caller must supply a synchronous Database; never share the app AsyncDatabase.
        self.bucket = bucket if bucket is not None else GridFSBucket(database, bucket_name='pdf_blobs')

    def _scope(self, identifier):
        return {'document_id': identifier, 'owner_id': self.owner, 'knowledge_base_id': self.base}

    def reference(self, document):
        identifier = document.get('_id')
        if (not isinstance(identifier, ObjectId) or document.get('owner_id') != self.owner
                or document.get('knowledge_base_id') != self.base
                or document.get('storage_backend') != 'gridfs'
                or document.get('storage_file_id') != identifier):
            raise StorageError('Invalid storage reference.')
        return identifier

    def _open(self, document):
        identifier = self.reference(document)
        stream = self.bucket.open_download_stream(identifier)
        if stream.metadata != self._scope(identifier) or not 0 < stream.length <= self.limit:
            stream.close()
            raise StorageError('Invalid stored PDF metadata.')
        return stream

    def save(self, upload, document_id, max_bytes):
        if not isinstance(document_id, ObjectId):
            raise StorageError('Server-generated document identifier required.')
        if type(max_bytes) is not int or max_bytes < 1:
            raise StorageError('Invalid upload limit.')
        # Reuse exactly the local filename/MIME/signature/size validation, before
        # any remote mutation. TemporaryDirectory creates a private random directory.
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            local = DocumentStorage(directory)
            stored = local.save(upload, document_id, min(max_bytes, self.limit))
            try:
                with timeout(self.deadline):
                    stream = self.bucket.open_upload_stream_with_id(
                        document_id, str(document_id) + '.pdf', chunk_size_bytes=CHUNK_SIZE,
                        metadata=self._scope(document_id))
                    with local.reference({'_id': document_id, **stored}).open('rb') as source:
                        while data := source.read(CHUNK_SIZE):
                            stream.write(data)
                    stream.close()
            except Exception:
                # No abort/delete: writes or finalization may have reached MongoDB.
                raise StorageUncertain('PDF upload outcome requires reconciliation.') from None
        return {**stored, 'storage_backend': 'gridfs', 'storage_file_id': document_id}

    def exists(self, document):
        self.reference(document)
        try:
            with timeout(self.deadline):
                stream = self._open(document)
                stream.close()
            return True
        except NoFile:
            return False
        except Exception:
            raise StorageError('PDF storage lookup is unavailable.') from None

    def materialize(self, document):
        self.reference(document)
        temporary = tempfile.TemporaryDirectory(dir=self.root)
        path = Path(temporary.name) / 'source.pdf'
        try:
            with timeout(self.deadline):
                stream = self._open(document)
                try:
                    size = 0
                    with path.open('xb') as output:
                        while data := stream.read(CHUNK_SIZE):
                            size += len(data)
                            if size > self.limit:
                                raise StorageError('Stored PDF exceeds limit.')
                            output.write(data)
                    if size != stream.length:
                        raise StorageError('Stored PDF length mismatch.')
                    with path.open('rb') as source:
                        if source.read(5) != b'%PDF-':
                            raise StorageError('Stored PDF signature mismatch.')
                finally:
                    stream.close()
            return MaterializedPDF(path, temporary.cleanup)
        except Exception:
            temporary.cleanup()
            raise StorageError('Stored PDF cannot be materialized.') from None

    def delete(self, document):
        identifier = self.reference(document)
        try:
            with timeout(self.deadline):
                try:
                    stream = self._open(document)
                except NoFile:
                    # Do not call GridFS.delete on absent metadata: it may remove
                    # ambiguous orphan chunks. Repeated confirmed deletion is a no-op.
                    return False  # Absence is not proof orphan chunks were removed.
                stream.close()
        except Exception:
            raise StorageError('PDF deletion preflight is unavailable.') from None
        try:
            with timeout(self.deadline):
                self.bucket.delete(identifier)
            return True
        except Exception:
            raise StorageUncertain('PDF deletion outcome requires reconciliation.') from None
