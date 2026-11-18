"""Real PDF/ASGI orchestration with fake metadata, Qdrant and Gemini SDK only."""
import copy
import json
import unittest
from unittest.mock import Mock, AsyncMock, patch
from bson import ObjectId
from config import EmbeddingSettings, GeminiSettings, ProcessingSettings, QdrantSettings
from main import app
from document_routes import embedding_settings, processing_settings
from search_routes import search_settings
from vector_store import VectorStore, point_id
from gemini_provider import GeminiProvider
import test_rag as rag_helpers
from test_rag import response as generation_response
from test_gemini_embeddings import response


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = rag_helpers.AskTests()
        await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = EmbeddingSettings(provider='gemini')
        await self.fixture.setup_ask()
        app.dependency_overrides[embedding_settings] = lambda: self.settings
        app.dependency_overrides[search_settings] = lambda: self.settings
        self.store = VectorStore(QdrantSettings('http://localhost:6333',
            collection_name='test_gemini_768'), self.fixture.qdrant)
        self.client = Mock()
        self.client.__enter__ = Mock(return_value=self.client)
        self.client.__exit__ = Mock(return_value=False)
        self.client.models.embed_content.side_effect = lambda **kw: response(kw['contents'])
        self.client.aio.models.generate_content = AsyncMock(return_value=generation_response(json.dumps(
            {'supported': True, 'claims': [{'text': 'Alex Morgan coordinates Aurora.', 'citation_ids': [1]}]})))
        self.patches = [patch('google.genai.Client', return_value=self.client),
            patch('gemini_embeddings.load_gemini_settings', return_value=GeminiSettings('offline')),
            patch('embeddings.load_model', side_effect=AssertionError('Local model forbidden')),
            patch('vector_store._store', self.store), patch('rag_routes.load_verified_rag_enabled', return_value=False)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.provider = GeminiProvider(GeminiSettings('offline'))
        p = patch('rag.get_gemini_provider', return_value=self.provider)
        p.start()
        self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def document(self):
        text = 'Alex Morgan coordinates Aurora. ' + 'The synthetic observatory tracks river temperatures. ' * 30
        body = await self.fixture.make_document([text])
        app.dependency_overrides[processing_settings] = lambda: ProcessingSettings(chunk_target=2000, chunk_overlap=100)
        self.assertEqual((await self.fixture.process(body))[0], 200)
        self.assertTrue(any(len(c['text'].encode('utf-8')) > 1024 for c in self.fixture.chunks.documents.values()))
        return body

    async def test_full_journey_citations_history_and_immutable_chunks(self):
        body = await self.document()
        chunks = copy.deepcopy(self.fixture.chunks.documents)
        self.assertEqual((await self.fixture.embed(body))[0], 200)
        self.assertEqual(self.fixture.chunks.documents, chunks)
        points = self.fixture.qdrant.points[self.store.collection]
        self.assertEqual(set(points), {point_id(c) for c in chunks.values()})
        for c in chunks.values():
            p = points[point_id(c)]
            self.assertEqual(p.payload['text'], c['text'])
            self.assertEqual(p.payload['chunk_id'], str(c['_id']))
            self.assertEqual(len(p.vector), 768)
        status, result, _ = await self.fixture.ask({'question': 'Who coordinates Aurora?'})
        self.assertEqual(status, 200)
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['answer'], 'Alex Morgan coordinates Aurora.')
        self.assertEqual(result['sources'][0]['document_id'], body['id'])
        self.assertEqual(result['sources'][0]['page_start'], 1)
        self.assertEqual(result['sources'][0]['source_filename'], body['filename'])
        self.assertEqual(len(self.fixture.history.documents), 1)
        self.client.aio.models.generate_content.assert_awaited_once()
        self.assertEqual(self.client.models.embed_content.call_args.kwargs['config'].task_type, 'RETRIEVAL_QUERY')
        self.assertEqual(set(self.fixture.qdrant.collections), {self.store.collection})

    async def test_owner_and_kb_filtering(self):
        body = await self.document()
        self.assertEqual((await self.fixture.embed(body))[0], 200)
        self.client.models.embed_content.reset_mock()
        self.fixture.current_owner = self.fixture.other
        self.assertEqual((await self.fixture.embed(body))[0], 404)
        self.assertEqual((await self.fixture.ask())[0], 404)
        self.client.models.embed_content.assert_not_called()
        self.fixture.current_owner = self.fixture.owner
        other_base = ObjectId()
        self.fixture.bases.documents[other_base] = {**self.fixture.bases.documents[self.fixture.base_id], '_id': other_base}
        status, value, _ = await self.fixture.ask(base=other_base)
        self.assertEqual(status, 200)
        self.assertEqual(value['status'], 'insufficient_context')
        self.client.models.embed_content.assert_not_called()
        await self.fixture.ask()
        filters = self.fixture.qdrant.last_query['filter']
        point = next(iter(self.fixture.qdrant.points[self.store.collection].values()))
        for key in ('owner_id', 'knowledge_base_id'):
            foreign = copy.deepcopy(point)
            foreign.payload[key] = str(ObjectId())
            self.assertFalse(self.fixture.qdrant.matches(foreign, filters))

    async def test_provider_quota_and_query_failure_no_answer_history(self):
        from google.genai.errors import ClientError
        body = await self.document()
        self.client.models.embed_content.side_effect = ClientError(429, {'error': {'message': 'PRIVATE'}})
        status, result, _ = await self.fixture.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(self.fixture.qdrant.points, {})
        self.client.models.embed_content.side_effect = lambda **kw: response(kw['contents'])
        self.assertEqual((await self.fixture.embed(body))[0], 200)
        self.client.models.embed_content.side_effect = TimeoutError('PRIVATE')
        status, result, _ = await self.fixture.ask()
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(self.fixture.history.documents, {})
        self.client.aio.models.generate_content.assert_not_awaited()

    async def test_existing_minilm_index_is_never_cleaned_up(self):
        body = await self.document()
        row = self.fixture.docs.documents[ObjectId(body['id'])]
        previous = {'collection_name': 'existing_minilm', 'embedding_model': EmbeddingSettings.model_name,
                    'embedding_dimension': 384, 'status': 'indexed'}
        row['vector_index'] = copy.deepcopy(previous)
        with patch('document_embeddings.cleanup_document_vectors', new_callable=AsyncMock) as cleanup:
            self.assertEqual((await self.fixture.embed(body))[0], 409)
            cleanup.assert_not_awaited()
        self.assertEqual(row['vector_index'], previous)
        self.client.models.embed_content.assert_not_called()

    async def test_existing_wrong_dimension_collection_not_modified(self):
        from qdrant_client.models import VectorParams, Distance
        body = await self.document()
        self.fixture.qdrant.collections[self.store.collection] = VectorParams(size=384, distance=Distance.COSINE)
        self.fixture.qdrant.points[self.store.collection] = {}
        self.assertEqual((await self.fixture.embed(body))[0], 503)
        self.assertEqual(self.fixture.qdrant.collections[self.store.collection].size, 384)
        self.assertEqual(self.fixture.qdrant.points[self.store.collection], {})

    async def test_oversized_chunk_fails_without_partial_index(self):
        from gemini_embeddings import MAX_DOCUMENT_BYTES
        body = await self.document()
        chunk = next(iter(self.fixture.chunks.documents.values()))
        chunk['text'] = 'x' * (MAX_DOCUMENT_BYTES + 1)
        original_id = chunk['_id']
        self.assertEqual((await self.fixture.embed(body))[0], 409)
        self.assertEqual(chunk['_id'], original_id)
        self.assertEqual(len(chunk['text']), MAX_DOCUMENT_BYTES + 1)
        self.client.models.embed_content.assert_not_called()
        self.assertEqual(self.fixture.qdrant.points, {})

    async def test_forged_retrieval_payload_and_generation_failure(self):
        body = await self.document()
        self.assertEqual((await self.fixture.embed(body))[0], 200)
        original = self.fixture.qdrant.query_points
        async def forged(*args, **kwargs):
            result = await original(*args, **kwargs)
            for point in result.points:
                point.payload['owner_id'] = str(ObjectId())
            return result
        with patch.object(self.fixture.qdrant, 'query_points', side_effect=forged):
            status, result, _ = await self.fixture.ask()
            self.assertEqual(status, 200)
            self.assertEqual(result['status'], 'insufficient_context')
            self.assertEqual(result['sources'], [])
            self.client.aio.models.generate_content.assert_not_awaited()
        before = copy.deepcopy(self.fixture.history.documents)
        self.client.aio.models.generate_content.side_effect = TimeoutError('PRIVATE')
        status, result, _ = await self.fixture.ask()
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(self.fixture.history.documents, before)
