"""Offline real upload orchestration, transaction double and in-memory GridFS."""
import asyncio
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from gridfs_uploads import GridFSUploads, gridfs_upload_lifespan
from gridfs_storage import GridFSPDFStorage
from storage_workers import StorageWorkers
from storage_contract import StorageUncertain
from storage_intents import QuotaPolicy
from document_storage import UploadRejected, DocumentStorage, StorageError
from config import load_gridfs_upload_enabled, ConfigurationError
from test_storage_intents import Database, Collection
from test_gridfs_storage import Bucket, PDF


class UploadCollection(Collection):
    def find_one_and_update(self, query, update, **kwargs):
        with self.db.lock:
            old = self.find_one(query)
            if old:
                row = self.db.rows[self.name][old['_id']]
                row.setdefault('_document_ids', []).append(update['$addToSet']['_document_ids'])
            return old

    def update_one(self, query, update, **kwargs):
        with self.db.lock:
            row = self.find_one(query)
            if row:
                stored = self.db.rows[self.name][row['_id']]
                stored['_document_ids'].remove(update['$pull']['_document_ids'])
            return SimpleNamespace(matched_count=int(row is not None))

    def insert_one(self, value, **kwargs):
        super().insert_one(value, **kwargs)
        if self.name == 'documents' and self.db.publication_failure:
            raise ConnectionFailure('private provider message')
        return SimpleNamespace(acknowledged=True, inserted_id=value['_id'])


