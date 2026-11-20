"""Offline remote-provider contracts and existing owned-worker integration."""
import asyncio
import math
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from config import EmbeddingSettings, GeminiSettings, ConfigurationError, load_embedding_settings
from embeddings import embed_batches, embed_text, EmbeddingFailure
from embedding_execution import query_vector
from gemini_embeddings import embed_remote, MAX_INPUT_BYTES


def response(texts):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=[3., 4.] + [0.] * 766) for _ in texts])


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.settings = EmbeddingSettings(provider='gemini')
        self.client = Mock()
        self.client.__enter__ = Mock(return_value=self.client)
        self.client.__exit__ = Mock(return_value=False)
        self.client.models.embed_content.side_effect = lambda **kw: response(kw['contents'])
        self.factory = patch('google.genai.Client', return_value=self.client).start()
        self.key = patch('gemini_embeddings.load_gemini_settings', return_value=GeminiSettings('offline-key')).start()
        self.local = patch('embeddings.load_model', side_effect=AssertionError('Local model forbidden')).start()
        self.addCleanup(patch.stopall)

    def test_document_batches_and_normalization(self):
        batches = list(embed_batches(['synthetic'] * 33, self.settings))
        self.assertEqual([len(v) for _, v in batches], [16, 16, 1])
        for dimension, vectors in batches:
            self.assertEqual(dimension, 768)
            self.assertAlmostEqual(math.hypot(*vectors[0]), 1)
            self.assertEqual(vectors[0][:2], [.6, .8])
        for call in self.client.models.embed_content.call_args_list:
            self.assertEqual(call.kwargs['model'], 'gemini-embedding-001')
            self.assertEqual(call.kwargs['config'].task_type, 'RETRIEVAL_DOCUMENT')
            self.assertEqual(call.kwargs['config'].output_dimensionality, 768)
        self.local.assert_not_called()
        self.assertEqual(self.client.__exit__.call_count, 3)

    def test_query_never_loads_local_dimension(self):
        dimension, vector = query_vector('question', self.settings)
        self.assertEqual(dimension, 768)
        self.assertEqual(len(vector), 768)
        self.assertEqual(self.client.models.embed_content.call_args.kwargs['config'].task_type, 'RETRIEVAL_QUERY')
        self.local.assert_not_called()

    def test_transport_deadline_no_retries(self):
        embed_text('question', self.settings)
        options = self.factory.call_args.kwargs['http_options']
        self.assertEqual(options.timeout, 30000)
        self.assertEqual(options.retry_options.attempts, 1)
        self.assertFalse(self.factory.call_args.kwargs['vertexai'])

    def test_errors_sanitized_and_never_retried(self):
        for error in (TimeoutError('PRIVATE'), RuntimeError('PRIVATE')):
            self.client.models.embed_content.reset_mock()
            self.client.models.embed_content.side_effect = error
            with self.assertRaises(EmbeddingFailure) as caught:
                embed_text('question', self.settings)
            self.assertEqual(caught.exception.status, 503)
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertEqual(self.client.models.embed_content.call_count, 1)

    def test_late_response_rejected(self):
        with patch('gemini_embeddings.time.monotonic', side_effect=[0, 31]):
            with self.assertRaises(EmbeddingFailure):
                embed_text('question', self.settings)

    def test_invalid_response_vectors(self):
        for values in ([0.] * 768, [float('nan')] * 768, [float('inf')] * 768,
                       [True] * 768, [1.] * 384, ['x'] * 768, [1e308] * 768):
            self.client.models.embed_content.side_effect = None
            self.client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=values)])
            with self.subTest(values=type(values[0])), self.assertRaises(EmbeddingFailure):
                embed_text('question', self.settings)
        for result in (None, SimpleNamespace(embeddings=None), SimpleNamespace(embeddings=[]),
                       SimpleNamespace(embeddings=[SimpleNamespace(values=[1.] * 768)] * 2)):
            self.client.models.embed_content.return_value = result
            with self.assertRaises(EmbeddingFailure):
                embed_text('question', self.settings)

    def test_input_budget_before_client_creation(self):
        for text in ('', ' ', 'a' * (MAX_INPUT_BYTES + 1), '🚀' * (MAX_INPUT_BYTES // 4 + 1), '\ud800'):
            with self.assertRaises(EmbeddingFailure):
                embed_text(text, self.settings)
        self.factory.assert_not_called()
        embed_text('a' * MAX_INPUT_BYTES, self.settings)
        self.factory.assert_called_once()

    def test_batch_and_task_validation(self):
        for texts, task in (([], 'RETRIEVAL_QUERY'), (['x'] * 17, 'RETRIEVAL_DOCUMENT'), (['x'], 'UNKNOWN')):
            with self.assertRaises(EmbeddingFailure):
                embed_remote(texts, self.settings, task)
        self.factory.assert_not_called()

    def test_lossless_unicode_packing_and_one_vector_per_chunk(self):
        from gemini_embeddings import document_pieces
        text = ('Aurora café 世界 🚀. ' * 150)
        pieces = document_pieces(text)
        self.assertEqual(''.join(pieces), text)
        self.assertTrue(all(len(p.encode('utf-8')) <= MAX_INPUT_BYTES for p in pieces))
        result = list(embed_batches([text, 'short'], self.settings))
        self.assertEqual(sum(len(v) for _, v in result), 2)
        sent = [t for c in self.client.models.embed_content.call_args_list for t in c.kwargs['contents']]
        self.assertEqual(sent, pieces + ['short'])
        self.assertAlmostEqual(math.hypot(*result[0][1][0]), 1)

    def test_oversized_tail_rejected_before_any_provider_call(self):
        from gemini_embeddings import MAX_DOCUMENT_BYTES
        with self.assertRaises(EmbeddingFailure):
            list(embed_batches(['valid', 'x' * (MAX_DOCUMENT_BYTES + 1)], self.settings))
        self.factory.assert_not_called()

    def test_packing_weight_and_cancellation_of_opposite_vectors(self):
        text = 'a' * 1024 + 'b' * 512
        self.client.models.embed_content.side_effect = None
        self.client.models.embed_content.return_value = SimpleNamespace(embeddings=[
            SimpleNamespace(values=[1., 0.] + [0.] * 766),
            SimpleNamespace(values=[0., 1.] + [0.] * 766)])
        vector = list(embed_batches([text], self.settings))[0][1][0]
        self.assertAlmostEqual(vector[0] / vector[1], 2)
        self.client.models.embed_content.return_value.embeddings[1].values = [-1.] + [0.] * 767
        with self.assertRaises(EmbeddingFailure):
            list(embed_batches(['a' * 2048], self.settings))


class ConfigurationTests(unittest.TestCase):
    def load(self, values):
        with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, values, clear=True):
            return load_embedding_settings()

    def test_default_local_unchanged(self):
        self.assertEqual(self.load({}), EmbeddingSettings())

    def test_opt_in_model_identity(self):
        settings = self.load({'EMBEDDING_PROVIDER': 'gemini', 'QDRANT_COLLECTION_NAME': 'demo_gemini_768'})
        self.assertEqual(settings.model_name, 'gemini-embedding-001')
        self.assertEqual(settings.dimension, 768)

    def test_invalid_or_unsafe_configuration(self):
        for values in ({'EMBEDDING_PROVIDER': 'other'}, {'QDRANT_COLLECTION_NAME': ''},
                       {'QDRANT_COLLECTION_NAME': 'deepdocs_chunks'}, {'EMBEDDING_DIMENSION': '384'},
                       {'EMBEDDING_DIMENSION': 'bad'}, {'GEMINI_EMBEDDING_MODEL': 'other'},
                       {'EMBEDDING_BATCH_SIZE': '0'}):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                self.load({'EMBEDDING_PROVIDER': 'gemini', 'QDRANT_COLLECTION_NAME': 'demo', **values})


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_wait_deadline_sanitized_and_local_unchanged(self):
        from embedding_execution import run_embedding
        from unittest.mock import AsyncMock
        async def slow(*args):
            await asyncio.sleep(1)
        session = SimpleNamespace(run=AsyncMock(side_effect=slow))
        with patch('embedding_execution.REMOTE_DEADLINE_SECONDS', .001):
            with self.assertRaises(EmbeddingFailure):
                await run_embedding(session, lambda: None, settings=EmbeddingSettings(provider='gemini'))
            session.run = AsyncMock(return_value='local result')
            self.assertEqual(await run_embedding(session, lambda: None, settings=EmbeddingSettings()), 'local result')

    async def test_event_loop_call_rejected(self):
        with patch('google.genai.Client') as client, self.assertRaises(EmbeddingFailure):
            embed_text('question', EmbeddingSettings(provider='gemini'))
        client.assert_not_called()

    async def test_cancel_retains_worker_until_provider_finishes(self):
        from admission_lifecycle import AdmissionService
        from config import ExpensiveAdmissionSettings
        from cpu_workers import CPUWorkers
        service = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(service)
        entered, release = threading.Event(), threading.Event()
        owner = threading.get_ident()
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        def blocked(**kw):
            self.assertNotEqual(threading.get_ident(), owner)
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return response(kw['contents'])
        client.models.embed_content.side_effect = blocked
        async def work():
            with workers.embedding_session() as session:
                return await session.run(query_vector, 'question', EmbeddingSettings(provider='gemini'))
        try:
            with patch('google.genai.Client', return_value=client), patch('gemini_embeddings.load_gemini_settings', return_value=GeminiSettings('offline')):
                task = asyncio.create_task(work())
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(.001)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                closing = asyncio.create_task(workers.aclose())
                await asyncio.sleep(.02)
                self.assertFalse(closing.done())
                release.set()
                await closing
                client.models.embed_content.assert_called_once()
        finally:
            release.set()
            await workers.aclose()
            await service.aclose()


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_index_and_search_existing_routes(self):
        from test_embeddings import EmbeddingTests
        from test_knowledge_bases import request
        from main import app
        from document_routes import embedding_settings
        from search_routes import search_settings
        from config import QdrantSettings
        from vector_store import VectorStore
        fixture = EmbeddingTests()
        await fixture.asyncSetUp()
        try:
            body = await fixture.prepare()
            remote = EmbeddingSettings(provider='gemini')
            app.dependency_overrides[embedding_settings] = lambda: remote
            app.dependency_overrides[search_settings] = lambda: remote
            store = VectorStore(QdrantSettings('http://localhost:6333', collection_name='isolated_gemini_768'), fixture.qdrant)
            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            calls = []
            loop_thread = threading.get_ident()
            def embed(**kw):
                self.assertNotEqual(threading.get_ident(), loop_thread)
                calls.append(kw['config'].task_type)
                return response(kw['contents'])
            client.models.embed_content.side_effect = embed
            with (patch('google.genai.Client', return_value=client),
                  patch('gemini_embeddings.load_gemini_settings', return_value=GeminiSettings('offline')),
                  patch('embeddings.load_model', side_effect=AssertionError('Local model forbidden')),
                  patch('vector_store._store', store)):
                status, result, _ = await fixture.embed(body)
                self.assertEqual(status, 200)
                self.assertEqual(result['embedding_model'], 'gemini-embedding-001')
                self.assertEqual(result['embedding_dimension'], 768)
                self.assertEqual(result['collection_name'], 'isolated_gemini_768')
                status, _, _ = await request('POST', f'/api/knowledge-bases/{fixture.base_id}/search', {'query': 'First page'})
                self.assertEqual(status, 200)
            self.assertEqual(calls, ['RETRIEVAL_DOCUMENT', 'RETRIEVAL_QUERY'])
            self.assertEqual(set(fixture.qdrant.collections), {'isolated_gemini_768'})
        finally:
            await fixture.asyncTearDown()
            fixture.doCleanups()
