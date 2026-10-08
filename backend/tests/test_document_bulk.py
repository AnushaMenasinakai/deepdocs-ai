"""Offline bulk route tests with real single-delete cleanup and temporary storage."""
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
from main import app
from document_operations import claim_document


class BulkDeleteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = embedding_helpers.EmbeddingTests.asyncSetUp
    asyncTearDown = document_helpers.DocumentTests.asyncTearDown
    upload = document_helpers.DocumentTests.upload

    async def bulk(self, ids, base=None):
        return await request("POST", f"/api/knowledge-bases/{base or self.base_id}/documents/bulk-delete",
                             {"document_ids": ids})

    async def indexed(self):
        body = await self.upload()
        identifier, generation = ObjectId(body["id"]), ObjectId()
        chunk = {"_id": ObjectId(), "document_id": identifier, "owner_id": self.owner,
                 "knowledge_base_id": self.base_id, "generation": generation,
                 "chunk_index": 0, "text": "Synthetic text", "source_filename": "notes.pdf",
                 "page_start": 1, "page_end": 1}
        self.chunks.documents[chunk["_id"]] = chunk
        self.docs.documents[identifier].update(status="processed", chunk_generation=generation,
            chunk_count=1, vector_index={"status": "indexed", "collection_name": self.vector_store.collection,
                                       "target": self.vector_store.target})
        await self.vector_store.ensure_collection(3)
        await self.vector_store.upsert([chunk], [[1., 0., 0.]], 3)
        return body["id"]

    def assert_safe(self, body):
        for text in ("PRIVATE", "owner_id", "storage_path", "stored_filename", "vector_index", "traceback"):
            self.assertNotIn(text, str(body))

    async def test_authentication_required(self):
        del app.dependency_overrides[get_current_user]
        status, _, _ = await self.bulk([str(ObjectId())])
        self.assertEqual(status, 401)
        self.assertEqual(self.docs.queries, [])

    async def test_multiple_success_cleanup_reservations_history_and_single_delete(self):
        first, second, untouched = [await self.indexed() for _ in range(3)]
        history_id = ObjectId()
        self.history.documents[history_id] = {"_id": history_id, "owner_id": self.owner,
            "knowledge_base_id": self.base_id, "sources": [{"document_id": first}], "answer": "Snapshot"}
        history = copy.deepcopy(self.history.documents)
        status, body, _ = await self.bulk([second, first])
        self.assertEqual(status, 200)
        self.assertEqual(body, {"operation": "delete", "requested": 2, "succeeded": 2,
            "failed": 0, "not_attempted": 0, "results": [
                {"document_id": value, "outcome": "succeeded"} for value in (second, first)]})
        self.assertEqual(set(self.docs.documents), {ObjectId(untouched)})
        self.assertEqual({r["document_id"] for r in self.chunks.documents.values()}, {ObjectId(untouched)})
        self.assertEqual(self.bases.documents[self.base_id]["_document_ids"], [ObjectId(untouched)])
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 1)
        self.assertEqual(self.history.documents, history)
        for value in (first, second):
            self.assertFalse(self.storage.reference({"_id": ObjectId(value), "stored_filename": value+".pdf", "storage_path": value+".pdf"}).exists())
        self.assert_safe(body)
        self.assertEqual((await request("DELETE", "/api/documents/"+untouched))[0], 204)
        self.assertEqual(self.bases.documents[self.base_id]["_document_ids"], [])
        self.assertEqual(self.history.documents, history)

    async def test_foreign_missing_and_invalid_base_before_execution(self):
        identifier = await self.indexed()
        foreign = ObjectId()
        self.bases.documents[foreign] = {"_id": foreign, "owner_id": self.other}
        snapshot = copy.deepcopy(self.docs.documents)
        with patch("document_bulk.documents.delete_document", new_callable=AsyncMock) as delete:
            missing = await self.bulk([identifier], ObjectId())
            other = await self.bulk([identifier], foreign)
            self.assertEqual(missing[:2], other[:2])
            self.assertEqual(missing[0], 404)
            self.assertEqual((await self.bulk([identifier], "invalid"))[0], 422)
            delete.assert_not_awaited()
        self.assertEqual(self.docs.documents, snapshot)

    async def test_foreign_owner_wrong_base_and_stale_indistinguishable_even_if_busy(self):
        foreign, wrong, own = [await self.indexed() for _ in range(3)]
        self.docs.documents[ObjectId(foreign)]["owner_id"] = self.other
        self.docs.documents[ObjectId(foreign)]["_operation"] = ObjectId()
        self.docs.documents[ObjectId(wrong)]["knowledge_base_id"] = ObjectId()
        self.docs.documents[ObjectId(wrong)]["_operation"] = ObjectId()
        protected = {key: copy.deepcopy(self.docs.documents[ObjectId(key)]) for key in (foreign, wrong)}
        points = copy.deepcopy(self.qdrant.points[self.vector_store.collection])
        stale = str(ObjectId())
        status, body, _ = await self.bulk([foreign, own, wrong, stale])
        self.assertEqual(status, 200)
        self.assertEqual([r.get("code") for r in body["results"]], ["not_found", None, "not_found", "not_found"])
        self.assertEqual(body["succeeded"], 1)
        self.assertEqual(body["failed"], 3)
        for key, value in protected.items():
            self.assertEqual(self.docs.documents[ObjectId(key)], value)
            self.assertTrue(self.storage.reference(value).exists())
        surviving = self.qdrant.points[self.vector_store.collection]
        self.assertEqual(len(surviving), 2)
        self.assertTrue(all(points[key] == value for key, value in surviving.items()))

    async def test_request_validation_is_all_or_nothing(self):
        identifier = await self.indexed()
        invalid = [None, {}, [], {"document_ids": []}, {"document_ids": identifier},
            {"document_ids": {}}, {"document_ids": [identifier, 42]},
            {"document_ids": [identifier, None]}, {"document_ids": [identifier, True]},
            {"document_ids": [identifier, "PRIVATE-invalid"]},
            {"document_ids": [identifier, identifier]},
            {"document_ids": [identifier, identifier.upper()]},
            {"document_ids": [str(ObjectId()) for _ in range(101)]},
            {"document_ids": [identifier], "owner_id": "PRIVATE"}]
        before = copy.deepcopy(self.docs.documents)
        with patch("document_bulk.documents.delete_document", new_callable=AsyncMock) as delete:
            for payload in invalid:
                with self.subTest(payload=payload):
                    status, body, _ = await request("POST", self.path+"/bulk-delete", payload)
                    self.assertEqual(status, 422)
                    self.assertNotIn("PRIVATE", str(body))
                    self.assertTrue(all("input" not in detail and "ctx" not in detail for detail in body["detail"]))
            delete.assert_not_awaited()
        self.assertEqual(self.docs.documents, before)

    async def test_maximum_batch_and_canonicalization(self):
        ids = [str(ObjectId()) for _ in range(100)]
        status, body, _ = await self.bulk([value.upper() for value in ids])
        self.assertEqual(status, 200)
        self.assertEqual([r["document_id"] for r in body["results"]], ids)
        self.assertEqual(body["failed"], 100)
        self.assertEqual(body["not_attempted"], 0)

    async def test_busy_claim_preserved_and_continues_sequentially(self):
        busy, good = [await self.indexed() for _ in range(2)]
        _, token = await claim_document(self.database, self.owner, ObjectId(busy))
        before = copy.deepcopy(self.docs.documents[ObjectId(busy)])
        status, body, _ = await self.bulk([busy, good])
        self.assertEqual(status, 200)
        self.assertEqual(body["results"], [{"document_id": busy, "outcome": "failed", "code": "busy"},
                                           {"document_id": good, "outcome": "succeeded"}])
        self.assertEqual(self.docs.documents[ObjectId(busy)], before)
        self.assertEqual(before["_operation"], token)
        self.assertTrue(self.storage.reference(before).exists())

    async def test_membership_checked_in_atomic_claim_and_busy_lookup(self):
        identifier = ObjectId((await self.upload())["id"])
        original = self.docs.find_one_and_update
        other_base = ObjectId()
        async def move_before_claim(query, update, **kwargs):
            self.assertEqual(query["knowledge_base_id"], self.base_id)
            self.docs.documents[identifier]["knowledge_base_id"] = other_base
            return await original(query, update, **kwargs)
        with patch.object(self.docs, "find_one_and_update", side_effect=move_before_claim):
            status, body, _ = await self.bulk([str(identifier)])
        self.assertEqual(status, 200)
        self.assertEqual(body["results"][0]["code"], "not_found")
        self.assertNotIn("_operation", self.docs.documents[identifier])
        self.assertEqual(self.docs.queries[-1]["knowledge_base_id"], self.base_id)

    async def test_preexecution_database_failure_is_safe_503(self):
        identifier = await self.indexed()
        self.bases.failure = ConnectionFailure("PRIVATE")
        with patch("document_bulk.documents.delete_document", new_callable=AsyncMock) as delete:
            status, body, _ = await self.bulk([identifier])
            self.assertEqual(status, 503)
            self.assert_safe(body)
            delete.assert_not_awaited()

    async def test_cleanup_failure_stops_remaining_items_without_rollback(self):
        # Each injection is reached through the actual authoritative delete service.
        for stage in ("qdrant", "filesystem", "chunks", "reservation", "metadata", "claim"):
            with self.subTest(stage=stage):
                first, broken, last = [await self.indexed() for _ in range(3)]
                broken_id = ObjectId(broken)
                before_last = copy.deepcopy(self.docs.documents[ObjectId(last)])
                if stage == "qdrant":
                    target, method = self.vector_store, "delete_document"
                elif stage == "filesystem":
                    target, method = self.storage, "delete"
                elif stage == "chunks":
                    target, method = self.chunks, "delete_many"
                elif stage == "reservation":
                    target, method = self.bases, "update_one"
                elif stage == "metadata":
                    target, method = self.docs, "delete_one"
                else:
                    target, method = self.docs, "find_one_and_update"
                original = getattr(target, method)
                def is_broken(args):
                    if stage == "reservation":
                        return args[1].get("$pull", {}).get("_document_ids") == broken_id
                    return args[0].get("document_id", args[0].get("_id")) == broken_id
                async def fail_async(*args, **kwargs):
                    if is_broken(args):
                        if stage == "qdrant":
                            from vector_store import VectorFailure
                            raise VectorFailure("PRIVATE")
                        raise ConnectionFailure("PRIVATE")
                    return await original(*args, **kwargs)
                def fail_sync(*args, **kwargs):
                    if is_broken(args):
                        raise OSError("PRIVATE")
                    return original(*args, **kwargs)
                with patch.object(target, method, side_effect=fail_sync if stage == "filesystem" else fail_async):
                    status, body, _ = await self.bulk([first, broken, last])
                self.assertEqual(status, 200)
                self.assertEqual((body["succeeded"], body["failed"], body["not_attempted"]), (1, 1, 1))
                self.assertEqual(body["results"], [
                    {"document_id": first, "outcome": "succeeded"},
                    {"document_id": broken, "outcome": "failed", "code": "service_unavailable"},
                    {"document_id": last, "outcome": "not_attempted"}])
                self.assertNotIn(ObjectId(first), self.docs.documents)
                self.assertEqual(self.docs.documents[ObjectId(last)], before_last)
                row = self.docs.documents[broken_id]
                self.assertNotIn("_operation", row)
                self.assertEqual(self.storage.reference(row).exists(), stage in ("qdrant", "filesystem", "claim"))
                if stage in ("chunks", "reservation", "metadata"):
                    self.assertEqual(row["status"], "failed")
                reservations = self.bases.documents[self.base_id]["_document_ids"]
                self.assertIn(ObjectId(last), reservations)
                self.assertNotIn(ObjectId(first), reservations)
                self.assertEqual(broken_id in reservations, stage != "metadata")
                self.assert_safe(body)
                # Existing retry semantics tolerate already-removed PDF/chunks.
                self.assertEqual((await self.bulk([broken, last]))[1]["succeeded"], 2)

    async def test_cancellation_does_not_report_completion_or_start_next_item(self):
        first, second = str(ObjectId()), str(ObjectId())
        with patch("document_bulk.documents.delete_document", new_callable=AsyncMock,
                   side_effect=asyncio.CancelledError) as delete:
            with self.assertRaises(asyncio.CancelledError):
                await self.bulk([first, second])
            self.assertEqual(delete.await_count, 1)
