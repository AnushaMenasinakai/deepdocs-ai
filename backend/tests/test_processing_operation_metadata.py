"""Real processing/claim services with offline, controlled diagnostic failures."""
import asyncio
import copy
import unittest
from unittest.mock import patch, AsyncMock
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_documents as fixtures
import test_processing as processing
from document_operations import release_document, claim_document


class ProcessingMetadataTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown
    make_document = processing.ProcessingTests.make_document
    process = processing.ProcessingTests.process

    async def test_order_sequence_and_completed_evidence(self):
        body = await self.make_document()
        identifier = ObjectId(body['id'])
        original = self.docs.update_one
        observed = []
        async def update(query, value):
            stage = value.get('$set', {}).get('_operation_meta.stage')
            if stage:
                meta = copy.deepcopy(self.docs.documents[identifier]['_operation_meta'])
                self.assertEqual(meta['type'], 'processing')
                self.assertEqual(meta['token'], query['_operation'])
                self.assertEqual(query['knowledge_base_id'], self.base_id)
                self.assertEqual(query['_operation_meta.stage_sequence'], len(observed))
                observed.append((stage, value['$set']['_operation_meta.last_acknowledged_stage']))
                if stage == 'chunk_insertion':
                    self.assertFalse(self.chunks.documents)
                if stage == 'vector_cleanup':
                    self.assertTrue(self.chunks.documents)
                if stage == 'obsolete_cleanup':
                    self.assertEqual(self.docs.documents[identifier]['status'], 'processed')
            return await original(query, value)
        with patch.object(self.docs, 'update_one', side_effect=update):
            self.assertEqual((await self.process(body))[0], 200)
        self.assertEqual(observed, [('extraction', 'claimed'), ('chunk_insertion', 'extraction'),
            ('vector_cleanup', 'chunk_insertion'), ('activation', 'vector_cleanup'), ('obsolete_cleanup', 'activation')])
        self.assertNotIn('_operation', self.docs.documents[identifier])
        self.assertNotIn('_operation_meta', self.docs.documents[identifier])

    async def test_required_checkpoint_failure_stops_next_mutation(self):
        for target in ('extraction', 'chunk_insertion', 'vector_cleanup', 'activation', 'obsolete_cleanup'):
            for commit in (False, True):
                with self.subTest(target=target, commit=commit):
                    body = await self.make_document()
                    identifier = ObjectId(body['id'])
                    original = self.docs.update_one
                    calls = []
                    async def update(query, value):
                        stage = value.get('$set', {}).get('_operation_meta.stage')
                        if stage == target:
                            calls.append(stage)
                            if commit: await original(query, value)
                            raise ConnectionFailure('PRIVATE')
                        return await original(query, value)
                    with patch.object(self.docs, 'update_one', side_effect=update), \
                            patch('document_processing.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
                        status, response, _ = await self.process(body)
                        self.assertEqual(status, 503)
                        self.assertNotIn('PRIVATE', str(response))
                        self.assertEqual(vectors.await_count, int(target in ('activation', 'obsolete_cleanup')))
                    self.assertEqual(calls, [target])
                    row = self.docs.documents[identifier]
                    self.assertIn('_operation', row)
                    self.assertNotEqual(row['status'], 'failed')
                    if target in ('extraction', 'chunk_insertion'):
                        self.assertFalse(any(c['document_id'] == identifier for c in self.chunks.documents.values()))
                    if target != 'obsolete_cleanup':
                        self.assertNotIn('chunk_generation', row)
                    self.assertEqual((await self.process(body))[0], 409)

    async def test_cancelled_delayed_stage_cannot_change_new_claim(self):
        body = await self.make_document()
        identifier = ObjectId(body['id'])
        original = self.docs.update_one
        arrived, pending = asyncio.Event(), []
        async def update(query, value):
            if value.get('$set', {}).get('_operation_meta.stage') == 'chunk_insertion':
                pending.append(copy.deepcopy((query, value)))
                arrived.set()
                await asyncio.Event().wait()
            return await original(query, value)
        with patch.object(self.docs, 'update_one', side_effect=update):
            task = asyncio.create_task(self.process(body))
            await asyncio.wait_for(arrived.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        row = self.docs.documents[identifier]
        self.assertIn('_operation', row)
        self.assertFalse(self.chunks.documents)
        self.assertEqual((await self.process(body))[0], 409)
        # Artificial later owner to exercise CAS only; not a recovery workflow.
        await release_document(self.database, self.owner, identifier, row['_operation'])
        await claim_document(self.database, self.owner, identifier)
        before = copy.deepcopy(row)
        await original(*pending[0])
        self.assertEqual(row, before)

    async def test_local_failure_releases_and_legacy_claim_is_untouched(self):
        body = await self.make_document()
        identifier = ObjectId(body['id'])
        with patch('document_processing.extract_chunks', side_effect=ValueError('PRIVATE')):
            self.assertEqual((await self.process(body))[0], 503)
        row = self.docs.documents[identifier]
        self.assertEqual(row['status'], 'failed')
        self.assertNotIn('_operation', row)
        self.assertNotIn('_operation_meta', row)
        row['_operation'] = ObjectId()
        before = copy.deepcopy(row)
        self.assertEqual((await self.process(body))[0], 409)
        self.assertEqual(row, before)

    async def test_remote_uncertainty_does_not_record_acknowledgement(self):
        for target in ('chunk_insertion', 'vector_cleanup', 'activation'):
            with self.subTest(target=target):
                body = await self.make_document()
                identifier = ObjectId(body['id'])
                original_insert, original_update = self.chunks.insert_many, self.docs.update_one
                async def insert(*args, **kw):
                    await original_insert(*args, **kw)
                    raise ConnectionFailure('PRIVATE')
                async def activate(query, value):
                    result = await original_update(query, value)
                    if value.get('$set', {}).get('status') == 'processed':
                        raise ConnectionFailure('PRIVATE')
                    return result
                if target == 'chunk_insertion':
                    fault = patch.object(self.chunks, 'insert_many', side_effect=insert)
                elif target == 'vector_cleanup':
                    fault = patch('document_processing.cleanup_document_vectors', side_effect=ConnectionFailure('PRIVATE'))
                else:
                    fault = patch.object(self.docs, 'update_one', side_effect=activate)
                with fault:
                    self.assertEqual((await self.process(body))[0], 503)
                row = self.docs.documents[identifier]
                self.assertIn('_operation', row)
                meta = row['_operation_meta']
                self.assertEqual(meta['stage'], target)
                self.assertEqual(meta['last_acknowledged_stage'], {
                    'chunk_insertion': 'extraction', 'vector_cleanup': 'chunk_insertion',
                    'activation': 'vector_cleanup'}[target])
                self.assertEqual((await self.process(body))[0], 409)
