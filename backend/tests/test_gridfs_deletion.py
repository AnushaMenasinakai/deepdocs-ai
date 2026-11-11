"""Offline deletion state machine with transactional rollback and real adapter."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from expensive_admission import Resource
from gridfs_deletion import GridFSDeletion
from gridfs_uploads import GridFSUploads
from gridfs_storage import GridFSPDFStorage
from storage_workers import StorageWorkers
from storage_contract import StorageUncertain
from document_storage import StorageError
from test_gridfs_uploads import UploadDatabase, UploadCollection
from test_gridfs_storage import Bucket, PDF
import io


class DeletionCollection(UploadCollection):
    def update_one(self, query, update, **kwargs):
        row = self.find_one(query)
        if row:
            target = self.db.rows[self.name][row['_id']]
            target.update(update.get('$set', {}))
            for key, value in update.get('$pull', {}).items():
                target[key] = [v for v in target.get(key, []) if v != value]
        return SimpleNamespace(matched_count=int(row is not None))

    def delete_one(self, query, **kwargs):
        if self.db.cleanup_failure: raise ConnectionFailure('private detail')
        row = self.find_one(query)
        if row: del self.db.rows[self.name][row['_id']]
        return SimpleNamespace(deleted_count=int(row is not None))

    def delete_many(self, query, **kwargs):
        rows = self.db.rows.setdefault(self.name, {})
        ids = [key for key, row in rows.items() if all(row.get(k) == v for k, v in query.items())]
        for key in ids: del rows[key]
        return SimpleNamespace(deleted_count=len(ids))


class DeletionDatabase(UploadDatabase):
    cleanup_failure = False
    def get_collection(self, name): return DeletionCollection(self, name)


class DeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = DeletionDatabase()
        self.owner, self.base = ObjectId(), ObjectId()
        self.db.rows['knowledge_bases'] = {self.base: {'_id': self.base, 'owner_id': self.owner}}
        self.bucket = Bucket()
        self.workers = StorageWorkers(Mock())
        self.admission = AdmissionService(ExpensiveAdmissionSettings())
        self.factory = lambda db, **kw: GridFSPDFStorage(db, bucket=self.bucket, **kw)
        uploader = GridFSUploads(self.workers, self.db, storage_factory=self.factory)
        upload = SimpleNamespace(filename='test.pdf', content_type='application/pdf', file=io.BytesIO(PDF))
        response = await uploader.upload(self.owner, self.base, upload, 100)
        upload._deepdocs_save_lifetime.close_when_finished()
        self.identifier = ObjectId(response.id)
        self.doc = dict(self.db.rows['documents'][self.identifier])
        self.token = ObjectId()
        self.db.rows['documents'][self.identifier]['_operation'] = self.token
        self.service = GridFSDeletion(self.workers, self.db, storage_factory=self.factory)
        self.db.rows['document_chunks'] = {ObjectId(): {'document_id': self.identifier,
            'owner_id': self.owner, 'knowledge_base_id': self.base}}

    async def asyncTearDown(self):
        await self.admission.aclose()
        await self.workers.aclose()

    async def delete(self):
        with self.admission.workflow(Resource.DOCUMENT) as workflow:
            return await self.service.delete(self.doc, self.owner, self.identifier, self.token, workflow)

    def quota(self):
        return self.db.rows['storage_operation_intents'][self.doc['storage_operation_id']]['quota_state']

    def deletion(self):
        return self.db.rows['storage_operation_intents'][self.token]

    async def test_success_exact_cleanup_and_quota(self):
        self.assertTrue(await self.delete())
        self.assertNotIn(self.identifier, self.db.rows['documents'])
        self.assertEqual(self.db.rows['document_chunks'], {})
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(self.quota(), 'released')
        self.assertEqual(self.deletion()['stage'], 'delete_confirmed')
        self.assertEqual(self.db.rows['knowledge_bases'][self.base]['_document_ids'], [])
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.bucket.deletes, 1) # no second remote delete

    async def test_missing_metadata_retains_quota_and_chunks(self):
        self.bucket.files.clear()
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.bucket.deletes, 0)
        self.assertEqual(self.quota(), 'committed')
        self.assertTrue(self.db.rows['document_chunks'])
        self.assertEqual(self.deletion()['reconciliation_status'], 'required')

    async def test_partial_delete_and_lost_ack(self):
        def lost(identifier):
            self.bucket.deletes += 1
            self.bucket.files.pop(identifier)
            raise TimeoutError('private detail')
        self.bucket.delete = lost
        with self.assertRaises(StorageUncertain) as error: await self.delete()
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(self.quota(), 'committed')
        self.assertIn(self.identifier, self.db.rows['documents'])
        self.assertEqual(self.deletion()['stage'], 'vectors_deleted')

    async def test_metadata_failure_rolls_back_cleanup_and_quota(self):
        self.db.cleanup_failure = True
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.quota(), 'committed')
        self.assertTrue(self.db.rows['document_chunks'])
        self.assertIn(self.identifier, self.db.rows['documents'])
        self.assertEqual(self.deletion()['stage'], 'file_deleted')

    async def test_quota_release_failure_rolls_back_metadata(self):
        with patch.object(self.service.intents, '_write_ledger', side_effect=ConnectionFailure('private')):
            with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.quota(), 'committed')
        self.assertIn(self.identifier, self.db.rows['documents'])
        self.assertTrue(self.db.rows['document_chunks'])

    async def test_owner_kb_file_and_claim_mismatch_before_deletion(self):
        for field in ('owner_id', 'knowledge_base_id', 'storage_file_id'):
            original = self.doc[field]
            self.doc[field] = ObjectId()
            with self.assertRaises((StorageError, StorageUncertain)): await self.delete()
            self.doc[field] = original
        self.db.rows['documents'][self.identifier]['_operation'] = ObjectId()
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.bucket.deletes, 0)
        self.assertEqual(self.quota(), 'committed')

    async def test_vectors_confirmed_before_file_delete(self):
        self.doc['vector_index'] = {'collection_name': 'synthetic', 'status': 'indexed'}
        self.db.rows['documents'][self.identifier]['vector_index'] = self.doc['vector_index']
        with patch('gridfs_deletion.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
            original = self.bucket.delete
            def remove(identifier):
                self.assertEqual(vectors.await_count, 1)
                self.assertEqual(self.deletion()['stage'], 'vectors_deleted')
                original(identifier)
            self.bucket.delete = remove
            await self.delete()

    async def test_cancel_after_dispatch_holds_admission_and_quota(self):
        entered, release = threading.Event(), threading.Event()
        original = self.bucket.delete
        def blocked(identifier):
            entered.set()
            if not release.wait(5): raise RuntimeError('test timeout')
            original(identifier)
        self.bucket.delete = blocked
        task = asyncio.create_task(self.delete())
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
        release.set()
        await self.admission.aclose()
        self.assertEqual(self.quota(), 'committed')
        self.assertEqual(self.deletion()['error_category'], 'cancelled')
        self.assertIn(self.identifier, self.db.rows['documents'])

    async def test_cancel_before_call_has_no_remote_mutation(self):
        task = asyncio.create_task(self.delete())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.bucket.deletes, 0)
        self.assertNotIn(self.token, self.db.rows['storage_operation_intents'])

    async def test_final_commit_ack_loss_does_not_retry(self):
        finalize = self.service.intents.finalize_deletion
        def lost(*args):
            self.db.failure = 'after_commit'
            finalize(*args)
        self.service.intents.finalize_deletion = lost
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.deletion()['stage'], 'delete_confirmed')
        self.assertEqual(self.quota(), 'released')
        self.assertNotIn(self.identifier, self.db.rows['documents'])
        self.assertEqual(self.bucket.deletes, 1)

    async def test_vector_failure_prevents_storage_and_quota_release(self):
        self.doc['vector_index'] = {'collection_name': 'synthetic'}
        with patch('gridfs_deletion.cleanup_document_vectors', side_effect=TimeoutError):
            with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.bucket.deletes, 0)
        self.assertEqual(self.quota(), 'committed')

    async def test_absence_outcome_is_not_confirmation(self):
        from storage_contract import DeletionOutcome
        original = self.factory
        def factory(*args, **kwargs):
            storage = original(*args, **kwargs)
            storage.delete_confirmed = lambda document: DeletionOutcome.ABSENT
            return storage
        self.service.factory = factory
        with self.assertRaises(StorageUncertain): await self.delete()
        self.assertEqual(self.quota(), 'committed')
        self.assertIn(self.identifier, self.db.rows['documents'])

    async def test_cancel_during_prepare_does_not_dispatch_delete(self):
        entered, release = threading.Event(), threading.Event()
        create = self.service.intents.create
        def blocked(*args):
            entered.set()
            if not release.wait(5): raise RuntimeError('test timeout')
            return create(*args)
        self.service.intents.create = blocked
        task = asyncio.create_task(self.delete())
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        release.set()
        await self.admission.aclose()
        self.assertEqual(self.bucket.deletes, 0)
        self.assertEqual(self.deletion()['stage'], 'prepared')
        self.assertEqual(self.deletion()['reconciliation_status'], 'required')
        self.assertEqual(self.quota(), 'committed')


class DeletionHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import test_documents as fixtures
        from main import app
        await fixtures.DocumentTests.asyncSetUp(self)
        self.old = getattr(app.state, 'gridfs_deletion', None)
        self.syncdb = DeletionDatabase()
        self.syncdb.rows.update(knowledge_bases=self.bases.documents, documents=self.docs.documents,
                                document_chunks=self.chunks.documents)
        self.storage_workers = StorageWorkers(Mock())
        self.bucket = Bucket()
        factory = lambda db, **kw: GridFSPDFStorage(db, bucket=self.bucket, **kw)
        uploader = GridFSUploads(self.storage_workers, self.syncdb, storage_factory=factory)
        upload = SimpleNamespace(filename='test.pdf', content_type='application/pdf', file=io.BytesIO(PDF))
        response = await uploader.upload(self.owner, self.base_id, upload, 100)
        upload._deepdocs_save_lifetime.close_when_finished()
        self.identifier = response.id
        app.state.gridfs_deletion = GridFSDeletion(self.storage_workers, self.syncdb, storage_factory=factory)

    async def asyncTearDown(self):
        import test_documents as fixtures
        from main import app
        app.state.gridfs_deletion = self.old
        await fixtures.DocumentTests.asyncTearDown(self)
        await self.storage_workers.aclose()

    async def test_delete_204_then_404(self):
        from test_documents import request
        path = '/api/documents/' + self.identifier
        self.assertEqual((await request('DELETE', path))[0], 204)
        self.assertEqual((await request('DELETE', path))[0], 404)
        self.assertEqual(self.bucket.deletes, 1)

    async def test_owner_busy_and_admission_precedence(self):
        from test_documents import request
        path = '/api/documents/' + self.identifier
        self.current_owner = self.other
        self.assertEqual((await request('DELETE', path))[0], 404)
        self.current_owner = self.owner
        row = self.docs.documents[ObjectId(self.identifier)]
        row['_operation'] = ObjectId() # processing owns the document
        self.assertEqual((await request('DELETE', path))[0], 409)
        del row['_operation']
        with self.admission.workflow(Resource.DOCUMENT):
            self.assertEqual((await request('DELETE', path))[0], 503)
        self.assertEqual(self.bucket.deletes, 0)

    async def test_missing_provider_never_uses_local_storage(self):
        from test_documents import request
        from main import app
        service = app.state.gridfs_deletion
        app.state.gridfs_deletion = None
        try:
            with patch.object(self.storage, 'delete', side_effect=AssertionError('fallback')) as local:
                self.assertEqual((await request('DELETE', '/api/documents/' + self.identifier))[0], 503)
                local.assert_not_called()
        finally:
            app.state.gridfs_deletion = service
        self.assertEqual(self.bucket.deletes, 0)

    async def test_bulk_routes_through_same_service(self):
        from test_knowledge_bases import request
        status, result, _ = await request('POST', self.path + '/bulk-delete',
                                         {'document_ids': [self.identifier]})
        self.assertEqual(status, 200)
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(self.bucket.deletes, 1)
