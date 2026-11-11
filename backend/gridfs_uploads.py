"""Opt-in upload orchestration. All synchronous IO stays in one owned worker.

No retry or remote compensation: nonterminal intents are reconciliation work.
This module does not select storage for existing documents.
"""
import asyncio
import hashlib
import tempfile
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from bson import ObjectId, BSON
from pymongo import timeout
from pymongo.write_concern import WriteConcern

from config import load_gridfs_upload_enabled, load_settings
from document_storage import DocumentStorage, UploadRejected
from documents import public_document
from gridfs_storage import GridFSPDFStorage
from storage_contract import StorageUncertain
from storage_intents import StorageIntents, IntentIdentity, QuotaPolicy, QuotaExceeded
from storage_workers import optional_storage_lifespan, StorageRejected
from upload_safety import SaveLifetime, require_acknowledged, check_cancellation


def checksum(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


class GridFSUploads:
    def __init__(self, workers, database, *, policy=QuotaPolicy(), storage_factory=GridFSPDFStorage):
        self.workers, self.db = workers, database
        self.intents = StorageIntents(database, policy)
        self.storage_factory = storage_factory
        self.worker_id = 'upload_' + str(ObjectId())

    async def upload(self, owner, base, upload, max_bytes):
        check_cancellation()
        identity = IntentIdentity.new(ObjectId(), owner, base)
        cancelled = threading.Event()
        lifetime = getattr(upload, '_deepdocs_save_lifetime', None) or SaveLifetime(upload)
        upload._deepdocs_save_lifetime = lifetime
        try:
            return await self.workers.run(lambda: lifetime.run(
                self._execute, identity, upload, max_bytes, cancelled))
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except StorageRejected:
            raise UploadRejected(503, 'Storage upload capacity is unavailable.') from None

    def _execute(self, identity, upload, max_bytes, cancelled):
        stage = None
        scope = {'_id': identity.knowledge_base_id, 'owner_id': identity.owner_id}
        bases, docs = (self.db.get_collection(name) for name in ('knowledge_bases', 'documents'))
        require_acknowledged(bases)
        require_acknowledged(docs)

        def checkpoint():
            if cancelled.is_set():
                raise StorageUncertain('Cancelled upload requires reconciliation.')

        def advance(target):
            nonlocal stage
            self.intents.transition(identity, stage, target, self.worker_id)
            stage = target

        try:
            # Private local validation precedes remote mutations. The same local
            # validator supplies filename/MIME/signature/size rules as before.
            with tempfile.TemporaryDirectory(prefix='deepdocs-upload-') as directory:
                checkpoint()
                local = DocumentStorage(directory)
                stored = local.save(upload, identity.document_id, min(max_bytes, self.intents.policy.file_bytes))
                digest = checksum(local.reference({'_id': identity.document_id, **stored}))
                checkpoint()
                with timeout(5):
                    base = bases.find_one_and_update(scope, {
                        '$addToSet': {'_document_ids': identity.document_id},
                        '$inc': {'_document_revision': 1}})
                if base is None:
                    return None
                if any(base.get(k) != v for k, v in scope.items()):
                    raise StorageUncertain('Upload authorization requires review.')
                self.intents.create(identity, stored['file_size'], digest, self.worker_id)
                stage = 'prepared'
                try:
                    self.intents.reserve(identity)
                except QuotaExceeded:
                    # Confirmed transaction rejection, no GridFS dispatch and no
                    # quota entry. Retain evidence of the rejected intent.
                    advance('failed_before_dispatch')
                    with timeout(5):
                        result = bases.update_one(scope, {'$pull': {'_document_ids': identity.document_id},
                                                         '$inc': {'_document_revision': 1}})
                    if result.matched_count != 1:
                        raise StorageUncertain('Upload reservation cleanup requires review.')
                    raise UploadRejected(413, 'PDF storage quota is exhausted.') from None
                checkpoint()
                storage = self.storage_factory(self.db, owner_id=identity.owner_id,
                    knowledge_base_id=identity.knowledge_base_id, temp_root=directory,
                    max_bytes=self.intents.policy.file_bytes)
                # Validate the adapter scope before any remote file mutation.
                reference = {'_id': identity.document_id, 'owner_id': identity.owner_id,
                    'knowledge_base_id': identity.knowledge_base_id, 'storage_backend': 'gridfs',
                    'storage_file_id': identity.document_id}
                storage.reference(reference)
                advance('write_dispatched')
                checkpoint()
                remote = storage.save(upload, identity.document_id, self.intents.policy.file_bytes)
                if (any(remote.get(k) != v for k, v in stored.items())
                        or remote.get('storage_backend') != 'gridfs'
                        or remote.get('storage_file_id') != identity.document_id):
                    raise StorageUncertain('Stored PDF metadata requires reconciliation.')
                document = {**remote, **reference}
                storage.reference(document)
                # Read back finalized bytes, scoped metadata, length and checksum.
                # No existence-only or missing-metadata inference is sufficient.
                lease = storage.materialize(document)
                try:
                    if lease.path.stat().st_size != stored['file_size'] or checksum(lease.path) != digest:
                        raise StorageUncertain('Stored PDF integrity requires reconciliation.')
                finally:
                    lease.close()
                if not lease.cleanup_complete:
                    raise StorageUncertain('Temporary upload cleanup requires review.')
                checkpoint()
                advance('file_confirmed')
                now = datetime.now(timezone.utc)
                document.update(status='uploaded', created_at=now, updated_at=now,
                                storage_operation_id=identity.operation_id, storage_checksum=digest)
                BSON.encode(document)
                checkpoint()
                with timeout(5):
                    result = docs.insert_one(document)
                if not result.acknowledged or result.inserted_id != identity.document_id:
                    raise StorageUncertain('Document publication requires reconciliation.')
                checkpoint()
                advance('published')
                self.intents.quota_transition(identity, 'commit')
                return public_document(document)
        except Exception as error:
            if isinstance(error, UploadRejected) and stage in (None, 'failed_before_dispatch'):
                raise
            # Best effort only; a failed diagnostic write cannot make the prior
            # mutation certain. Nonterminal or published/reserved records persist.
            if stage is not None:
                try:
                    self.intents.mark_uncertain(identity, stage,
                        'cancelled' if cancelled.is_set() else 'storage_unavailable')
                except Exception:
                    pass
            raise StorageUncertain('Upload outcome requires reconciliation.') from None


@asynccontextmanager
async def gridfs_upload_lifespan(app, *, cpu_workers=None):
    app.state.gridfs_uploads = None
    app.state.gridfs_processing = None
    app.state.gridfs_deletion = None
    if not load_gridfs_upload_enabled():
        yield
        return
    settings = load_settings()
    async with optional_storage_lifespan(enabled=True, uri=settings.mongodb_uri) as workers:
        database = workers.client.get_database(settings.mongodb_db_name,
                                               write_concern=WriteConcern('majority'))
        app.state.gridfs_uploads = GridFSUploads(workers, database)
        from gridfs_deletion import GridFSDeletion
        app.state.gridfs_deletion = GridFSDeletion(workers, database)
        if cpu_workers is not None:
            from gridfs_processing import GridFSProcessing
            app.state.gridfs_processing = GridFSProcessing(workers, database, cpu_workers)
        try:
            yield
        finally:
            if app.state.gridfs_processing is not None:
                await app.state.gridfs_processing.leases.aclose()
            app.state.gridfs_processing = None
            app.state.gridfs_deletion = None
            app.state.gridfs_uploads = None
