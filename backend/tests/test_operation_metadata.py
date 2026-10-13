"""Offline diagnostic metadata CAS and ambiguous-write tests."""
import asyncio
import copy
import unittest
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from test_document_claims import ClaimCollection
from document_operations import (claim_document, release_document, record_operation_stage,
    OperationMetadataFailure, ClaimUncertain, DocumentBusy, OPERATION_TYPES)


class MetadataCollection(ClaimCollection):
    def matching(self, query):
        nested = {k: v for k, v in query.items() if '.' in k}
        rows = super().matching({k: v for k, v in query.items() if '.' not in k})
        def get(row, key):
            for part in key.split('.'):
                if not isinstance(row, dict):
                    return None
                row = row.get(part)
            return row
        return [row for row in rows if all(get(row, k) == v for k, v in nested.items())]

    def apply(self, document, update):
        normal = copy.deepcopy(update)
        for key, value in update.get('$set', {}).items():
            if '.' in key:
                parent, child = key.split('.', 1)
                document[parent][child] = copy.deepcopy(value)
                del normal['$set'][key]
        super().apply(document, normal)


class OperationMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner, self.base, self.identifier = ObjectId(), ObjectId(), ObjectId()
        self.collection = MetadataCollection()
        self.row = {'_id': self.identifier, 'owner_id': self.owner, 'knowledge_base_id': self.base, 'status': 'processed'}
        self.collection.documents[self.identifier] = self.row
        self.db = SimpleNamespace(get_collection=lambda name: self.collection)

    async def acquire(self, **kw):
        return await claim_document(self.db, self.owner, self.identifier, self.base, **kw)

    async def stage(self, token, **kw):
        args = dict(expected_sequence=0, stage='validation', expected_knowledge_base_id=self.base)
        args.update(kw)
        return await record_operation_stage(self.db, self.owner, self.identifier, token, **args)

    async def test_atomic_initialization_types_snapshot_and_release(self):
        for kind in OPERATION_TYPES:
            with self.subTest(kind=kind):
                before = copy.deepcopy(self.row)
                with patch.object(self.collection, 'find_one_and_update', wraps=self.collection.find_one_and_update) as write:
                    document, token = await self.acquire(operation_type=kind)
                self.assertEqual(document, before)
                self.assertEqual(write.await_count, 1)
                self.assertEqual(set(write.call_args.args[1]['$set']), {'_operation', '_operation_meta'})
                meta = self.row['_operation_meta']
                self.assertEqual(meta, dict(schema_version=1, token=token, type=kind,
                    started_at=meta['started_at'], stage='claimed', stage_updated_at=meta['started_at'],
                    stage_sequence=0, last_acknowledged_stage=None, outcome='in_progress', error_category=None))
                self.assertEqual(meta['started_at'].tzinfo, timezone.utc)
                await release_document(self.db, self.owner, self.identifier, token)
                self.assertEqual(self.row, before)
        await self.acquire()
        self.assertEqual(self.row['_operation_meta']['type'], 'unknown')

    async def test_stage_updates_and_all_guards(self):
        _, token = await self.acquire()
        self.assertEqual(await self.stage(token, last_acknowledged_stage='claimed'), 1)
        self.assertEqual(self.row['_operation_meta']['stage_sequence'], 1)
        self.assertEqual(self.row['_operation_meta']['last_acknowledged_stage'], 'claimed')
        self.assertEqual(self.row['_operation_meta']['stage_updated_at'].tzinfo, timezone.utc)
        for changes in ({'_operation': ObjectId()}, {'owner_id': ObjectId()}, {'knowledge_base_id': ObjectId()},
                        {'_operation_meta': None}, {'_operation_meta': {}}, {'_operation_meta': 'PRIVATE'},
                        {'_operation_meta': {'token': token, 'schema_version': 2, 'stage_sequence': 1}},
                        {'_operation_meta': {'token': ObjectId(), 'schema_version': 1, 'stage_sequence': 1}}):
            before = copy.deepcopy(self.row)
            self.row.update(changes)
            protected = copy.deepcopy(self.row)
            with self.assertRaises(OperationMetadataFailure):
                await self.stage(token, expected_sequence=1)
            self.assertEqual(self.row, protected)
            self.row.clear(); self.row.update(before)
        with self.assertRaises(OperationMetadataFailure):
            await self.stage(token)
        with self.assertRaises(OperationMetadataFailure):
            await self.stage(ObjectId(), expected_sequence=1)

    async def test_validation_no_secret_storage_or_dispatch(self):
        for kind in ('PRIVATE', None, [], 1):
            with self.assertRaises(OperationMetadataFailure):
                await self.acquire(operation_type=kind)
        self.assertEqual(self.collection.queries, [])
        _, token = await self.acquire()
        before = copy.deepcopy(self.row)
        for kw in ({'stage': 'PRIVATE'}, {'error_category': 'PRIVATE'}, {'outcome': 'completed'},
                   {'expected_sequence': True}, {'expected_sequence': -1}, {'last_acknowledged_stage': 'PRIVATE'}):
            with self.assertRaises(OperationMetadataFailure) as error:
                await self.stage(token, **kw)
            self.assertNotIn('PRIVATE', str(error.exception))
            self.assertEqual(self.row, before)

    async def test_acquisition_ack_loss_and_metadata_verification(self):
        original = self.collection.find_one_and_update
        for corrupt in (False, True):
            async def lost(query, update):
                await original(query, update)
                if corrupt:
                    self.row['_operation_meta']['token'] = ObjectId()
                raise ConnectionFailure('PRIVATE')
            with patch.object(self.collection, 'find_one_and_update', side_effect=lost) as write:
                if corrupt:
                    with self.assertRaises(ClaimUncertain):
                        await self.acquire()
                else:
                    doc, token = await self.acquire()
                    self.assertNotIn('_operation_meta', doc)
            self.assertEqual(write.await_count, 1)
            self.assertIn('_operation', self.row)
            if not corrupt:
                await release_document(self.db, self.owner, self.identifier, token)

    async def test_metadata_ack_loss_never_replays_or_unlocks(self):
        _, token = await self.acquire()
        original = self.collection.update_one
        async def lost(*args):
            await original(*args)
            raise ConnectionFailure('PRIVATE')
        with patch.object(self.collection, 'update_one', side_effect=lost) as write:
            with self.assertRaises(OperationMetadataFailure) as error:
                await self.stage(token)
        self.assertEqual(write.await_count, 1)
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertEqual(self.row['_operation'], token)
        self.assertEqual(self.row['_operation_meta']['stage_sequence'], 1)

    async def test_delayed_cancelled_metadata_cannot_overwrite_later_claim(self):
        _, token = await self.acquire()
        original = self.collection.update_one
        arrived, pending = asyncio.Event(), []
        async def delayed(*args):
            pending.append(copy.deepcopy(args))
            arrived.set()
            await asyncio.Event().wait()
        with patch.object(self.collection, 'update_one', side_effect=delayed):
            task = asyncio.create_task(self.stage(token))
            await asyncio.wait_for(arrived.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.row['_operation'], token)
        await release_document(self.db, self.owner, self.identifier, token)
        await self.acquire()
        before = copy.deepcopy(self.row)
        await original(*pending[0])
        self.assertEqual(self.row, before)

    async def test_release_cancel_and_lost_ack_are_not_replayed(self):
        for committed in (False, True):
            _, token = await self.acquire()
            original = self.collection.update_one
            async def interrupted(*args):
                if committed: await original(*args)
                raise asyncio.CancelledError()
            with patch.object(self.collection, 'update_one', side_effect=interrupted) as write:
                with self.assertRaises(asyncio.CancelledError):
                    await release_document(self.db, self.owner, self.identifier, token)
            self.assertEqual(write.await_count, 1)
            self.assertEqual('_operation' in self.row, not committed)
            self.assertEqual('_operation_meta' in self.row, not committed)
            if not committed: await release_document(self.db, self.owner, self.identifier, token)
        _, token = await self.acquire()
        async def lost(*args):
            await original(*args)
            raise ConnectionFailure('synthetic')
        with patch.object(self.collection, 'update_one', side_effect=lost) as write:
            with self.assertRaises(ConnectionFailure):
                await release_document(self.db, self.owner, self.identifier, token)
        self.assertEqual(write.await_count, 1)
        self.assertNotIn('_operation_meta', self.row)

    async def test_legacy_and_stale_metadata(self):
        token = ObjectId()
        self.row['_operation'] = token
        with self.assertRaises(DocumentBusy): await self.acquire()
        with self.assertRaises(OperationMetadataFailure): await self.stage(token)
        await release_document(self.db, self.owner, self.identifier, ObjectId())
        self.assertEqual(self.row['_operation'], token)
        await release_document(self.db, self.owner, self.identifier, token)
        self.row['_operation_meta'] = {'token': token, 'schema_version': 999}
        document, fresh = await self.acquire()
        self.assertNotIn('_operation_meta', document)
        self.assertEqual(self.row['_operation_meta']['token'], fresh)
        self.assertEqual(self.row['_operation_meta']['schema_version'], 1)