class UploadDatabase(Database):
    def __init__(self):
        super().__init__()
        self.publication_failure = False
    def get_collection(self, name): return UploadCollection(self, name)


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = UploadDatabase()
        self.owner, self.base = ObjectId(), ObjectId()
        self.db.rows['knowledge_bases'] = {self.base: {'_id': self.base, 'owner_id': self.owner}}
        self.bucket = Bucket()
        self.workers = StorageWorkers(Mock())
        self.service = GridFSUploads(self.workers, self.db, storage_factory=self.factory)
        self.uploads = []

    def factory(self, database, **kwargs):
        return GridFSPDFStorage(database, bucket=self.bucket, **kwargs)

    def upload(self, data=PDF):
        value = SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf', file=io.BytesIO(data))
        self.uploads.append(value)
        return value

    async def call(self, **kwargs):
        return await self.service.upload(self.owner, self.base, self.upload(**kwargs), 10 * 1024**2)

    async def asyncTearDown(self):
        await self.workers.aclose()
        for upload in self.uploads:
            lifetime = getattr(upload, '_deepdocs_save_lifetime', None)
            if lifetime: lifetime.close_when_finished()
            else: upload.file.close()

    def intent(self):
        return next(iter(self.db.rows['storage_operation_intents'].values()))

    async def test_success_integrity_public_contract_and_quota(self):
        result = await self.call()
        row = self.intent()
        self.assertEqual((row['stage'], row['quota_state']), ('published', 'committed'))
        doc = self.db.rows['documents'][ObjectId(result.id)]
        self.assertEqual(doc['storage_checksum'], row['checksum'])
        self.assertEqual(doc['storage_file_id'], doc['_id'])
        self.assertEqual(doc['owner_id'], self.owner)
        self.assertEqual(self.bucket.files[doc['_id']][0], PDF)
        self.assertNotIn('storage_file_id', result.model_dump())
        with self.assertRaises(StorageError): DocumentStorage().reference(doc)

    async def test_invalid_before_mutation(self):
        for data in (b'bad', b'%PDF-' + b'x' * (5 * 1024**2)):
            with self.assertRaises(UploadRejected): await self.call(data=data)
        self.assertNotIn('storage_operation_intents', self.db.rows)
        self.assertEqual(self.bucket.files, {})
        self.assertNotIn('_document_ids', self.db.rows['knowledge_bases'][self.base])

    async def test_owner_and_kb_mismatch(self):
        for owner, base in ((ObjectId(), self.base), (self.owner, ObjectId())):
            self.assertIsNone(await self.service.upload(owner, base, self.upload(), 100))
        self.assertEqual(self.bucket.files, {})
        with self.assertRaises(ValueError):
            await self.service.upload('untrusted', self.base, self.upload(), 100)

    async def test_quota_exhaustion_no_file_write_and_concurrent_admission(self):
        self.service.intents.policy = QuotaPolicy(100, 100, 100, 1, 1)
        results = await asyncio.gather(self.call(), self.call(), return_exceptions=True)
        self.assertEqual(sum(isinstance(r, UploadRejected) for r in results), 1)
        self.assertEqual(len(self.bucket.files), 1)
        self.assertEqual(len(self.db.rows['knowledge_bases'][self.base]['_document_ids']), 1)
        entries = self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries']
        self.assertEqual(len(entries), 1)

    async def test_timeout_after_dispatch_retains_file_and_capacity(self):
        self.bucket.failure = 'close'
        with self.assertRaises(StorageUncertain): await self.call()
        row = self.intent()
        self.assertEqual((row['stage'], row['quota_state'], row['reconciliation_status']),
                         ('write_dispatched', 'reserved', 'required'))
        self.assertEqual(len(self.bucket.files), 1)
        self.assertEqual(self.bucket.deletes, 0)
        self.assertNotIn('documents', self.db.rows)

    async def test_metadata_ack_loss_never_deletes_or_retries(self):
        self.db.publication_failure = True
        with self.assertRaises(StorageUncertain) as error: await self.call()
        self.assertNotIn('private', str(error.exception))
        self.assertEqual(len(self.db.rows['documents']), 1)
        self.assertEqual(self.intent()['stage'], 'file_confirmed')
        self.assertEqual(self.intent()['quota_state'], 'reserved')
        self.assertEqual(self.bucket.deletes, 0)

    async def test_cancellation_keeps_stream_owned_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = self.factory
        def factory(*args, **kwargs):
            storage = original(*args, **kwargs)
            save = storage.save
            def blocked(*args):
                entered.set()
                if not release.wait(5): raise RuntimeError('test timeout')
                return save(*args)
            storage.save = blocked
            return storage
        self.service.storage_factory = factory
        upload = self.upload()
        task = asyncio.create_task(self.service.upload(self.owner, self.base, upload, 100))
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        upload._deepdocs_save_lifetime.close_when_finished()
        self.assertFalse(upload.file.closed)
        release.set()
        await self.workers.aclose()
        self.assertTrue(upload.file.closed)
        self.assertEqual(self.intent()['quota_state'], 'reserved')
        self.assertEqual(self.intent()['error_category'], 'cancelled')
        self.assertNotIn('documents', self.db.rows)

    async def test_corrupt_readback_refuses_publication(self):
        original = self.bucket.open_download_stream
        def corrupt(identifier):
            stream = original(identifier)
            stream.seek(5); stream.write(b'X'); stream.seek(0)
            return stream
        self.bucket.open_download_stream = corrupt
        with self.assertRaises(StorageUncertain): await self.call()
        self.assertNotIn('documents', self.db.rows)
        self.assertEqual(self.intent()['reconciliation_status'], 'required')

    async def test_local_lifespan_creates_no_client(self):
        app = SimpleNamespace(state=SimpleNamespace())
        with patch('gridfs_uploads.load_gridfs_upload_enabled', return_value=False), \
             patch('gridfs_uploads.optional_storage_lifespan') as client:
            async with gridfs_upload_lifespan(app):
                self.assertIsNone(app.state.gridfs_uploads)
            client.assert_not_called()

    async def test_enabled_lifespan_owns_one_client_and_clears_service(self):
        from contextlib import asynccontextmanager
        app = SimpleNamespace(state=SimpleNamespace())
        client = Mock()
        client.get_database.return_value = self.db
        events = []
        @asynccontextmanager
        async def optional(**kwargs):
            self.assertTrue(kwargs['enabled'])
            events.append('open')
            yield SimpleNamespace(client=client)
            events.append('close')
        with patch('gridfs_uploads.load_gridfs_upload_enabled', return_value=True), \
             patch('gridfs_uploads.load_settings', return_value=SimpleNamespace(
                 mongodb_uri='synthetic-test-setting', mongodb_db_name='synthetic')), \
             patch('gridfs_uploads.optional_storage_lifespan', side_effect=optional) as factory:
            async with gridfs_upload_lifespan(app):
                self.assertIsInstance(app.state.gridfs_uploads, GridFSUploads)
                self.assertEqual(events, ['open'])
                self.assertNotIn('storage_operation_intents', self.db.rows)
            self.assertIsNone(app.state.gridfs_uploads)
            factory.assert_called_once()
            client.get_database.assert_called_once()
            self.assertEqual(events, ['open', 'close'])

    def test_flag_default_false_and_invalid(self):
        with patch('config.dotenv_values', return_value={}), patch.dict('os.environ', {}, clear=True):
            self.assertFalse(load_gridfs_upload_enabled())
            with patch.dict('os.environ', {'GRIDFS_UPLOAD_ENABLED': 'yes'}):
                with self.assertRaises(ConfigurationError): load_gridfs_upload_enabled()

    async def test_wrong_adapter_scope_rejected_before_file_write(self):
        def forged(database, **kwargs):
            kwargs['owner_id'] = ObjectId()
            return self.factory(database, **kwargs)
        self.service.storage_factory = forged
        with self.assertRaises(StorageUncertain): await self.call()
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(self.intent()['stage'], 'prepared')
        self.assertEqual(self.intent()['quota_state'], 'reserved')

    async def test_lost_quota_ack_never_dispatches_file(self):
        reserve = self.service.intents.reserve
        def uncertain(identity):
            reserve(identity)
            raise StorageUncertain('lost ack')
        self.service.intents.reserve = uncertain
        with self.assertRaises(StorageUncertain): await self.call()
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(self.intent()['quota_state'], 'reserved')

    async def test_worker_rejection_precedes_intent_and_kb_mutation(self):
        self.workers.begin_shutdown()
        with self.assertRaises(UploadRejected) as error: await self.call()
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn('storage_operation_intents', self.db.rows)
        self.assertNotIn('_document_ids', self.db.rows['knowledge_bases'][self.base])

    async def test_quota_commit_failure_retains_published_document(self):
        def fail(*args): raise StorageUncertain('lost quota commit')
        self.service.intents.quota_transition = fail
        with self.assertRaises(StorageUncertain): await self.call()
        self.assertEqual(self.intent()['stage'], 'published')
        self.assertEqual(self.intent()['quota_state'], 'reserved')
        self.assertEqual(self.intent()['reconciliation_status'], 'required')
        self.assertEqual(len(self.db.rows['documents']), 1)
        self.assertEqual(self.bucket.deletes, 0)


class GridFSHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_documents import DocumentTests
        from main import app
        await DocumentTests.asyncSetUp(self)
        self.previous = getattr(app.state, 'gridfs_uploads', None)
        self.syncdb = UploadDatabase()
        self.syncdb.rows['knowledge_bases'] = {self.base_id: {'_id': self.base_id, 'owner_id': self.owner}}
        self.bucket = Bucket()
        self.storage_workers = StorageWorkers(Mock())
        self.gridfs = GridFSUploads(self.storage_workers, self.syncdb,
            storage_factory=lambda db, **kw: GridFSPDFStorage(db, bucket=self.bucket, **kw))
        app.state.gridfs_uploads = self.gridfs

    async def asyncTearDown(self):
        from test_documents import DocumentTests
        from main import app
        app.state.gridfs_uploads = self.previous
        await self.storage_workers.aclose()
        await DocumentTests.asyncTearDown(self)

    async def test_http_success_preserves_response_and_uses_only_gridfs(self):
        from test_documents import request, PDF as HTTP_PDF, PUBLIC
        status, body, _ = await request('POST', self.path, content=HTTP_PDF)
        self.assertEqual(status, 201)
        self.assertEqual(set(body), PUBLIC)
        self.assertEqual(len(self.bucket.files), 1)
        self.assertFalse(self.root.exists()) # local storage was never initialized
        self.assertEqual(self.docs.documents, {}) # async local uploader not used

    async def test_existing_local_document_stays_local_with_flag_enabled(self):
        from test_documents import request, PDF as HTTP_PDF
        from main import app
        app.state.gridfs_uploads = None
        try:
            status, body, _ = await request('POST', self.path, content=HTTP_PDF)
        finally:
            app.state.gridfs_uploads = self.gridfs
        self.assertEqual(status, 201)
        path = self.root / (body['id'] + '.pdf')
        self.assertTrue(path.exists())
        self.assertEqual((await request('DELETE', '/api/documents/' + body['id']))[0], 204)
        self.assertFalse(path.exists())
        self.assertEqual(self.bucket.files, {})
        self.assertNotIn('storage_operation_intents', self.syncdb.rows)

    async def test_provider_error_has_existing_sanitized_http_contract(self):
        from test_documents import request, PDF as HTTP_PDF
        self.bucket.failure = 'close'
        status, body, _ = await request('POST', self.path, content=HTTP_PDF)
        self.assertEqual(status, 503)
        self.assertEqual(body, {'detail': 'Document service is temporarily unavailable.'})
        self.assertEqual(len(self.bucket.files), 1)
        self.assertEqual(self.bucket.deletes, 0)

    async def test_authorization_then_admission_before_gridfs_or_body(self):
        from test_documents import request, PDF as HTTP_PDF
        from auth import get_current_user
        from expensive_admission import Resource
        from main import app
        held = [self.admission.workflow(Resource.UPLOAD) for _ in range(2)]
        auth = app.dependency_overrides[get_current_user]
        try:
            self.current_owner = self.other
            self.assertEqual((await request('POST', self.path, content=HTTP_PDF))[0], 404)
            self.current_owner = self.owner
            self.assertEqual((await request('POST', self.path, content=HTTP_PDF))[0], 503)
            del app.dependency_overrides[get_current_user]
            self.assertEqual((await request('POST', self.path, content=HTTP_PDF))[0], 401)
            self.assertEqual(self.bucket.files, {})
            self.assertNotIn('storage_operation_intents', self.syncdb.rows)
        finally:
            app.dependency_overrides[get_current_user] = auth
            for work in held: work.finish()

    async def test_cancelled_http_upload_retains_admission_until_native_completion(self):
        from test_documents import request, PDF as HTTP_PDF
        from expensive_admission import Resource
        entered, release = threading.Event(), threading.Event()
        original = self.gridfs.storage_factory
        def factory(*args, **kwargs):
            store = original(*args, **kwargs)
            save = store.save
            def blocked(*args):
                entered.set()
                if not release.wait(5): raise RuntimeError('test timeout')
                return save(*args)
            store.save = blocked
            return store
        self.gridfs.storage_factory = factory
        task = asyncio.create_task(request('POST', self.path, content=HTTP_PDF))
        while not entered.is_set(): await asyncio.sleep(.001)
        try:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 1)
        finally:
            release.set()
        await asyncio.wait_for(self.admission.aclose(), 5)
        self.assertNotIn('documents', self.syncdb.rows)
        self.assertEqual(self.bucket.deletes, 0)
