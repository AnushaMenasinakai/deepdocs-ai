"""Owned, no-retry GridFS deletion. Quota is retained on uncertain outcomes."""
import asyncio
import tempfile
from concurrent.futures import Future
from bson import ObjectId
from pymongo import timeout
from document_storage import StorageError
from storage_contract import validate_scope, DeletionOutcome, StorageUncertain
from storage_intents import StorageIntents, IntentIdentity, IntentConflict
from gridfs_storage import GridFSPDFStorage
from vector_store import cleanup_document_vectors


class _OwnedDeletion:
    def __init__(self, workflow):
        self._terminal = Future()
        self._terminal.set_running_or_notify_cancel()
        self.abandoned = False
        workflow.own_cleanup(self)

    def close(self):
        self.abandoned = True

    def checkpoint(self):
        if self.abandoned:
            raise StorageUncertain('Deletion requires reconciliation.')


class GridFSDeletion:
    def __init__(self, workers, database, *, storage_factory=GridFSPDFStorage):
        self.workers, self.db = workers, database
        self.intents = StorageIntents(database)
        self.factory = storage_factory
        self.worker_id = 'delete_' + str(ObjectId())

    async def delete(self, document, owner, identifier, token, workflow):
        if workflow is None:
            raise StorageError('Owned deletion workflow required.')
        if validate_scope(document, owner, document.get('knowledge_base_id')) != identifier:
            raise StorageError('Invalid storage identity.')
        if (document.get('storage_backend') != 'gridfs'
                or document.get('storage_file_id') != identifier
                or not isinstance(document.get('storage_operation_id'), ObjectId)):
            raise StorageError('Invalid GridFS original identity.')
        identity = IntentIdentity(token, identifier, owner, document['knowledge_base_id'], 'delete')
        work = _OwnedDeletion(workflow)
        task = asyncio.create_task(self._run(dict(document), identity, work))
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            work.close()
            raise

    async def _run(self, document, identity, work):
        stage = None
        scope = {'_id': identity.document_id, 'owner_id': identity.owner_id,
                 'knowledge_base_id': identity.knowledge_base_id, '_operation': identity.operation_id}

        def check_claim():
            with timeout(5):
                current = self.db.get_collection('documents').find_one(scope)
            keys = ('storage_backend', 'storage_file_id', 'storage_operation_id', 'storage_checksum', 'file_size')
            if current is None or any(current.get(k) != document.get(k) for k in keys):
                raise IntentConflict('Deletion claim identity conflict.')

        async def advance(target):
            nonlocal stage
            await self.workers.run(lambda: self.intents.transition(identity, stage, target, self.worker_id))
            stage = target
            work.checkpoint()

        def prepare():
            check_claim()
            # Only a fully committed, certain upload can be deleted here.
            with self.intents.transaction() as session:
                source = self.intents.intents.find_one({'_id': document['storage_operation_id']}, session=session)
                expected = {'document_id': identity.document_id, 'owner_id': identity.owner_id,
                    'knowledge_base_id': identity.knowledge_base_id, 'file_id': identity.document_id,
                    'backend': 'gridfs', 'operation_type': 'upload', 'stage': 'published',
                    'quota_state': 'committed', 'reconciliation_status': 'none',
                    'expected_size': document.get('file_size'), 'checksum': document.get('storage_checksum')}
                if source is None or any(source.get(k) != v for k, v in expected.items()):
                    raise IntentConflict('Upload reconciliation required before deletion.')
            self.intents.create(identity, document['file_size'], document['storage_checksum'],
                                self.worker_id, document['storage_operation_id'])

        def remove_file():
            check_claim()
            with tempfile.TemporaryDirectory(prefix='deepdocs-delete-') as root:
                storage = self.factory(self.db, owner_id=identity.owner_id,
                    knowledge_base_id=identity.knowledge_base_id, temp_root=root)
                storage.reference(document)
                outcome = storage.delete_confirmed(document)
                if outcome is not DeletionOutcome.DELETED:
                    raise StorageUncertain('GridFS deletion is unconfirmed.')

        try:
            work.checkpoint()
            await self.workers.run(prepare)
            stage = 'prepared'
            work.checkpoint()
            await advance('delete_dispatched')
            if document.get('vector_index', {}).get('collection_name'):
                def invalidate():
                    check_claim()
                    with timeout(5):
                        result = self.db.get_collection('documents').update_one(scope,
                            {'$set': {'vector_index': {**document['vector_index'], 'status': 'stale'}}})
                    if result.matched_count != 1:
                        raise StorageUncertain('Vector invalidation is unconfirmed.')
                await self.workers.run(invalidate)
                work.checkpoint()
                await cleanup_document_vectors(document)
                work.checkpoint()
            await advance('vectors_deleted')
            await self.workers.run(remove_file)
            work.checkpoint()
            await advance('file_deleted')
            await self.workers.run(lambda: self.intents.finalize_deletion(identity, self.worker_id))
            stage = 'delete_confirmed'
            return True
        except Exception:
            if stage is not None:
                try:
                    await self.workers.run(lambda: self.intents.mark_uncertain(identity, stage,
                        'cancelled' if work.abandoned else 'storage_unavailable'))
                except Exception:
                    pass
            raise StorageUncertain('Deletion outcome requires reconciliation.') from None
        finally:
            # This owned task is never cancelled by the request; each native
            # storage call has actually completed before workflow ownership ends.
            if not asyncio.current_task().cancelling() and all(
                    future is not None for future in self.workers.pending.values()):
                work._terminal.set_result(None)
