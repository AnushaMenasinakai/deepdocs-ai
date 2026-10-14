"""Offline real indexing services with deterministic diagnostic fault injection."""
import asyncio
import copy
import unittest
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_bulk_reindex as fixtures
import document_embeddings
from document_operations import claim_document, release_document


STAGES = ['encoding', 'collection_setup', 'vector_cleanup', 'indexing_metadata',
          'vector_replacement', 'count_verification', 'final_publication']
ACKS = ['validation', 'encoding', 'collection_setup', 'vector_cleanup',
        'indexing_metadata', 'vector_replacement', 'count_verification']


class IndexingMetadataTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkReindexTests.asyncSetUp
    asyncTearDown = fixtures.BulkReindexTests.asyncTearDown
    upload = fixtures.BulkReindexTests.upload
    prepared = fixtures.BulkReindexTests.prepared
    bulk = fixtures.BulkReindexTests.bulk

    async def test_order_guards_atomic_claim_and_completion(self):
        identifier = await self.prepared()
        row = self.docs.documents[ObjectId(identifier)]
        original = self.docs.update_one
        observed = []
        async def update(query, value):
            stage = value.get('$set', {}).get('_operation_meta.stage')
            if stage:
                meta = row['_operation_meta']
                self.assertEqual(meta['type'], 'indexing')
                self.assertEqual(query, {'_id': row['_id'], 'owner_id': self.owner,
                    'knowledge_base_id': self.base_id, '_operation': row['_operation'],
                    '_operation_meta.token': row['_operation'], '_operation_meta.schema_version': 1,
                    '_operation_meta.stage_sequence': len(observed)})
                observed.append((stage, value['$set']['_operation_meta.last_acknowledged_stage']))
                if stage == 'encoding': self.assertEqual(self.model.batches, [])
                if stage == 'collection_setup': self.assertEqual(len(self.model.batches), 1)
                if stage == 'vector_replacement': self.assertEqual(row['vector_index']['status'], 'indexing')
                if stage in ('count_verification', 'final_publication'):
                    self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 3)
            return await original(query, value)
        with patch.object(self.docs, 'find_one_and_update', wraps=self.docs.find_one_and_update) as claim, \
                patch.object(self.docs, 'update_one', side_effect=update):
            self.assertEqual((await self.bulk([identifier]))[1]['succeeded'], 1)
        self.assertEqual(set(claim.call_args.args[1]['$set']), {'_operation', '_operation_meta'})
        self.assertEqual(observed, list(zip(STAGES, ACKS)))
        self.assertNotIn('_operation', row)
        self.assertNotIn('_operation_meta', row)

    async def test_each_checkpoint_failure_stops_and_retains_without_replay(self):
        for target in STAGES:
            for commit in (False, True):
                with self.subTest(target=target, commit=commit):
                    identifier, later = await self.prepared(), await self.prepared()
                    row = self.docs.documents[ObjectId(identifier)]
                    before_later = copy.deepcopy(self.docs.documents[ObjectId(later)])
                    original = self.docs.update_one
                    reached = []
                    async def update(query, value):
                        stage = value.get('$set', {}).get('_operation_meta.stage')
                        if stage:
                            reached.append(stage)
                        if stage == target:
                            if commit: await original(query, value)
                            raise ConnectionFailure('PRIVATE')
                        return await original(query, value)
                    with patch.object(self.docs, 'update_one', side_effect=update), \
                            patch.object(self.vector_store, 'ensure_collection', wraps=self.vector_store.ensure_collection) as setup, \
                            patch('document_embeddings.cleanup_document_vectors', wraps=document_embeddings.cleanup_document_vectors) as cleanup, \
                            patch.object(self.vector_store, 'delete_document', wraps=self.vector_store.delete_document) as delete, \
                            patch.object(self.vector_store, 'upsert', wraps=self.vector_store.upsert) as upsert:
                        report = (await self.bulk([identifier, later]))[1]
                    position = STAGES.index(target)
                    self.assertEqual(setup.await_count, int(position > 1))
                    self.assertEqual(cleanup.await_count, int(position > 2))
                    self.assertEqual(delete.await_count, int(position > 4))
                    self.assertEqual(upsert.await_count, 2 if position > 4 else 0)
                    self.assertEqual(report['results'][0]['code'], 'service_unavailable')
                    self.assertEqual(report['results'][1]['outcome'], 'not_attempted')
                    self.assertNotIn('PRIVATE', str(report))
                    self.assertEqual(reached, STAGES[:STAGES.index(target)+1])
                    self.assertEqual(row['_operation_meta']['stage_sequence'], STAGES.index(target)+int(commit))
                    self.assertEqual(row['embedding']['status'], 'generating')
                    self.assertIn('_operation', row)
                    self.assertEqual(self.docs.documents[ObjectId(later)], before_later)
                    self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'busy')

    async def test_repeated_cancellation_and_delayed_update_cannot_change_new_claim(self):
        for stage in ('encoding', 'count_verification'):
            with self.subTest(stage=stage):
                identifier, later = await self.prepared(), await self.prepared()
                row = self.docs.documents[ObjectId(identifier)]
                later_before = copy.deepcopy(self.docs.documents[ObjectId(later)])
                original = self.docs.update_one
                arrived, cancelled = asyncio.Event(), asyncio.Event()
                pending = []
                batches_before = len(self.model.batches)
                async def delayed(query, value):
                    if value.get('$set', {}).get('_operation_meta.stage') == stage:
                        pending.append(copy.deepcopy((query, value)))
                        arrived.set()
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            cancelled.set()
                            await asyncio.Event().wait()  # adapter pending until second cancellation
                    return await original(query, value)
                with patch.object(self.docs, 'update_one', side_effect=delayed) as writes, \
                        patch('document_embeddings.release_document', wraps=release_document) as release, \
                        patch.object(self.vector_store, 'delete_document', wraps=self.vector_store.delete_document) as delete, \
                        patch.object(self.vector_store, 'upsert', wraps=self.vector_store.upsert) as upsert, \
                        patch('document_bulk.document_embeddings.generate_embeddings', wraps=document_embeddings.generate_embeddings) as generate:
                    task = asyncio.create_task(self.bulk([identifier, later]))
                    await asyncio.wait_for(arrived.wait(), 3)
                    boundary = copy.deepcopy(row)
                    points = copy.deepcopy(self.qdrant.points)
                    calls = (writes.await_count, delete.await_count, upsert.await_count)
                    if stage == 'count_verification':
                        self.assertEqual((delete.await_count, upsert.await_count), (1, 2))
                        self.assertEqual(sum(p.payload['document_id'] == identifier for p in
                                             self.qdrant.points[self.vector_store.collection].values()), 3)
                    task.cancel()
                    await asyncio.wait_for(cancelled.wait(), 3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    self.assertEqual((writes.await_count, delete.await_count, upsert.await_count), calls)
                    release.assert_not_awaited()
                    self.assertEqual(generate.await_count, 1)
                self.assertEqual(len(pending), 1)
                self.assertEqual(row, boundary)  # no success/failure publication or unlock
                self.assertEqual(row['_operation_meta']['token'], row['_operation'])
                self.assertEqual(row['_operation_meta']['type'], 'indexing')
                self.assertEqual(row['_operation_meta']['stage_sequence'], STAGES.index(stage))
                self.assertEqual(row['embedding']['status'], 'generating')
                self.assertEqual(row['vector_index']['status'], 'indexing')
                self.assertEqual(self.qdrant.points, points)
                self.assertEqual(len(self.model.batches)-batches_before, 0 if stage == 'encoding' else 2)
                self.assertEqual(self.docs.documents[ObjectId(later)], later_before)
                # Artificial claim replacement tests CAS only; never a recovery path.
                await release_document(self.database, self.owner, row['_id'], row['_operation'])
                await claim_document(self.database, self.owner, row['_id'])
                before = copy.deepcopy(row)
                await original(*pending[0])
                self.assertEqual(row, before)

    async def test_local_failure_cleans_both_and_legacy_claim_protected(self):
        identifier = await self.prepared()
        row = self.docs.documents[ObjectId(identifier)]
        with patch('embeddings.load_model', side_effect=RuntimeError('PRIVATE')):
            self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'service_unavailable')
        self.assertEqual(row['embedding']['status'], 'failed')
        self.assertNotIn('_operation', row)
        self.assertNotIn('_operation_meta', row)
        row['_operation'] = ObjectId()
        before = copy.deepcopy(row)
        self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'busy')
        self.assertEqual(row, before)

    async def test_final_publication_rejects_changed_scope_token_or_generation(self):
        for field in ('owner_id', 'knowledge_base_id', '_operation', 'chunk_generation'):
            with self.subTest(field=field):
                identifier = await self.prepared()
                row = self.docs.documents[ObjectId(identifier)]
                original = self.docs.update_one
                async def update(query, value):
                    if value.get('$set', {}).get('embedding', {}).get('status') == 'generated':
                        row[field] = ObjectId()
                    return await original(query, value)
                with patch.object(self.docs, 'update_one', side_effect=update):
                    self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'service_unavailable')
                self.assertEqual(row['embedding']['status'], 'generating')
                self.assertIn('_operation', row)
                self.assertIn('_operation_meta', row)

    async def test_remote_uncertainty_does_not_advance_acknowledged_stage(self):
        for method, stage, acknowledged in (
                ('delete_document', 'vector_replacement', 'indexing_metadata'),
                ('upsert', 'vector_replacement', 'indexing_metadata'),
                ('count', 'count_verification', 'vector_replacement')):
            with self.subTest(method=method):
                identifier = await self.prepared()
                row = self.docs.documents[ObjectId(identifier)]
                original = getattr(self.vector_store, method)
                async def lost(*args, **kwargs):
                    if method == 'count' and len(args) < 2:
                        return await original(*args, **kwargs)  # deletion's count is earlier
                    await original(*args, **kwargs)
                    raise TimeoutError('PRIVATE')
                with patch.object(self.vector_store, method, side_effect=lost):
                    self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'service_unavailable')
                self.assertIn('_operation', row)
                self.assertEqual(row['_operation_meta']['stage'], stage)
                self.assertEqual(row['_operation_meta']['last_acknowledged_stage'], acknowledged)
                self.assertEqual(row['embedding']['status'], 'generating')
