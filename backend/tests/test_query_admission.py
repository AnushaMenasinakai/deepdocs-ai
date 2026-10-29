"""Offline complete-workflow admission tests; synthetic records and inference."""
import asyncio
from contextlib import ExitStack
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from bson import ObjectId

from auth import get_current_user
from config import EmbeddingSettings, RAGSettings
from cpu_workers import Lane
from cpu_workers import CPUWorkers, get_cpu_workers
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from expensive_admission import Resource
from main import app
from rag_routes import rag_settings
from search_routes import search_settings
import test_documents as document_fixtures
from test_knowledge_bases import request
from test_rag import hit
from citation_helpers import claims_for


class QueryAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await document_fixtures.DocumentTests.asyncSetUp(self)
        self.settings = EmbeddingSettings()
        app.dependency_overrides[search_settings] = lambda: self.settings
        app.dependency_overrides[rag_settings] = lambda: RAGSettings()
        identifier, generation = ObjectId(), ObjectId()
        self.docs.documents[identifier] = dict(
            _id=identifier, owner_id=self.owner, knowledge_base_id=self.base_id,
            status='processed', chunk_generation=generation, chunk_count=1, page_count=1,
            vector_index=dict(status='indexed', chunk_generation=generation,
                              embedding_model=self.settings.model_name, chunk_count=1,
                              embedding_dimension=3, collection_name='test', target='test'))
        self.store = SimpleNamespace(collection='test', target='test', search=AsyncMock(return_value=[]))
        self.stack = ExitStack()
        self.stack.enter_context(patch('retrieval.get_vector_store', return_value=self.store))
        self.encode = self.stack.enter_context(patch('retrieval.query_vector', return_value=(3, [1., 0., 0.])))
        self.provider = SimpleNamespace(answer=AsyncMock(side_effect=claims_for('Supported answer.')))
        self.provider_factory = self.stack.enter_context(patch('rag.get_gemini_provider', return_value=self.provider))
        self.save = self.stack.enter_context(patch('ask_history.save_history', AsyncMock(return_value=True)))

    async def asyncTearDown(self):
        self.stack.close()
        await document_fixtures.DocumentTests.asyncTearDown(self)

    async def query(self, kind='search', base=None):
        return await request('POST', f'/api/knowledge-bases/{base or self.base_id}/{kind}',
                             {'query' if kind == 'search' else 'question': 'question'})

    def assert_busy(self, result):
        status, body, headers = result
        self.assertEqual(status, 503)
        self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
        self.assertEqual(headers[b'retry-after'], b'1')
        self.assertEqual(headers[b'cache-control'], b'no-store')

    def counts(self, query, gemini, cpu=0):
        actual = self.admission._admission.outstanding
        self.assertEqual((actual[Resource.QUERY], actual[Resource.GEMINI], actual[Resource.CPU]),
                         (query, gemini, cpu))

    async def test_saturation_atomicity_no_expensive_side_effects_and_liveness(self):
        for kind, resource in [('search', Resource.QUERY), ('ask', Resource.QUERY), ('ask', Resource.GEMINI)]:
            with self.subTest(kind=kind, resource=resource):
                reservations = [self.admission.workflow(resource) for _ in range(2)]
                before = self.admission._admission.outstanding
                try:
                    with patch.object(self.docs, 'find', wraps=self.docs.find) as scan:
                        self.assert_busy(await self.query(kind))
                        scan.assert_not_called()
                    self.assertEqual(self.admission._admission.outstanding, before)
                    self.encode.assert_not_called()
                    self.store.search.assert_not_awaited()
                    self.provider_factory.assert_not_called()
                    self.save.assert_not_awaited()
                    self.assertEqual((await request('GET', '/api/health'))[0], 200)
                finally:
                    for reservation in reservations:
                        reservation.finish()
                self.counts(0, 0)

    async def test_auth_and_owner_precedence_under_saturation(self):
        held = [self.admission.workflow(Resource.QUERY, Resource.GEMINI) for _ in range(2)]
        override = app.dependency_overrides[get_current_user]
        try:
            for kind in ('search', 'ask'):
                self.assertEqual((await self.query(kind, ObjectId()))[0], 404)
                self.current_owner = self.other
                self.assertEqual((await self.query(kind))[0], 404)
                self.current_owner = self.owner
                del app.dependency_overrides[get_current_user]
                self.assertEqual((await self.query(kind))[0], 401)
                app.dependency_overrides[get_current_user] = override
            self.encode.assert_not_called()
        finally:
            app.dependency_overrides[get_current_user] = override
            for item in held:
                item.finish()

    async def test_query_retained_after_cpu_and_unused_gemini_released_before_history(self):
        for kind in ('search', 'ask'):
            async def vector(*args):
                self.counts(1, int(kind == 'ask'), 0)
                return []
            async def history(*args):
                self.counts(1, 0, 0)
                return True
            self.store.search.side_effect = vector
            self.save.side_effect = history
            result = await self.query(kind)
            self.assertEqual(result[0], 200)
            self.assertEqual(result[1]['results'] if kind == 'search' else result[1]['status'],
                             [] if kind == 'search' else 'insufficient_context')
            self.counts(0, 0)
        self.provider_factory.assert_not_called()

    async def test_gemini_and_query_retained_through_generation_and_history(self):
        async def generate(*args):
            self.counts(1, 1)
            return await claims_for('Supported answer.')(*args)
        async def history(*args):
            self.counts(1, 1)
            return True
        self.provider.answer.side_effect = generate
        self.save.side_effect = history
        with patch('retrieval.search_chunks', AsyncMock(return_value=[hit()])):
            result = await self.query('ask')
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1]['status'], 'answered')
        self.assertEqual(result[1]['citation_version'], 1)
        self.provider.answer.assert_awaited_once()
        self.save.assert_awaited_once()
        self.counts(0, 0)

    async def test_cpu_saturation_releases_workflow_without_downstream_calls(self):
        with self.workers.embedding_session():
            for kind in ('search', 'ask'):
                self.assert_busy(await self.query(kind))
                self.counts(0, 0, 1)
        self.encode.assert_not_called()
        self.store.search.assert_not_awaited()
        self.provider_factory.assert_not_called()
        self.save.assert_not_awaited()

    async def test_cancelled_native_embedding_retains_all_ownership_until_completion(self):
        for kind in ('search', 'ask'):
            with self.subTest(kind=kind):
                entered, release = asyncio.Event(), threading.Event()
                loop = asyncio.get_running_loop()
                def encode(*args):
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(10):
                        raise AssertionError('native synchronization timed out')
                    return 3, [1., 0., 0.]
                self.encode.side_effect = encode
                task = asyncio.create_task(self.query(kind))
                terminal = None
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    terminal = self.workers._lanes[Lane.EMBEDDING]._terminal
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.counts(1, int(kind == 'ask'), 1)
                    self.assertFalse(terminal.done())
                    task.cancel()
                    self.store.search.assert_not_awaited()
                    self.save.assert_not_awaited()
                finally:
                    release.set()
                    await asyncio.gather(task, return_exceptions=True)
                    if terminal is not None:
                        await asyncio.wrap_future(terminal)
                self.counts(0, 0)

    async def test_cancellation_at_async_stages_cleans_local_ownership(self):
        for stage in ('scan', 'vector', 'generation', 'history'):
            with self.subTest(stage=stage), ExitStack() as patches:
                entered = asyncio.Event()
                async def blocked(*args, **kwargs):
                    entered.set()
                    await asyncio.Event().wait()
                if stage == 'scan':
                    class Cursor:
                        def __aiter__(self): return self
                        __anext__ = blocked
                    patches.enter_context(patch.object(self.docs, 'find', return_value=Cursor()))
                elif stage == 'vector':
                    patches.enter_context(patch.object(self.store, 'search', side_effect=blocked))
                else:
                    patches.enter_context(patch('retrieval.search_chunks', AsyncMock(return_value=[hit()])))
                    target = self.provider.answer if stage == 'generation' else self.save
                    patches.enter_context(patch.object(target, 'side_effect', blocked))
                task = asyncio.create_task(self.query('ask'))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    self.counts(1, 1)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.counts(0, 0)

    async def shutdown_case(self, kind):
        entered, proceed = asyncio.Event(), asyncio.Event()
        original = self.docs.find
        async def items():
            entered.set()
            await proceed.wait()
            async for item in original({'owner_id': self.owner, 'knowledge_base_id': self.base_id}):
                yield item
        with patch.object(self.docs, 'find', side_effect=lambda *a: items()):
            task = asyncio.create_task(self.query(kind))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.workers.begin_drain()
                closing = asyncio.create_task(self.admission.aclose())
                self.assert_busy(await self.query())
                self.assertFalse(closing.done())
                proceed.set()
                self.assertEqual((await task)[0], 200)
                await closing
            finally:
                proceed.set()
                await asyncio.gather(task, return_exceptions=True)
        self.encode.assert_called_once()
        self.counts(0, 0)

    async def test_search_shutdown_allows_late_embedding_but_rejects_new_workflows(self):
        await self.shutdown_case('search')

    async def test_ask_shutdown_allows_late_embedding_but_rejects_new_workflows(self):
        await self.shutdown_case('ask')

    async def test_cancelled_before_admission_does_no_expensive_work(self):
        task = asyncio.create_task(self.query('ask'))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.encode.assert_not_called()
        self.save.assert_not_awaited()
        self.counts(0, 0)

    async def test_separate_application_service_is_not_saturated(self):
        other = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(other)
        held = [self.admission.workflow(Resource.QUERY, Resource.GEMINI) for _ in range(2)]
        try:
            self.assert_busy(await self.query('ask'))
            app.dependency_overrides[get_cpu_workers] = lambda: workers
            self.assertEqual((await self.query('ask'))[0], 200)
            self.assertFalse(any(other._admission.outstanding.values()))
            self.counts(2, 2)
        finally:
            app.dependency_overrides[get_cpu_workers] = lambda: self.workers
            for workflow in held:
                workflow.finish()
            await other.aclose()
            await workers.aclose()

    async def test_provider_failure_releases_without_history_or_retry(self):
        from gemini_provider import GeminiFailure
        self.provider.answer.side_effect = GeminiFailure('private')
        with patch('retrieval.search_chunks', AsyncMock(return_value=[hit()])):
            result = await self.query('ask')
        self.assertEqual(result[0], 503)
        self.assertNotIn('private', str(result))
        self.provider.answer.assert_awaited_once()
        self.save.assert_not_awaited()
        self.counts(0, 0)
