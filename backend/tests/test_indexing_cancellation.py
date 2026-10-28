"""Offline indexing dispatch/commit/ack fault injection, with no live services."""
import asyncio
import copy
import unittest
from unittest.mock import patch, AsyncMock
import test_bulk_reindex as fixtures
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from document_inspection import classify


class IndexingCancellationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BulkReindexTests.asyncSetUp
    asyncTearDown = fixtures.BulkReindexTests.asyncTearDown
    upload = fixtures.BulkReindexTests.upload
    prepared = fixtures.BulkReindexTests.prepared
    bulk = fixtures.BulkReindexTests.bulk

    def retained(self, identifier):
        row = self.docs.documents[ObjectId(identifier)]
        self.assertIn("_operation", row)
        self.assertEqual(row["embedding"]["status"], "generating")
        self.assertEqual(row["vector_index"]["status"], "indexing")
        result = classify(row["_id"], {"status": row["status"], "claimed": True, "indexed": False})
        self.assertEqual(result.attention, "outcome_uncertain")
        return row

    async def test_remote_mutations_ack_loss_timeout_and_delayed_completion(self):
        for method in ("delete", "upsert"):
            for mode in ("ack_loss", "timeout", "cancel"):
                with self.subTest(method=method, mode=mode):
                    identifier, later = await self.prepared(), await self.prepared()
                    before_later = copy.deepcopy(self.docs.documents[ObjectId(later)])
                    original = getattr(self.qdrant, method)
                    pending = []
                    arrived = asyncio.Event()
                    async def remote(*args, **kwargs):
                        pending.append((args, kwargs))
                        if mode == "ack_loss":
                            await original(*args, **kwargs)
                        arrived.set()
                        if mode == "cancel":
                            await asyncio.Event().wait()
                        raise TimeoutError("PRIVATE transport")
                    with patch.object(self.qdrant, method, side_effect=remote) as called:
                        task = asyncio.create_task(self.bulk([identifier, later]))
                        await asyncio.wait_for(arrived.wait(), 3)
                        if mode == "cancel":
                            task.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                        else:
                            status, report, _ = await task
                            self.assertEqual(status, 200)
                            self.assertEqual(report["results"][0]["code"], "service_unavailable")
                            self.assertEqual(report["results"][1]["outcome"], "not_attempted")
                            self.assertNotIn("PRIVATE", str(report))
                        self.assertEqual(called.await_count, 1)
                    token = self.retained(identifier)["_operation"]
                    if mode != "ack_loss":
                        await original(*pending[0][0], **pending[0][1])
                    self.assertEqual(self.retained(identifier)["_operation"], token)
                    self.assertEqual(self.docs.documents[ObjectId(later)], before_later)
                    self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "busy")

    async def test_encoding_checkpoint_cancels_before_qdrant_mutation(self):
        identifier, later = await self.prepared(), await self.prepared()
        encode = self.model.encode
        loop = asyncio.get_running_loop()
        def cancel(*args, **kwargs):
            result = encode(*args, **kwargs)
            # Encoding is worker-owned; cancel the requesting task on its loop
            # before the worker reports completion to that same loop.
            loop.call_soon_threadsafe(task.cancel)
            return result
        with patch.object(self.model, "encode", side_effect=cancel), \
                patch.object(self.vector_store, "ensure_collection", new_callable=AsyncMock) as setup:
            task = asyncio.create_task(self.bulk([identifier, later]))
            with self.assertRaises(asyncio.CancelledError):
                await task
            setup.assert_not_awaited()
        self.retained(identifier)
        self.assertNotIn("_operation", self.docs.documents[ObjectId(later)])

    async def test_initial_metadata_uncertainty_stops_encoding(self):
        identifier = await self.prepared()
        original = self.docs.update_one
        async def update(query, value):
            await original(query, value)
            raise ConnectionFailure("PRIVATE")
        with patch.object(self.docs, "update_one", side_effect=update) as writes:
            report = (await self.bulk([identifier]))[1]
        self.assertEqual(report["results"][0]["code"], "service_unavailable")
        self.assertEqual(writes.await_count, 1)
        self.assertEqual(self.model.batches, [])
        self.retained(identifier)

    async def test_final_publication_scoped_atomic_and_never_compensated(self):
        for commit in (False, True):
            for cancel in (False, True):
                with self.subTest(commit=commit, cancel=cancel):
                    identifier = await self.prepared()
                    original = self.docs.update_one
                    final = []
                    async def publish(query, value):
                        if value.get("$set", {}).get("embedding", {}).get("status") == "generated":
                            final.append((copy.deepcopy(query), copy.deepcopy(value)))
                            if commit:
                                await original(query, value)
                            if cancel:
                                raise asyncio.CancelledError()
                            raise ConnectionFailure("PRIVATE")
                        return await original(query, value)
                    with patch.object(self.docs, "update_one", side_effect=publish) as writes:
                        if cancel:
                            with self.assertRaises(asyncio.CancelledError):
                                await self.bulk([identifier])
                        else:
                            self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "service_unavailable")
                    self.assertEqual(len(final), 1)
                    query, value = final[0]
                    self.assertEqual(set(query), {"_id", "owner_id", "knowledge_base_id", "_operation", "chunk_generation"})
                    self.assertEqual(query["owner_id"], self.owner)
                    self.assertEqual(query["knowledge_base_id"], self.base_id)
                    self.assertEqual(value["$unset"], {"_operation": "", "_operation_meta": ""})
                    self.assertEqual(writes.await_count, 10)  # three business writes + seven diagnostics; no compensation
                    row = self.docs.documents[ObjectId(identifier)]
                    self.assertEqual(row["vector_index"]["status"], "indexed" if commit else "indexing")
                    self.assertEqual("_operation" in row, not commit)

    async def test_count_unavailable_preserves_vectors_and_claim(self):
        identifier = await self.prepared()
        original = self.vector_store.count
        async def count(document, generation=None, **kwargs):
            if generation is not None:
                raise TimeoutError("PRIVATE")
            return await original(document, generation, **kwargs)
        with patch.object(self.vector_store, "count", side_effect=count):
            self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "service_unavailable")
        self.retained(identifier)
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 3)
