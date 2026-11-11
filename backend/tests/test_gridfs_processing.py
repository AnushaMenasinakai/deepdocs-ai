"""Real processing/publication and PDF supervisor, isolated GridFS bytes only."""
import asyncio
import hashlib
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bson import ObjectId
from gridfs_processing import GridFSProcessing
from gridfs_storage import GridFSPDFStorage
from storage_workers import StorageWorkers
from expensive_admission import Resource
from main import app
import test_documents as document_fixtures
import test_processing as processing_fixtures
from test_gridfs_storage import Bucket
from test_parser_leases import Supervisor
from pdf_subprocess import PDFSupervisor
from pdf_subprocess_helpers import synthetic_child


class GridFSProcessingTests(unittest.IsolatedAsyncioTestCase):
    make_document = processing_fixtures.ProcessingTests.make_document
    process = processing_fixtures.ProcessingTests.process

    async def asyncSetUp(self):
        await document_fixtures.DocumentTests.asyncSetUp(self)
        self.old = getattr(app.state, 'gridfs_processing', None)
        self.bucket = Bucket()
        self.storage_workers = StorageWorkers(Mock())
        self.paths = []
        self.remote = GridFSProcessing(self.storage_workers, None, self.workers,
                                       storage_factory=self.factory)
        app.state.gridfs_processing = self.remote

    def factory(self, db, **kwargs):
        storage = GridFSPDFStorage(db, bucket=self.bucket, **kwargs)
        original = storage.materialize
        def materialize(document):
            lease = original(document)
            self.paths.append(lease.path)
            return lease
        storage.materialize = materialize
        return storage

    async def asyncTearDown(self):
        app.state.gridfs_processing = self.old
        await self.remote.leases.aclose()
        await self.storage_workers.aclose()
        await document_fixtures.DocumentTests.asyncTearDown(self)

    async def grid_document(self):
        body = await self.make_document()
        identifier = ObjectId(body['id'])
        row = self.docs.documents[identifier]
        path = self.root / (body['id'] + '.pdf')
        data = path.read_bytes()
        path.unlink() # disposable test-owned local fixture, never production data
        self.bucket.files[identifier] = (data, {'document_id': identifier,
            'owner_id': self.owner, 'knowledge_base_id': self.base_id})
        row.update(storage_backend='gridfs', storage_file_id=identifier,
                   storage_checksum=hashlib.sha256(data).hexdigest())
        return body, row

    async def test_real_supervisor_success_and_reprocessing_immutable_original(self):
        body, row = await self.grid_document()
        original = self.bucket.files.copy()
        status, result, _ = await self.process(body)
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'processed')
        first = row['chunk_generation']
        supervisor = self.workers._pdf_supervisor
        self.assertTrue(all(not p.exists() for p in self.paths))
        self.assertEqual((await self.process(body))[0], 200)
        self.assertNotEqual(row['chunk_generation'], first)
        self.assertIs(self.workers._pdf_supervisor, supervisor)
        self.assertTrue(all(c['generation'] == row['chunk_generation'] for c in self.chunks.documents.values()))
        self.assertEqual(row['vector_index']['status'], 'not_generated')
        self.assertEqual(self.bucket.files, original)
        self.assertEqual(self.bucket.deletes, 0)

    async def test_local_compatibility_no_materialization(self):
        body = await self.make_document()
        self.assertEqual((await self.process(body))[0], 200)
        self.assertEqual(self.paths, [])
        self.assertTrue((self.root / (body['id'] + '.pdf')).exists())

    async def test_missing_corrupt_and_wrong_kb_never_fallback(self):
        for failure in ('missing', 'corrupt', 'wrong_kb', 'wrong_owner', 'wrong_file', 'unknown_backend'):
            body, row = await self.grid_document()
            identifier = row['_id']
            data, metadata = self.bucket.files[identifier]
            if failure == 'missing': del self.bucket.files[identifier]
            elif failure == 'corrupt': self.bucket.files[identifier] = (data + b'changed', metadata)
            elif failure == 'wrong_kb': metadata['knowledge_base_id'] = ObjectId()
            elif failure == 'wrong_owner': metadata['owner_id'] = ObjectId()
            elif failure == 'wrong_file': row['storage_file_id'] = ObjectId()
            else: row['storage_backend'] = 'unknown'
            with patch.object(self.storage, 'processing_path', side_effect=AssertionError('fallback')) as local:
                self.assertEqual((await self.process(body))[0], 503)
                local.assert_not_called()
            self.assertNotEqual(row['status'], 'processed')
        self.assertEqual(self.chunks.documents, {})

    async def test_owner_and_capacity_precede_materialization(self):
        body, row = await self.grid_document()
        self.current_owner = self.other
        self.assertEqual((await self.process(body))[0], 404)
        self.current_owner = self.owner
        with self.admission.workflow(Resource.CPU):
            self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual(self.paths, [])
        self.assertNotIn('_operation', row)

    async def test_cancellation_during_materialization_never_dispatches(self):
        body, row = await self.grid_document()
        entered, release = threading.Event(), threading.Event()
        original = self.factory
        def factory(*args, **kwargs):
            storage = original(*args, **kwargs)
            materialize = storage.materialize
            def blocked(document):
                entered.set()
                if not release.wait(5): raise RuntimeError('test timeout')
                return materialize(document)
            storage.materialize = blocked
            return storage
        self.remote.storage_factory = factory
        task = asyncio.create_task(self.process(body))
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
        release.set()
        await self.remote.leases.aclose()
        self.assertIsNone(self.workers._pdf_supervisor)
        self.assertTrue(all(not p.exists() for p in self.paths))
        self.assertIn('_operation', row)

    async def test_unknown_completion_cancellation_and_shutdown_order(self):
        body, row = await self.grid_document()
        supervisor = Supervisor()
        self.workers._pdf_supervisor = supervisor
        task = asyncio.create_task(self.process(body))
        while not supervisor.calls: await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        closing = asyncio.create_task(self.admission.aclose())
        await asyncio.sleep(.02)
        self.assertFalse(closing.done())
        self.assertTrue(self.paths[0].exists())
        self.assertTrue(self.remote.leases.unresolved())
        self.assertEqual(supervisor.calls, 1)
        # Only the simulated supervisor's reaping signal permits cleanup.
        supervisor.future.set_result((1, []))
        await closing
        self.assertFalse(self.paths[0].exists())
        self.assertEqual(self.chunks.documents, {})
        self.assertIn('_operation', row)

    async def test_real_child_crash_reaped_before_lease_cleanup(self):
        body, row = await self.grid_document()
        supervisor = PDFSupervisor(_target=synthetic_child)
        self.workers._pdf_supervisor = supervisor
        submit = supervisor.submit
        with patch.object(supervisor, 'submit', side_effect=lambda path, settings:
                          submit(str(Path(path).with_name('crash')), settings)):
            self.assertEqual((await self.process(body))[0], 503)
        self.assertTrue(all(not p.exists() for p in self.paths))
        self.assertIn('_operation', row)
        self.assertFalse(any(self.admission._admission.outstanding.values()))
        self.assertEqual(self.chunks.documents, {})

    async def test_cleanup_precedes_publication_and_shutdown_drain(self):
        body, row = await self.grid_document()
        entered, release = threading.Event(), threading.Event()
        original = self.factory
        def factory(*args, **kwargs):
            storage = original(*args, **kwargs)
            materialize = storage.materialize
            def owned(document):
                lease = materialize(document)
                close = lease.close
                def blocked_close():
                    entered.set()
                    if not release.wait(5): raise RuntimeError('test timeout')
                    close()
                lease.close = blocked_close
                return lease
            storage.materialize = owned
            return storage
        self.remote.storage_factory = factory
        task = asyncio.create_task(self.process(body))
        while not entered.is_set(): await asyncio.sleep(.001)
        closing = asyncio.create_task(self.admission.aclose())
        try:
            await asyncio.sleep(.01)
            self.assertFalse(closing.done())
            self.assertEqual(self.chunks.documents, {})
            self.assertEqual(row['status'], 'processing')
            self.assertTrue(self.paths[0].exists())
            self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
        finally:
            release.set()
        self.assertEqual((await task)[0], 200)
        await closing
        self.assertFalse(self.paths[0].exists())
