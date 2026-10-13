"""Vector-status diagnostics do not repair business state or mutate vectors."""
import asyncio
import copy
import unittest
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_document_bulk as fixtures
from test_documents import request


class VectorInspectionMetadataTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkDeleteTests.asyncSetUp
    asyncTearDown = fixtures.BulkDeleteTests.asyncTearDown
    upload = fixtures.BulkDeleteTests.upload
    indexed = fixtures.BulkDeleteTests.indexed

    async def inspect(self, identifier):
        return await request('GET', f'/api/documents/{identifier}/vector-status')

    async def test_single_checkpoint_atomic_release_and_no_business_changes(self):
        identifier = await self.indexed()
        row = self.docs.documents[ObjectId(identifier)]
        before, points = copy.deepcopy(row), copy.deepcopy(self.qdrant.points)
        original = self.docs.update_one
        writes = []
        async def update(query, value):
            writes.append(copy.deepcopy((query, value)))
            self.assertEqual(row['_operation_meta']['type'], 'vector_inspection')
            return await original(query, value)
        with patch.object(self.docs, 'update_one', side_effect=update), \
                patch.object(self.docs, 'find_one_and_update', wraps=self.docs.find_one_and_update) as claim, \
                patch.object(self.vector_store, 'count', wraps=self.vector_store.count) as count:
            self.assertEqual((await self.inspect(identifier))[0], 200)
        self.assertEqual(count.await_count, 2)
        self.assertEqual(set(claim.call_args.args[1]['$set']), {'_operation', '_operation_meta'})
        self.assertEqual(len(writes), 2)
        query, update = writes[0]
        self.assertEqual(query['knowledge_base_id'], self.base_id)
        self.assertEqual(query['owner_id'], self.owner)
        self.assertEqual(query['_operation'], query['_operation_meta.token'])
        self.assertEqual(query['_operation_meta.schema_version'], 1)
        self.assertEqual(query['_operation_meta.stage_sequence'], 0)
        self.assertEqual(update['$set']['_operation_meta.stage'], 'inspection')
        self.assertEqual(update['$set']['_operation_meta.last_acknowledged_stage'], 'claimed')
        self.assertEqual(writes[1][1], {'$unset': {'_operation': '', '_operation_meta': ''}})
        self.assertEqual(row, before)
        self.assertEqual(self.qdrant.points, points)

    async def test_release_ack_loss_or_cancellation_is_not_replayed(self):
        for cancel in (False, True):
            identifier = await self.indexed()
            row = self.docs.documents[ObjectId(identifier)]
            before = copy.deepcopy(row)
            original = self.docs.update_one
            releases = []
            async def update(query, value):
                result = await original(query, value)
                if '$unset' in value:
                    releases.append(query)
                    if cancel: raise asyncio.CancelledError()
                    raise ConnectionFailure('PRIVATE')
                return result
            with patch.object(self.docs, 'update_one', side_effect=update):
                if cancel:
                    with self.assertRaises(asyncio.CancelledError): await self.inspect(identifier)
                else:
                    self.assertEqual((await self.inspect(identifier))[0], 503)
            self.assertEqual(len(releases), 1)
            self.assertEqual(row, before)

    async def test_diagnostic_ack_loss_stops_reads_and_retains(self):
        for commit in (False, True):
            identifier = await self.indexed()
            original = self.docs.update_one
            async def update(query, value):
                if commit: await original(query, value)
                raise ConnectionFailure('PRIVATE')
            with patch.object(self.docs, 'update_one', side_effect=update) as writes, \
                    patch.object(self.vector_store, 'count', wraps=self.vector_store.count) as count:
                response = await self.inspect(identifier)
            self.assertEqual(response[0], 503)
            self.assertNotIn('PRIVATE', str(response))
            self.assertEqual(writes.await_count, 1)
            count.assert_not_awaited()
            self.assertIn('_operation', self.docs.documents[ObjectId(identifier)])
            self.assertEqual((await self.inspect(identifier))[0], 409)

    async def test_cancellation_during_diagnostic_or_count_retains(self):
        for stage in ('diagnostic', 'count'):
            identifier = await self.indexed()
            arrived, cancelled = asyncio.Event(), asyncio.Event()
            async def wait(*args, **kwargs):
                arrived.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await asyncio.Event().wait()
            target, method = (self.docs, 'update_one') if stage == 'diagnostic' else (self.vector_store, 'count')
            with patch.object(target, method, side_effect=wait):
                task = asyncio.create_task(self.inspect(identifier))
                await asyncio.wait_for(arrived.wait(), 3)
                task.cancel()
                await asyncio.wait_for(cancelled.wait(), 3)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
            self.assertIn('_operation', self.docs.documents[ObjectId(identifier)])
            self.assertEqual((await self.inspect(identifier))[0], 409)

    async def test_timeout_retained_and_legacy_busy(self):
        identifier = await self.indexed()
        with patch.object(self.vector_store, 'count', side_effect=TimeoutError('PRIVATE')):
            self.assertEqual((await self.inspect(identifier))[0], 503)
        row = self.docs.documents[ObjectId(identifier)]
        self.assertIn('_operation', row)
        legacy = (await self.upload())['id']
        row = self.docs.documents[ObjectId(legacy)]
        row['_operation'] = ObjectId()
        before = copy.deepcopy(row)
        self.assertEqual((await self.inspect(legacy))[0], 409)
        self.assertEqual(row, before)
