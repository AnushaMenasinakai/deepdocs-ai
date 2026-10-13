"""Real deletion orchestration with offline checkpoint fault injection."""
import asyncio
import copy
import unittest
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_document_bulk as fixtures
from document_operations import claim_document, release_document

STAGES = ['vector_invalidation', 'vector_deletion', 'pdf_deletion',
          'chunk_deletion', 'reservation_release', 'metadata_deletion']


class DeletionMetadataTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkDeleteTests.asyncSetUp
    asyncTearDown = fixtures.BulkDeleteTests.asyncTearDown
    upload = fixtures.BulkDeleteTests.upload
    indexed = fixtures.BulkDeleteTests.indexed
    bulk = fixtures.BulkDeleteTests.bulk

    async def test_order_scope_and_success_with_and_without_vectors(self):
        for indexed in (False, True):
            identifier = await self.indexed() if indexed else (await self.upload())['id']
            row = self.docs.documents[ObjectId(identifier)]
            original = self.docs.update_one
            seen = []
            stages = STAGES if indexed else STAGES[2:]
            async def update(query, value):
                stage = value.get('$set', {}).get('_operation_meta.stage')
                if stage:
                    self.assertEqual(row['_operation_meta']['type'], 'deletion')
                    self.assertEqual(query, {'_id': row['_id'], 'owner_id': self.owner,
                        'knowledge_base_id': self.base_id, '_operation': row['_operation'],
                        '_operation_meta.token': row['_operation'], '_operation_meta.schema_version': 1,
                        '_operation_meta.stage_sequence': len(seen)})
                    self.assertEqual(value['$set']['_operation_meta.last_acknowledged_stage'], seen[-1] if seen else 'claimed')
                    if stage == 'chunk_deletion': self.assertFalse(self.storage.reference(row).exists())
                    if stage == 'reservation_release':
                        self.assertFalse(any(c['document_id'] == row['_id'] for c in self.chunks.documents.values()))
                    if stage == 'metadata_deletion': self.assertNotIn(row['_id'], self.bases.documents[self.base_id]['_document_ids'])
                    seen.append(stage)
                return await original(query, value)
            with patch.object(self.docs, 'update_one', side_effect=update), \
                    patch.object(self.docs, 'find_one_and_update', wraps=self.docs.find_one_and_update) as claim:
                self.assertEqual((await self.bulk([identifier]))[1]['succeeded'], 1)
            self.assertEqual(set(claim.call_args.args[1]['$set']), {'_operation', '_operation_meta'})
            self.assertEqual(seen, stages)
            self.assertNotIn(ObjectId(identifier), self.docs.documents)

    async def test_all_checkpoint_failures_retain_and_stop_bulk(self):
        for stage in STAGES:
            for commit in (False, True):
                with self.subTest(stage=stage, commit=commit):
                    identifier, later = await self.indexed(), await self.indexed()
                    later_before = copy.deepcopy(self.docs.documents[ObjectId(later)])
                    original = self.docs.update_one
                    seen = []
                    async def update(query, value):
                        current = value.get('$set', {}).get('_operation_meta.stage')
                        if current: seen.append(current)
                        if current == stage:
                            if commit: await original(query, value)
                            raise ConnectionFailure('PRIVATE')
                        return await original(query, value)
                    with patch.object(self.docs, 'update_one', side_effect=update):
                        report = (await self.bulk([identifier, later]))[1]
                    self.assertEqual([r.get('code', r['outcome']) for r in report['results']], ['service_unavailable', 'not_attempted'])
                    self.assertNotIn('PRIVATE', str(report))
                    self.assertEqual(seen, STAGES[:STAGES.index(stage)+1])
                    row = self.docs.documents[ObjectId(identifier)]
                    self.assertIn('_operation', row)
                    self.assertEqual(row['_operation_meta']['stage_sequence'], STAGES.index(stage)+int(commit))
                    self.assertEqual(self.docs.documents[ObjectId(later)], later_before)
                    if STAGES.index(stage) <= 2: self.assertTrue(self.storage.reference(row).exists())
                    if STAGES.index(stage) <= 3: self.assertTrue(any(c['document_id'] == row['_id'] for c in self.chunks.documents.values()))
                    if STAGES.index(stage) <= 4: self.assertIn(row['_id'], self.bases.documents[self.base_id]['_document_ids'])
                    self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'busy')

    async def test_repeated_cancellation_and_delayed_diagnostic_isolation(self):
        for stage in ('vector_invalidation', 'chunk_deletion'):
            identifier, later = await self.indexed(), await self.indexed()
            original = self.docs.update_one
            arrived, cancelled, pending = asyncio.Event(), asyncio.Event(), []
            async def update(query, value):
                if value.get('$set', {}).get('_operation_meta.stage') == stage:
                    pending.append(copy.deepcopy((query, value)))
                    arrived.set()
                    try: await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        await asyncio.Event().wait()
                return await original(query, value)
            with patch.object(self.docs, 'update_one', side_effect=update):
                task = asyncio.create_task(self.bulk([identifier, later]))
                await asyncio.wait_for(arrived.wait(), 3)
                task.cancel()
                await asyncio.wait_for(cancelled.wait(), 3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            row = self.docs.documents[ObjectId(identifier)]
            self.assertIn('_operation', row)
            self.assertNotIn('_operation', self.docs.documents[ObjectId(later)])
            # Artificial replacement solely to exercise the delayed CAS guard.
            await release_document(self.database, self.owner, row['_id'], row['_operation'])
            await claim_document(self.database, self.owner, row['_id'])
            before = copy.deepcopy(row)
            await original(*pending[0])
            self.assertEqual(row, before)

    async def test_final_deletion_rejects_changed_scope_or_token(self):
        for field in ('owner_id', 'knowledge_base_id', '_operation'):
            with self.subTest(field=field):
                identifier = await self.indexed()
                row = self.docs.documents[ObjectId(identifier)]
                original = self.docs.delete_one
                async def delete(query):
                    self.assertEqual(set(query), {'_id', 'owner_id', 'knowledge_base_id', '_operation'})
                    row[field] = ObjectId()
                    return await original(query)
                with patch.object(self.docs, 'delete_one', side_effect=delete) as deletion:
                    self.assertEqual((await self.bulk([identifier]))[1]['results'][0]['code'], 'service_unavailable')
                self.assertEqual(deletion.await_count, 1)
                self.assertIn(ObjectId(identifier), self.docs.documents)
                self.assertIn('_operation_meta', row)
