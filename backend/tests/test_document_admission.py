"""Document workflow saturation and genuine native completion, offline."""
import asyncio
import copy
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from bson import ObjectId
from auth import get_current_user
from expensive_admission import Resource
from main import app
from test_knowledge_bases import request
import test_bulk_reindex as fixtures
import test_documents as document_helpers


class DocumentAdmissionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkReindexTests.asyncSetUp
    asyncTearDown = document_helpers.DocumentTests.asyncTearDown
    upload = document_helpers.DocumentTests.upload
    prepared = fixtures.BulkReindexTests.prepared

    async def test_all_workflows_saturate_before_claims_with_owner_auth_precedence(self):
        identifier = await self.prepared()
        routes = [('POST', f'/api/documents/{identifier}/embeddings', None),
                  ('DELETE', f'/api/documents/{identifier}', None),
                  ('GET', f'/api/documents/{identifier}/vector-status', None),
                  ('POST', self.path + '/bulk-reindex', {'document_ids': [identifier]}),
                  ('POST', self.path + '/bulk-delete', {'document_ids': [identifier]})]
        before = copy.deepcopy(self.docs.documents)
        held = self.admission.workflow(Resource.DOCUMENT)
        identity = app.dependency_overrides[get_current_user]
        try:
            for method, path, data in routes:
                with self.subTest(path=path):
                    status, body, _ = await request(method, path, data)
                    self.assertEqual(status, 503)
                    self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
                    self.current_owner = self.other
                    self.assertEqual((await request(method, path, data))[0], 404)
                    self.current_owner = self.owner
                    del app.dependency_overrides[get_current_user]
                    self.assertEqual((await request(method, path, data))[0], 401)
                    app.dependency_overrides[get_current_user] = identity
            self.assertEqual(self.docs.documents, before)
            self.assertFalse(self.model.batches)
            self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)
        finally:
            self.current_owner = self.owner
            app.dependency_overrides[get_current_user] = identity
            held.finish()

    async def test_cpu_rejection_precedes_claim_and_releases_document_slot(self):
        identifier = await self.prepared()
        before = copy.deepcopy(self.docs.documents)
        with self.workers.embedding_session():
            for path, data in ((f'/api/documents/{identifier}/embeddings', None),
                               (self.path + '/bulk-reindex', {'document_ids': [identifier]})):
                with self.subTest(path=path):
                    self.assertEqual((await request('POST', path, data))[0], 503)
                    self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 0)
        self.assertEqual(before, self.docs.documents)

    async def test_bulk_session_not_nested_and_partial_results_preserved(self):
        ids = [await self.prepared(), str(ObjectId()), await self.prepared()]
        seen = []
        def batches(texts, settings):
            seen.append(self.admission._admission.outstanding)
            yield 3, [[1., 0., 0.] for _ in texts]
        with patch('document_embeddings.embed_batches', side_effect=batches):
            status, body, _ = await request('POST', self.path + '/bulk-reindex', {'document_ids': ids})
        self.assertEqual(status, 200)
        self.assertEqual((body['succeeded'], body['failed']), (2, 1))
        self.assertEqual([r['document_id'] for r in body['results']], ids)
        self.assertTrue(seen)
        self.assertTrue(all(s[Resource.DOCUMENT] == s[Resource.CPU] == 1 for s in seen))
        self.assertFalse(any(self.admission._admission.outstanding.values()))

    async def test_cancelled_encoding_retains_document_and_cpu_until_native_finish(self):
        identifier = await self.prepared()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def batches(*args):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise RuntimeError('test timeout')
            yield 3, [[1., 0., 0.]] * 3
        with patch('document_embeddings.embed_batches', side_effect=batches):
            task = asyncio.create_task(request('POST', f'/api/documents/{identifier}/embeddings'))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            try:
                self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                self.assertIn('_operation', self.docs.documents[ObjectId(identifier)])
            finally:
                release.set()
            await asyncio.wait_for(self.admission.aclose(), 5)
        self.assertFalse(any(self.admission._admission.outstanding.values()))

    async def test_cancelled_native_delete_retains_capacity_and_claim(self):
        await self.cancelled_delete(False)

    async def test_cancelled_bulk_native_delete_retains_capacity_and_claim(self):
        await self.cancelled_delete(True)

    async def cancelled_delete(self, bulk):
        identifier = (await self.upload())['id']
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = self.storage.delete
        def deletion(document):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(document)
        with patch.object(self.storage, 'delete', side_effect=deletion):
            task = asyncio.create_task(request('POST', self.path + '/bulk-delete', {'document_ids': [identifier]})
                                       if bulk else request('DELETE', f'/api/documents/{identifier}'))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.admission.begin_shutdown()
            try:
                self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
                self.assertFalse(self.admission._drained.done())
                self.assertIn('_operation', self.docs.documents[ObjectId(identifier)])
            finally:
                release.set()
            await asyncio.wait_for(self.admission.aclose(), 5)
        self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 0)
        self.assertIn('_operation', self.docs.documents[ObjectId(identifier)])

    async def test_vector_status_holds_document_through_metadata_write(self):
        identifier = (await self.upload())['id']
        entered, release = asyncio.Event(), asyncio.Event()
        from document_operations import record_operation_stage
        async def blocked(*args, **kwargs):
            result = await record_operation_stage(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        with patch('document_routes.record_operation_stage', side_effect=blocked):
            task = asyncio.create_task(request('GET', f'/api/documents/{identifier}/vector-status'))
            await entered.wait()
            try:
                self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
                self.assertIn('_operation_meta', self.docs.documents[ObjectId(identifier)])
            finally:
                release.set()
            self.assertEqual((await task)[0], 200)
        self.assertNotIn('_operation', self.docs.documents[ObjectId(identifier)])
        self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 0)

    async def test_separate_app_worker_services_do_not_share_document_capacity(self):
        from admission_lifecycle import AdmissionService
        from config import ExpensiveAdmissionSettings
        from cpu_workers import CPUWorkers
        from document_routes import vector_status
        identifier = (await self.upload())['id']
        other = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(other)
        try:
            with self.admission.workflow(Resource.DOCUMENT):
                result = await vector_status(identifier, current_user=SimpleNamespace(id=str(self.owner)),
                                             database=self.database, workers=workers)
                self.assertEqual(result['document_id'], identifier)
                self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
                self.assertEqual(other._admission.outstanding[Resource.DOCUMENT], 0)
        finally:
            await other.aclose()
            await workers.aclose()

    async def test_native_delete_failure_and_pre_dispatch_seal_release_once(self):
        from documents import _DeletionWork
        from pymongo.errors import PyMongoError
        calls = []
        def failed(document):
            calls.append(document)
            raise OSError('synthetic')
        with self.admission.workflow(Resource.DOCUMENT) as workflow:
            work = _DeletionWork(workflow)
            with self.assertRaises(OSError):
                work.run(failed, {})
            work.close()
            work.close()
            self.assertTrue(work._terminal.done())
        with self.admission.workflow(Resource.DOCUMENT) as workflow:
            work = _DeletionWork(workflow)
            work.close()
            with self.assertRaises(PyMongoError):
                work.run(failed, {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 0)
