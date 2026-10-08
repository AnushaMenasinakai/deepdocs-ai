"""Bulk indexing uses real services with fake inference/vector IO; no PDF parsing."""
import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_documents as document_helpers
import test_embeddings as embedding_helpers
from test_knowledge_bases import request
from auth import get_current_user
from config import EmbeddingSettings
from document_routes import embedding_settings
from main import app


class BulkReindexTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = document_helpers.DocumentTests.asyncTearDown
    upload = document_helpers.DocumentTests.upload

    async def asyncSetUp(self):
        await embedding_helpers.EmbeddingTests.asyncSetUp(self)
        app.dependency_overrides[embedding_settings] = lambda: EmbeddingSettings(batch_size=2)
        self.model = embedding_helpers.FakeModel()
        model_patch = patch("embeddings.load_model", return_value=self.model)
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.no_processing = patch("document_processing.process_document", new_callable=AsyncMock,
                                   side_effect=AssertionError("Bulk must not process PDFs"))
        self.process_mock = self.no_processing.start()
        self.addCleanup(self.no_processing.stop)

    async def prepared(self):
        identifier = ObjectId((await self.upload())["id"])
        generation = ObjectId()
        self.docs.documents[identifier].update(status="processed", chunk_generation=generation, chunk_count=3)
        for index in range(3):
            chunk = {"_id": ObjectId(), "owner_id": self.owner, "knowledge_base_id": self.base_id,
                     "document_id": identifier, "generation": generation, "chunk_index": index,
                     "text": f"Synthetic page {index + 1}", "source_filename": "notes.pdf",
                     "page_start": index + 1, "page_end": index + 1}
            self.chunks.documents[chunk["_id"]] = chunk
        return str(identifier)

    async def bulk(self, ids, base=None):
        return await request("POST", f"/api/knowledge-bases/{base or self.base_id}/documents/bulk-reindex",
                             {"document_ids": ids})

    def assert_safe(self, body):
        for secret in ("PRIVATE", "owner_id", "vector_index", "collection_name", "target",
                       "storage_path", "model_cache", "embedding_dimension"):
            self.assertNotIn(secret, str(body))

    async def test_authentication_and_owned_base_validation(self):
        identifier = await self.prepared()
        foreign = ObjectId()
        self.bases.documents[foreign] = {"_id": foreign, "owner_id": self.other}
        before = copy.deepcopy(self.docs.documents)
        missing = await self.bulk([identifier], ObjectId())
        self.assertEqual((await self.bulk([identifier], foreign))[:2], missing[:2])
        self.assertEqual(missing[0], 404)
        self.assertEqual((await self.bulk([identifier], "bad"))[0], 422)
        del app.dependency_overrides[get_current_user]
        self.assertEqual((await self.bulk([identifier]))[0], 401)
        self.assertEqual(self.docs.documents, before)
        self.assertEqual(self.model.batches, [])

    async def test_strict_validation_before_any_indexing(self):
        identifier = await self.prepared()
        invalid = [None, {}, [], {"document_ids": []}, {"document_ids": identifier},
                   {"document_ids": {}}, {"document_ids": [identifier, 12]},
                   {"document_ids": [identifier, None]}, {"document_ids": [identifier, True]},
                   {"document_ids": [identifier, "PRIVATE-invalid"]},
                   {"document_ids": [identifier, identifier.upper()]},
                   {"document_ids": [identifier] * 2},
                   {"document_ids": [str(ObjectId()) for _ in range(6)]},
                   {"document_ids": [identifier], "model": "PRIVATE"}]
        before = copy.deepcopy(self.docs.documents)
        with patch("document_bulk.document_embeddings.generate_embeddings", new_callable=AsyncMock) as generate:
            for payload in invalid:
                with self.subTest(payload=payload):
                    status, body, _ = await request("POST", self.path+"/bulk-reindex", payload)
                    self.assertEqual(status, 422)
                    self.assertNotIn("PRIVATE", str(body))
                    self.assertTrue(all("input" not in item and "ctx" not in item for item in body["detail"]))
            generate.assert_not_awaited()
        self.assertEqual(self.docs.documents, before)

    async def test_five_successes_order_repeat_safe_and_existing_chunk_pipeline(self):
        ids = [await self.prepared() for _ in range(5)]
        ordered = list(reversed(ids))
        chunks = copy.deepcopy(self.chunks.documents)
        history_id = ObjectId()
        self.history.documents[history_id] = {"_id": history_id, "answer": "Historical snapshot"}
        history = copy.deepcopy(self.history.documents)
        status, body, _ = await self.bulk([value.upper() for value in ordered])
        self.assertEqual(status, 200)
        self.assertEqual(body, {"operation": "reindex", "requested": 5, "succeeded": 5,
                               "failed": 0, "not_attempted": 0, "results": [
                                   {"document_id": value, "outcome": "succeeded"} for value in ordered]})
        point_ids = set(self.qdrant.points[self.vector_store.collection])
        self.assertEqual(len(point_ids), 15)
        self.assertEqual((await self.bulk([ids[0]]))[1]["succeeded"], 1)
        self.assertEqual(set(self.qdrant.points[self.vector_store.collection]), point_ids)
        self.assertEqual(self.chunks.documents, chunks)
        self.assertEqual(self.history.documents, history)
        self.assertEqual([len(batch) for batch in self.model.batches], [2, 1] * 6)
        self.process_mock.assert_not_awaited()
        for value in ids:
            row = self.docs.documents[ObjectId(value)]
            self.assertNotIn("_operation", row)
            self.assertEqual(row["status"], "processed")
            self.assertEqual(row["embedding"]["status"], "generated")
            self.assertEqual(row["vector_index"]["status"], "indexed")
            self.assertEqual(row["vector_index"]["chunk_generation"], row["chunk_generation"])
            self.assertEqual(row["vector_index"]["chunk_count"], 3)
            self.assertTrue(self.storage.reference(row).exists())
        self.assert_safe(body)

    async def test_not_found_isolation_and_busy_mixed_with_success(self):
        foreign, wrong, busy, good = [await self.prepared() for _ in range(4)]
        self.docs.documents[ObjectId(foreign)].update(owner_id=self.other, _operation=ObjectId())
        self.docs.documents[ObjectId(wrong)].update(knowledge_base_id=ObjectId(), _operation=ObjectId())
        self.docs.documents[ObjectId(busy)]["_operation"] = ObjectId()
        before = copy.deepcopy(self.docs.documents)
        stale = str(ObjectId())
        status, body, _ = await self.bulk([foreign, good, wrong, stale, busy])
        self.assertEqual(status, 200)
        self.assertEqual([r.get("code") for r in body["results"]],
                         ["not_found", None, "not_found", "not_found", "busy"])
        for value in (foreign, wrong, busy):
            self.assertEqual(self.docs.documents[ObjectId(value)], before[ObjectId(value)])
        self.assertEqual({p.payload["document_id"] for p in self.qdrant.points[self.vector_store.collection].values()}, {good})

    async def test_ineligible_metadata_rejected_without_claim_or_mutation(self):
        identifier = await self.prepared()
        for changes in ({"status": "uploaded"}, {"status": "failed"}, {"status": "processing"},
                        {"status": "processed", "chunk_generation": None},
                        {"status": "processed", "chunk_generation": "invalid"}):
            with self.subTest(changes=changes):
                row = self.docs.documents[ObjectId(identifier)]
                row.update(changes)
                before = copy.deepcopy(row)
                with patch.object(self.docs, "find_one_and_update", wraps=self.docs.find_one_and_update) as claim:
                    status, body, _ = await self.bulk([identifier])
                    claim.assert_not_awaited()
                self.assertEqual(status, 200)
                self.assertEqual(body["results"][0]["code"], "requires_processing")
                self.assertEqual(row, before)
        self.assertEqual(self.model.batches, [])

    async def test_no_chunks_requires_processing_and_mixed_batch_continues(self):
        empty, good = await self.prepared(), await self.prepared()
        for key in list(self.chunks.documents):
            if self.chunks.documents[key]["document_id"] == ObjectId(empty):
                del self.chunks.documents[key]
        self.docs.documents[ObjectId(empty)]["chunk_count"] = 0
        uploaded = (await self.upload())["id"]
        before = copy.deepcopy(self.docs.documents[ObjectId(empty)])
        status, body, _ = await self.bulk([empty, good, uploaded])
        self.assertEqual(status, 200)
        self.assertEqual([r.get("code") for r in body["results"]], ["requires_processing", None, "requires_processing"])
        self.assertEqual(self.docs.documents[ObjectId(empty)], before)

    async def test_membership_and_eligibility_rechecked_after_preflight(self):
        identifier = await self.prepared()
        original = self.docs.find_one_and_update
        async def move_before_claim(query, update, **kwargs):
            self.assertEqual(query["knowledge_base_id"], self.base_id)
            self.docs.documents[ObjectId(identifier)]["knowledge_base_id"] = ObjectId()
            return await original(query, update, **kwargs)
        with patch.object(self.docs, "find_one_and_update", side_effect=move_before_claim):
            self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "not_found")
        self.docs.documents[ObjectId(identifier)]["knowledge_base_id"] = self.base_id
        async def unprocess_before_claim(query, update, **kwargs):
            self.docs.documents[ObjectId(identifier)]["status"] = "uploaded"
            return await original(query, update, **kwargs)
        with patch.object(self.docs, "find_one_and_update", side_effect=unprocess_before_claim):
            self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "requires_processing")
        self.assertNotIn("_operation", self.docs.documents[ObjectId(identifier)])
        self.assertEqual(self.model.batches, [])

    async def test_shared_model_and_qdrant_failures_stop_and_are_retryable(self):
        for stage in ("model", "qdrant"):
            with self.subTest(stage=stage):
                broken, later = await self.prepared(), await self.prepared()
                original_later = copy.deepcopy(self.docs.documents[ObjectId(later)])
                target, method = ("embeddings", "load_model") if stage == "model" else ("", "upsert")
                failure = patch(target+"."+method, side_effect=RuntimeError("PRIVATE")) if target else patch.object(self.qdrant, method, side_effect=RuntimeError("PRIVATE"))
                with failure:
                    status, body, _ = await self.bulk([broken, later])
                self.assertEqual(status, 200)
                self.assertEqual(body["results"], [
                    {"document_id": broken, "outcome": "failed", "code": "service_unavailable"},
                    {"document_id": later, "outcome": "not_attempted"}])
                row = self.docs.documents[ObjectId(broken)]
                self.assertEqual(row["embedding"]["status"], "failed")
                self.assertEqual(row["vector_index"]["status"], "failed")
                self.assertNotIn("_operation", row)
                self.assertEqual(self.docs.documents[ObjectId(later)], original_later)
                self.assert_safe(body)
                self.assertEqual((await self.bulk([broken]))[1]["succeeded"], 1)

    async def test_success_before_failure_not_rolled_back_count_verification_enforced(self):
        good, broken, later = [await self.prepared() for _ in range(3)]
        original = self.vector_store.count
        async def wrong_count(document, *args, **kwargs):
            persisted = any(point.payload["document_id"] == broken for point in
                            self.qdrant.points.get(self.vector_store.collection, {}).values())
            if str(document["_id"]) == broken and persisted:
                return 999
            return await original(document, *args, **kwargs)
        with patch.object(self.vector_store, "count", side_effect=wrong_count):
            status, body, _ = await self.bulk([good, broken, later])
        self.assertEqual(status, 200)
        self.assertEqual((body["succeeded"], body["failed"], body["not_attempted"]), (1, 1, 1))
        self.assertEqual(self.docs.documents[ObjectId(good)]["vector_index"]["status"], "indexed")
        self.assertEqual(self.docs.documents[ObjectId(broken)]["vector_index"]["status"], "failed")
        self.assertNotIn("vector_index", self.docs.documents[ObjectId(later)])

    async def test_invalid_chunk_order_and_count_fail_safely(self):
        for bad in ("order", "count"):
            with self.subTest(bad=bad):
                identifier = await self.prepared()
                if bad == "count":
                    self.docs.documents[ObjectId(identifier)]["chunk_count"] = 10
                else:
                    chunk = next(c for c in self.chunks.documents.values() if c["document_id"] == ObjectId(identifier))
                    chunk["chunk_index"] = 9
                status, body, _ = await self.bulk([identifier, str(ObjectId())])
                self.assertEqual(status, 200)
                self.assertEqual(body["results"][0]["code"], "service_unavailable")
                self.assertEqual(body["results"][1]["outcome"], "not_attempted")
                self.assertNotIn("_operation", self.docs.documents[ObjectId(identifier)])
        self.assertEqual(self.model.batches, [])

    async def test_persistent_metadata_failure_keeps_recovery_claim(self):
        identifier, later = await self.prepared(), await self.prepared()
        original = self.docs.update_one
        async def fail_publish(query, update):
            if update.get("$set", {}).get("embedding", {}).get("status") in ("generated", "failed"):
                raise ConnectionFailure("PRIVATE")
            return await original(query, update)
        with patch.object(self.docs, "update_one", side_effect=fail_publish):
            status, body, _ = await self.bulk([identifier, later])
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["code"], "service_unavailable")
        self.assertEqual(body["results"][1]["outcome"], "not_attempted")
        row = self.docs.documents[ObjectId(identifier)]
        self.assertIn("_operation", row)
        self.assertEqual(row["vector_index"]["status"], "indexing")
        self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "busy")
        self.assert_safe(body)

    async def test_cancellation_before_writes_releases_claim_after_writes_retains_it(self):
        for started in (False, True):
            with self.subTest(started=started):
                identifier, later = await self.prepared(), await self.prepared()
                arrived = asyncio.Event()
                original = self.vector_store.upsert
                async def wait(*args, **kwargs):
                    if started:
                        await original(*args, **kwargs)
                    arrived.set()
                    await asyncio.Event().wait()
                interruption = patch.object(self.vector_store, "upsert", side_effect=wait) if started else patch("document_embeddings.inspect_chunks", side_effect=wait)
                with interruption:
                    task = asyncio.create_task(self.bulk([identifier, later]))
                    try:
                        await asyncio.wait_for(arrived.wait(), 3)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    finally:
                        if not task.done():
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                row = self.docs.documents[ObjectId(identifier)]
                self.assertEqual("_operation" in row, started)
                if started:
                    self.assertEqual(row["embedding"]["status"], "generating")
                    self.assertEqual(row["vector_index"]["status"], "indexing")
                    self.assertEqual((await self.bulk([identifier]))[1]["results"][0]["code"], "busy")
                self.assertNotIn("vector_index", self.docs.documents[ObjectId(later)])

    async def test_preexecution_service_failure(self):
        identifier = await self.prepared()
        self.bases.failure = ConnectionFailure("PRIVATE")
        status, body, _ = await self.bulk([identifier])
        self.assertEqual(status, 503)
        self.assert_safe(body)
        self.assertEqual(self.model.batches, [])

    async def test_cancelled_unlock_failure_still_propagates_cancellation(self):
        identifier, later = await self.prepared(), await self.prepared()
        with patch("document_embeddings.inspect_chunks", side_effect=asyncio.CancelledError), patch(
            "document_embeddings.release_document", side_effect=ConnectionFailure("PRIVATE")
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.bulk([identifier, later])
        self.assertIn("_operation", self.docs.documents[ObjectId(identifier)])
        self.assertNotIn("_operation", self.docs.documents[ObjectId(later)])
        self.assertNotIn("vector_index", self.docs.documents[ObjectId(later)])

    async def test_cancelled_publication_ack_does_not_undo_completed_state(self):
        identifier, later = await self.prepared(), await self.prepared()
        original = self.docs.update_one
        async def cancel_ack(query, update):
            result = await original(query, update)
            if update.get("$set", {}).get("embedding", {}).get("status") == "generated":
                raise asyncio.CancelledError
            return result
        with patch.object(self.docs, "update_one", side_effect=cancel_ack):
            with self.assertRaises(asyncio.CancelledError):
                await self.bulk([identifier, later])
        row = self.docs.documents[ObjectId(identifier)]
        self.assertEqual(row["vector_index"]["status"], "indexed")
        self.assertNotIn("_operation", row)
        self.assertNotIn("vector_index", self.docs.documents[ObjectId(later)])

    async def test_busy_race_at_claim_and_sequential_service_calls(self):
        import document_embeddings
        busy, first, second = [await self.prepared() for _ in range(3)]
        original_claim = self.docs.find_one_and_update
        async def race(query, update, **kwargs):
            if query["_id"] == ObjectId(busy):
                self.docs.documents[ObjectId(busy)]["_operation"] = ObjectId()
            return await original_claim(query, update, **kwargs)
        original_generate = document_embeddings.generate_embeddings
        active = 0
        completed = []
        async def measured(*args, **kwargs):
            nonlocal active
            active += 1
            self.assertEqual(active, 1)
            try:
                await asyncio.sleep(0)
                return await original_generate(*args, **kwargs)
            finally:
                completed.append(str(args[2]))
                active -= 1
        with patch.object(self.docs, "find_one_and_update", side_effect=race), patch(
            "document_bulk.document_embeddings.generate_embeddings", side_effect=measured
        ):
            status, body, _ = await self.bulk([busy, first, second])
        self.assertEqual(status, 200)
        self.assertEqual([r.get("code") for r in body["results"]], ["busy", None, None])
        self.assertEqual(completed, [busy, first, second])
