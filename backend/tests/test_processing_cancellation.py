"""Controlled, offline delayed commands; never a live MongoDB/Qdrant mutation."""
import asyncio
import copy
import unittest
from unittest.mock import patch, AsyncMock
import test_documents as fixtures
import test_processing as processing
from test_documents import request
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from document_inspection import classify


class ProcessingCancellationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown
    make_document = processing.ProcessingTests.make_document
    process = processing.ProcessingTests.process

    async def prepared(self):
        body = await self.make_document()
        self.assertEqual((await self.process(body))[0], 200)
        return body

    def retained(self, body):
        row = self.docs.documents[ObjectId(body["id"])]
        self.assertIn("_operation", row)
        status = classify(row["_id"], {"status": row["status"], "claimed": True, "indexed": False})
        self.assertEqual(status.operation_state, "claimed_unknown")
        self.assertEqual(status.attention, "outcome_uncertain")
        return row

    async def test_local_cancellation_checkpoint_stops_insertion(self):
        body = await self.make_document()
        from cpu_workers import ExecutionSession
        original = ExecutionSession.run_pdf
        async def cancel(*args):
            result = await original(*args)
            asyncio.current_task().cancel()
            return result
        with patch("cpu_workers.ExecutionSession.run_pdf", new=cancel), \
                patch.object(self.chunks, "insert_many", new_callable=AsyncMock) as insert:
            task = asyncio.create_task(self.process(body))
            with self.assertRaises(asyncio.CancelledError):
                await task
        insert.assert_not_awaited()
        self.assertEqual(self.retained(body)["status"], "processing")

    async def test_cancelled_insert_can_complete_later_without_compensation(self):
        body = await self.prepared()
        arrived = asyncio.Event()
        pending = []
        original = self.chunks.insert_many
        async def insert(values, **kwargs):
            pending.extend(copy.deepcopy(values))
            arrived.set()
            await asyncio.Event().wait()
        with patch.object(self.chunks, "insert_many", side_effect=insert), \
                patch.object(self.chunks, "delete_many", new_callable=AsyncMock) as delete, \
                patch("document_processing.cleanup_document_vectors", new_callable=AsyncMock) as vectors:
            task = asyncio.create_task(self.process(body))
            await asyncio.wait_for(arrived.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            delete.assert_not_awaited()
            vectors.assert_not_awaited()
        await original(pending)
        self.retained(body)
        self.assertEqual((await self.process(body))[0], 409)

    async def test_activation_commit_lost_ack_or_cancel_preserves_active_data(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                body = await self.prepared()
                original = self.docs.update_one
                async def activate(query, update):
                    result = await original(query, update)
                    if update.get("$set", {}).get("status") == "processed":
                        if cancelled:
                            raise asyncio.CancelledError()
                        raise ConnectionFailure("private")
                    return result
                with patch.object(self.docs, "update_one", side_effect=activate), \
                        patch.object(self.chunks, "delete_many", new_callable=AsyncMock) as delete:
                    if cancelled:
                        with self.assertRaises(asyncio.CancelledError):
                            await self.process(body)
                    else:
                        self.assertEqual((await self.process(body))[0], 503)
                    delete.assert_not_awaited()
                row = self.retained(body)
                self.assertEqual(row["status"], "processed")
                self.assertTrue(any(c["generation"] == row["chunk_generation"] for c in self.chunks.documents.values()))

    async def test_fixed_cleanup_delayed_timeout_and_cancel_protect_future_and_foreign(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                body = await self.prepared()
                identifier = ObjectId(body["id"])
                previous = self.docs.documents[identifier]["chunk_generation"]
                template = copy.deepcopy(next(c for c in self.chunks.documents.values() if c["document_id"] == identifier))
                pending = []
                arrived = asyncio.Event()
                original = self.chunks.delete_many
                async def delayed(query):
                    pending.append(copy.deepcopy(query))
                    arrived.set()
                    if cancelled:
                        await asyncio.Event().wait()
                    raise TimeoutError("private")
                with patch.object(self.chunks, "delete_many", side_effect=delayed):
                    task = asyncio.create_task(self.process(body))
                    await asyncio.wait_for(arrived.wait(), 2)
                    if cancelled:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        self.assertEqual((await task)[0], 503)
                self.assertEqual(pending, [{"owner_id": self.owner, "knowledge_base_id": self.base_id,
                                           "document_id": identifier, "generation": previous}])
                survivors = []
                for changes in ({"generation": ObjectId()}, {"owner_id": self.other},
                                {"knowledge_base_id": ObjectId()}, {"document_id": ObjectId()}):
                    row = {**template, "_id": ObjectId(), **changes}
                    await self.chunks.insert_one(row)
                    survivors.append(row["_id"])
                await original(pending[0])  # Outstanding command finally completes.
                self.assertTrue(all(i in self.chunks.documents for i in survivors))
                self.retained(body)

    async def test_vector_cleanup_uncertainty_retains_claim_and_stops_activation(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                body = await self.prepared()
                old = self.docs.documents[ObjectId(body["id"])]["chunk_generation"]
                arrived = asyncio.Event()
                completed = []
                async def vectors(document):
                    arrived.set()
                    if cancelled:
                        await asyncio.Event().wait()
                    completed.append(document["_id"])
                    raise ConnectionFailure("ack lost")
                with patch("document_processing.cleanup_document_vectors", side_effect=vectors):
                    task = asyncio.create_task(self.process(body))
                    await asyncio.wait_for(arrived.wait(), 2)
                    if cancelled:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                        completed.append(ObjectId(body["id"]))  # delayed remote delete
                    else:
                        self.assertEqual((await task)[0], 503)
                self.assertEqual(self.retained(body)["chunk_generation"], old)
                self.assertEqual((await self.process(body))[0], 409)

    async def test_release_ack_loss_never_retries_or_marks_failed(self):
        body = await self.make_document()
        original = self.docs.update_one
        releases = []
        async def update(query, value):
            result = await original(query, value)
            if "$unset" in value:
                releases.append(query)
                raise ConnectionFailure("private")
            return result
        with patch.object(self.docs, "update_one", side_effect=update):
            self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual(len(releases), 1)
        row = self.docs.documents[ObjectId(body["id"])]
        self.assertEqual(row["status"], "processed")
        self.assertNotIn("_operation", row)

    async def test_cancel_during_metadata_writes_never_compensates(self):
        for stage in ("initialization", "activation", "release"):
            with self.subTest(stage=stage):
                body = await self.make_document()
                arrived = asyncio.Event()
                original = self.docs.update_one
                async def update(query, value):
                    status = value.get("$set", {}).get("status")
                    target = ((stage == "initialization" and status == "processing") or
                              (stage == "activation" and status == "processed") or
                              (stage == "release" and "$unset" in value))
                    if target:
                        arrived.set()
                        await asyncio.Event().wait()
                    return await original(query, value)
                with patch.object(self.docs, "update_one", side_effect=update):
                    task = asyncio.create_task(self.process(body))
                    await asyncio.wait_for(arrived.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertNotEqual(self.retained(body)["status"], "failed")

    async def test_invalid_prior_generation_skips_destructive_cleanup_and_retains(self):
        for value in ("invalid", None):
            with self.subTest(value=value):
                body = await self.make_document()
                self.docs.documents[ObjectId(body["id"])]["chunk_generation"] = value
                with patch.object(self.chunks, "delete_many", new_callable=AsyncMock) as delete:
                    self.assertEqual((await self.process(body))[0], 503)
                    delete.assert_not_awaited()
                self.retained(body)

    async def test_activation_scope_race_does_not_overwrite_changed_generation(self):
        body = await self.prepared()
        newer = ObjectId()
        async def change(document):
            self.docs.documents[document["_id"]]["chunk_generation"] = newer
        with patch("document_processing.cleanup_document_vectors", side_effect=change), \
                patch.object(self.chunks, "delete_many", new_callable=AsyncMock) as delete:
            self.assertEqual((await self.process(body))[0], 503)
            delete.assert_not_awaited()
        self.assertEqual(self.retained(body)["chunk_generation"], newer)
