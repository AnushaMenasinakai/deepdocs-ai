"""Offline deletion dispatch/commit/cancellation regressions."""
import asyncio
import copy
import threading
import unittest
from unittest.mock import patch, AsyncMock
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_document_bulk as fixtures
from document_embeddings import generate_embeddings
from document_operations import DocumentBusy
from document_inspection import classify
from config import EmbeddingSettings


class DeletionCancellationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkDeleteTests.asyncSetUp
    asyncTearDown = fixtures.BulkDeleteTests.asyncTearDown
    upload = fixtures.BulkDeleteTests.upload
    indexed = fixtures.BulkDeleteTests.indexed
    bulk = fixtures.BulkDeleteTests.bulk

    def retained(self, identifier):
        row = self.docs.documents[ObjectId(identifier)]
        self.assertIn('_operation', row)
        self.assertEqual(classify(row['_id'], {'status': row['status'], 'claimed': True,
            'indexed': False}).attention, 'outcome_uncertain')
        return row

    async def test_delayed_vector_delete_blocks_replacement_and_bulk_continuation(self):
        for mode in ('timeout', 'ack_loss', 'cancel'):
            with self.subTest(mode=mode):
                first, later = await self.indexed(), await self.indexed()
                before = copy.deepcopy(self.docs.documents[ObjectId(later)])
                original = self.qdrant.delete
                pending, arrived = [], asyncio.Event()
                async def remote(*args, **kwargs):
                    pending.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
                    if mode == 'ack_loss':
                        await original(*args, **kwargs)
                    arrived.set()
                    if mode == 'cancel':
                        await asyncio.Event().wait()
                    raise TimeoutError('PRIVATE')
                with patch.object(self.qdrant, 'delete', side_effect=remote):
                    task = asyncio.create_task(self.bulk([first, later]))
                    await asyncio.wait_for(arrived.wait(), 3)
                    if mode == 'cancel':
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        status, report, _ = await task
                        self.assertEqual(status, 200)
                        self.assertEqual([r.get('code', r['outcome']) for r in report['results']],
                                         ['service_unavailable', 'not_attempted'])
                        self.assertNotIn('PRIVATE', str(report))
                token = self.retained(first)['_operation']
                with self.assertRaises(DocumentBusy):
                    await generate_embeddings(self.database, self.owner, ObjectId(first), EmbeddingSettings())
                if mode != 'ack_loss':
                    await original(*pending[0][0], **pending[0][1])
                self.assertEqual(len(pending), 1)
                self.assertEqual(self.retained(first)['_operation'], token)
                self.assertEqual(self.docs.documents[ObjectId(later)], before)
                self.assertTrue(any(p.payload['document_id'] == later for p in self.qdrant.points[self.vector_store.collection].values()))

    async def test_later_mutations_uncertain_or_cancelled_never_unlock(self):
        for stage in ('invalidation', 'chunks', 'reservation', 'metadata'):
            for mode in ('ack_loss', 'cancel'):
                with self.subTest(stage=stage, mode=mode):
                    first, later = await self.indexed(), await self.indexed()
                    before = copy.deepcopy(self.docs.documents[ObjectId(later)])
                    target, method = {'invalidation': (self.docs, 'update_one'),
                        'chunks': (self.chunks, 'delete_many'), 'reservation': (self.bases, 'update_one'),
                        'metadata': (self.docs, 'delete_one')}[stage]
                    original = getattr(target, method)
                    arrived, pending = asyncio.Event(), []
                    async def remote(*args, **kwargs):
                        pending.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
                        if mode == 'ack_loss':
                            await original(*args, **kwargs)
                            raise ConnectionFailure('PRIVATE')
                        arrived.set()
                        await asyncio.Event().wait()
                    with patch.object(target, method, side_effect=remote):
                        task = asyncio.create_task(self.bulk([first, later]))
                        if mode == 'cancel':
                            await asyncio.wait_for(arrived.wait(), 3)
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                        else:
                            report = (await task)[1]
                            self.assertEqual(report['results'][1]['outcome'], 'not_attempted')
                    self.assertEqual(len(pending), 1)
                    if stage == 'metadata' and mode == 'ack_loss':
                        self.assertNotIn(ObjectId(first), self.docs.documents)
                    else:
                        self.retained(first)
                    if mode == 'cancel':
                        await original(*pending[0][0], **pending[0][1])
                        if stage != 'metadata':
                            self.retained(first)
                    self.assertEqual(self.docs.documents[ObjectId(later)], before)

    async def test_cancelled_filesystem_worker_can_finish_without_unlock(self):
        first, later = await self.indexed(), await self.indexed()
        arrived, finish, exited = threading.Event(), threading.Event(), threading.Event()
        loop, entered = asyncio.get_running_loop(), asyncio.Event()
        original = self.storage.delete
        def worker(document):
            arrived.set()
            loop.call_soon_threadsafe(entered.set)
            try:
                if not finish.wait(5):
                    raise AssertionError('Worker was not released')
                original(document)
            finally:
                exited.set()
        with patch.object(self.storage, 'delete', side_effect=worker):
            task = asyncio.create_task(self.bulk([first, later]))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(exited.is_set())
                self.retained(first)
                self.assertNotIn('_operation', self.docs.documents[ObjectId(later)])
            finally:
                finish.set()
                self.assertTrue(await asyncio.to_thread(exited.wait, 5))
        self.retained(first)
        self.assertFalse(self.storage.reference(self.docs.documents[ObjectId(first)]).exists())
        self.assertTrue(any(c['document_id'] == ObjectId(first) for c in self.chunks.documents.values()))

    async def test_pre_dispatch_failure_and_exact_token_release(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                first = await self.indexed()
                other_token = ObjectId()
                def invalid(document):
                    if replace:
                        self.docs.documents[ObjectId(first)]['_operation'] = other_token
                    raise OSError('PRIVATE')
                with patch.object(self.storage, 'reference', side_effect=invalid), \
                        patch('documents.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
                    self.assertEqual((await self.bulk([first]))[1]['results'][0]['code'], 'service_unavailable')
                    vectors.assert_not_awaited()
                row = self.docs.documents[ObjectId(first)]
                self.assertEqual('_operation' in row, replace)
                if replace:
                    self.assertEqual(row['_operation'], other_token)
                self.assertEqual(row['vector_index']['status'], 'indexed')

    async def test_pre_dispatch_cancellation_propagates_even_if_release_fails(self):
        for failed_release in (False, True):
            with self.subTest(failed_release=failed_release):
                first, later = await self.indexed(), await self.indexed()
                from document_operations import release_document
                async def release(*args):
                    if failed_release:
                        raise ConnectionFailure('PRIVATE')
                    await release_document(*args)
                with patch('documents.deletion_checkpoint', side_effect=asyncio.CancelledError), \
                        patch('documents.release_document', side_effect=release) as unlock, \
                        patch('documents.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
                    with self.assertRaises(asyncio.CancelledError):
                        await self.bulk([first, later])
                    self.assertEqual(unlock.await_count, 1)
                    vectors.assert_not_awaited()
                self.assertEqual('_operation' in self.docs.documents[ObjectId(first)], failed_release)
                self.assertNotIn('_operation', self.docs.documents[ObjectId(later)])
