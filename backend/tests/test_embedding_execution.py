"""Offline migration checks: fake inference and controlled asynchronous I/O."""
import asyncio
import copy
import threading
import unittest
from unittest.mock import patch, AsyncMock
from bson import ObjectId
import embeddings
from cpu_workers import SubmissionRejected, WorkerInfrastructureFailure
from expensive_admission import Resource, CapacityExceeded
from test_knowledge_bases import request
import test_embeddings as helpers


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.EmbeddingTests.asyncSetUp
    asyncTearDown = helpers.EmbeddingTests.asyncTearDown
    make_document = helpers.EmbeddingTests.make_document
    prepare = helpers.EmbeddingTests.prepare
    process = helpers.EmbeddingTests.process
    embed = helpers.EmbeddingTests.embed

    async def indexed(self):
        body = await self.prepare()
        with patch('embeddings.load_model', return_value=helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        return body

    async def query(self, kind='search'):
        data = {'query': 'question'} if kind == 'search' else {'question': 'question'}
        return await request('POST', f'/api/knowledge-bases/{self.base_id}/{kind}', data)

    async def test_load_dimension_encode_validate_all_on_one_worker(self):
        body = await self.prepare()
        owner, seen = threading.get_ident(), []
        model = helpers.FakeModel()
        original_dimension, original_encode = model.get_sentence_embedding_dimension, model.encode
        def load(name):
            seen.append(('load', threading.get_ident()))
            return model
        def dimension():
            seen.append(('dimension', threading.get_ident()))
            return original_dimension()
        def encode(*args, **kwargs):
            seen.append(('encode', threading.get_ident()))
            self.assertTrue(kwargs['normalize_embeddings'])
            self.assertTrue(kwargs['convert_to_numpy'])
            return original_encode(*args, **kwargs)
        original_validate = embeddings.validate_vector
        def validate(*args):
            seen.append(('validate', threading.get_ident()))
            return original_validate(*args)
        model.get_sentence_embedding_dimension, model.encode = dimension, encode
        with patch('embeddings._load_model', side_effect=load), patch('embeddings.validate_vector', side_effect=validate):
            self.assertEqual(seen, [])
            self.assertEqual((await self.embed(body))[0], 200)
            self.assertEqual((await self.query())[0], 200)
        self.assertEqual({name for name, _ in seen}, {'load', 'dimension', 'encode', 'validate'})
        self.assertEqual(len({thread for _, thread in seen}), 1)
        self.assertNotIn(owner, {thread for _, thread in seen})
        self.assertEqual([len(batch) for batch in model.batches], [2, 1, 1])

    async def test_generator_iteration_and_batch_order_on_worker(self):
        body = await self.prepare()
        owner, seen = threading.get_ident(), []
        def batches(texts, settings):
            seen.append(threading.get_ident())
            self.assertEqual(settings.batch_size, 2)
            self.assertEqual(texts, ['First page text.', 'Second page text.', 'Third page text.'])
            for offset in range(0, len(texts), 2):
                seen.append(threading.get_ident())
                yield 3, [[0.1, 0.2, 0.3] for _ in texts[offset:offset+2]]
        with patch('document_embeddings.embed_batches', side_effect=batches):
            result = await self.embed(body)
        self.assertEqual(result[0], 200)
        self.assertEqual(len(seen), 3)
        self.assertNotIn(owner, seen)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(result[1]['chunk_count'], 3)

    async def test_capacity_retained_across_vector_io_without_prefetch(self):
        body = await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.vector_store.upsert
        model = helpers.FakeModel()
        async def delayed(*args):
            entered.set()
            await release.wait()
            return await original(*args)
        with patch('embeddings.load_model', return_value=model), patch.object(self.vector_store, 'upsert', side_effect=delayed):
            task = asyncio.create_task(self.embed(body))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assertEqual(len(model.batches), 1)
                with self.assertRaises(CapacityExceeded):
                    with self.workers.embedding_session():
                        pass
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                self.assertEqual(self.workers._mailbox.qsize(), 0)
            finally:
                release.set()
            self.assertEqual((await task)[0], 200)
        self.assertEqual([len(batch) for batch in model.batches], [2, 1])
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_busy_preclaim_no_mutation_or_model(self):
        body = await self.prepare()
        before = copy.deepcopy(self.docs.documents)
        with self.workers.embedding_session(), patch('document_embeddings.claim_document', new_callable=AsyncMock) as claim, patch('embeddings.load_model') as model:
            status, result, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertEqual(result, {'detail': 'Service is busy. Please try again later.'})
        claim.assert_not_awaited()
        model.assert_not_called()
        self.assertEqual(before, self.docs.documents)

    async def test_search_ask_busy_no_generation_or_history(self):
        await self.indexed()
        before = copy.deepcopy(self.history.documents)
        with self.workers.embedding_session(), patch('rag.get_gemini_provider') as provider:
            for kind in ('search', 'ask'):
                status, body, headers = await self.query(kind)
                self.assertEqual(status, 503)
                self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
                self.assertEqual(headers[b'cache-control'], b'no-store')
                self.assertEqual(headers[b'retry-after'], b'1')
            provider.assert_not_called()
        self.assertEqual(before, self.history.documents)

    async def test_blocked_encode_responsive_cancellation_and_shutdown(self):
        body = await self.prepare()
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        model = helpers.FakeModel()
        def encode(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise AssertionError('synchronization failed')
            return [[0.1, 0.2, 0.3] for _ in args[0]]
        model.encode = encode
        closing = None
        with patch('embeddings.load_model', return_value=model), patch.object(self.vector_store, 'upsert', new_callable=AsyncMock) as upsert:
            task = asyncio.create_task(self.embed(body))
            try:
                await asyncio.wait_for(started.wait(), 5)
                self.assertEqual((await request('GET', '/api/health'))[0], 200)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                self.assertEqual(self.admission._admission.outstanding[Resource.QUERY], 0)
                self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 0)
                self.assertIn('_operation', self.docs.documents[ObjectId(body['id'])])
                upsert.assert_not_awaited()
                self.admission.begin_shutdown()
                closing = asyncio.create_task(self.admission.aclose())
                self.assertFalse(closing.done())
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if closing is not None:
                    await closing
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)
        self.assertIn('_operation', self.docs.documents[ObjectId(body['id'])])

    async def test_cancel_before_dispatch_does_not_claim(self):
        body = await self.prepare()
        async def call():
            asyncio.current_task().cancel()
            await self.embed(body)
        with patch('document_embeddings.claim_document', new_callable=AsyncMock) as claim:
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.create_task(call())
            claim.assert_not_awaited()
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_known_submission_failure_releases_and_sanitizes(self):
        body = await self.prepare()
        with patch.object(self.workers, '_submit_embedding', side_effect=SubmissionRejected('PRIVATE')):
            status, result, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_model_load_failure_then_explicit_success(self):
        body = await self.prepare()
        with patch('embeddings._load_model', side_effect=RuntimeError('PRIVATE')):
            status, result, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)
        with patch('embeddings.load_model', return_value=helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)

    async def test_infrastructure_failure_does_not_publish_failed_or_unlock(self):
        body = await self.prepare()
        with patch('cpu_workers.ExecutionSession.run', new_callable=AsyncMock, side_effect=WorkerInfrastructureFailure()):
            self.assertEqual((await self.embed(body))[0], 503)
        row = self.docs.documents[ObjectId(body['id'])]
        self.assertIn('_operation', row)
        self.assertEqual(row['embedding']['status'], 'generating')

    async def test_bulk_busy_before_first_item(self):
        body = await self.prepare()
        before = copy.deepcopy(self.docs.documents)
        with self.workers.embedding_session(), patch('document_embeddings.claim_document', new_callable=AsyncMock) as claim:
            status, result, _ = await request('POST', self.path+'/bulk-reindex', {'document_ids': [body['id']]})
        self.assertEqual(status, 503)
        claim.assert_not_awaited()
        self.assertEqual(before, self.docs.documents)

    async def test_query_cancellation_retains_worker_and_skips_vector_search(self):
        await self.indexed()
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        model = helpers.FakeModel()
        def encode(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise AssertionError('synchronization failed')
            return [[0.1, 0.2, 0.3]]
        model.encode = encode
        terminal = None
        with patch('embeddings.load_model', return_value=model), patch.object(self.vector_store, 'search', new_callable=AsyncMock) as search:
            task = asyncio.create_task(self.query())
            try:
                await asyncio.wait_for(started.wait(), 5)
                from cpu_workers import Lane
                terminal = self.workers._lanes[Lane.EMBEDDING]._terminal
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(terminal.done())
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                self.assertEqual(self.admission._admission.outstanding[Resource.QUERY], 0)
                search.assert_not_awaited()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if terminal is not None:
                    await asyncio.wrap_future(terminal)
            search.assert_not_awaited()

    async def test_bulk_holds_one_session_between_documents(self):
        first, second = await self.prepare(), await self.prepare()
        import document_embeddings
        original = document_embeddings.generate_embeddings
        sessions = []
        async def checked(*args, **kwargs):
            sessions.append(kwargs['session'])
            self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
            with self.assertRaises(CapacityExceeded):
                with self.workers.embedding_session():
                    pass
            return await original(*args, **kwargs)
        with patch('document_embeddings.generate_embeddings', side_effect=checked), patch('embeddings.load_model', return_value=helpers.FakeModel()):
            status, result, _ = await request('POST', self.path+'/bulk-reindex', {'document_ids': [first['id'], second['id']]})
        self.assertEqual(status, 200)
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(len(sessions), 2)
        self.assertIs(sessions[0], sessions[1])
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_worker_exception_sanitized_no_partial_publication(self):
        body = await self.prepare()
        model = helpers.FakeModel()
        model.encode = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('PRIVATE'))
        with patch('embeddings.load_model', return_value=model):
            status, result, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotEqual(self.docs.documents[ObjectId(body['id'])]['vector_index']['status'], 'indexed')
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)
