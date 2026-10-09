"""Deterministic acquisition fault injection; no live database or vector IO."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure, OperationFailure
from pymongo.write_concern import WriteConcern
from pymongo.read_preferences import ReadPreference
from test_documents import Collection
from document_operations import claim_document, release_document, ClaimUncertain, DocumentBusy
import document_processing
import document_embeddings
import documents
import document_bulk
from document_routes import safe_errors
from fastapi import HTTPException


class ClaimCollection(Collection):
    write_concern = WriteConcern()

    def with_options(self, **kwargs):
        self.options = kwargs
        return self


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner, self.identifier, self.base = ObjectId(), ObjectId(), ObjectId()
        self.collection = ClaimCollection()
        self.original = dict(_id=self.identifier, owner_id=self.owner, knowledge_base_id=self.base,
                             status="processed", chunk_generation=ObjectId(), chunk_count=1)
        self.collection.documents[self.identifier] = copy.deepcopy(self.original)
        self.database = SimpleNamespace(get_collection=lambda name: self.collection)

    async def acquire(self, **kwargs):
        return await claim_document(self.database, self.owner, self.identifier, **kwargs)

    async def test_normal_success_and_preimage(self):
        document, token = await self.acquire(expected_knowledge_base_id=self.base)
        self.assertEqual(document, self.original)
        self.assertEqual(self.collection.documents[self.identifier]["_operation"], token)
        self.assertEqual(self.collection.queries[0], {"_id": self.identifier, "owner_id": self.owner,
            "knowledge_base_id": self.base, "_operation": {"$exists": False}})

    async def test_busy_including_null_claim(self):
        for token in (ObjectId(), None):
            self.collection.documents[self.identifier]["_operation"] = token
            with self.assertRaises(DocumentBusy): await self.acquire()
            self.assertEqual(self.collection.documents[self.identifier]["_operation"], token)

    async def test_missing_foreign_and_wrong_base(self):
        for changes in ({"owner_id": ObjectId()}, {"knowledge_base_id": ObjectId()}):
            self.collection.documents[self.identifier] = {**self.original, **changes}
            self.assertIsNone((await self.acquire(expected_knowledge_base_id=self.base))[0])
            self.assertNotIn("_operation", self.collection.documents[self.identifier])
        self.collection.documents.clear()
        self.assertIsNone((await self.acquire())[0])

    async def test_commit_lost_ack_exact_scope_no_retry_and_snapshot(self):
        original = self.collection.find_one_and_update
        async def lost_ack(query, update):
            await original(query, update)
            raise ConnectionFailure("PRIVATE token and connection")
        with patch.object(self.collection, "find_one_and_update", side_effect=lost_ack) as write:
            document, token = await self.acquire(expected_knowledge_base_id=self.base)
        self.assertEqual(write.await_count, 1)
        self.assertEqual(document, self.original)
        self.assertNotIn("_operation", document)
        self.assertEqual(self.collection.documents[self.identifier]["_operation"], token)
        self.assertEqual(self.collection.queries[-1], {"_id": self.identifier, "owner_id": self.owner,
                         "knowledge_base_id": self.base, "_operation": token})
        self.assertEqual(self.collection.options["read_preference"], ReadPreference.PRIMARY)
        self.assertEqual(self.collection.options["read_concern"].document, {"level": "majority"})
        document["status"] = "changed"
        self.assertEqual(self.collection.documents[self.identifier]["status"], "processed")

    async def test_exception_or_label_does_not_prove_noncommit(self):
        for error in (ConnectionFailure("PRIVATE"), OperationFailure("PRIVATE", code=121),
                      OperationFailure("PRIVATE", details={"errorLabels": ["NoWritesPerformed"]})):
            with self.subTest(error=type(error).__name__):
                with patch.object(self.collection, "find_one_and_update", side_effect=error) as write:
                    with self.assertRaises(ClaimUncertain) as caught: await self.acquire()
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(write.await_count, 1)
                self.assertEqual(self.collection.documents[self.identifier], self.original)

    async def test_different_token_not_released_or_reported_busy(self):
        foreign_token = ObjectId()
        async def unknown(*args):
            self.collection.documents[self.identifier]["_operation"] = foreign_token
            raise ConnectionFailure("PRIVATE")
        with patch.object(self.collection, "find_one_and_update", side_effect=unknown), patch.object(
                self.collection, "update_one", wraps=self.collection.update_one) as release:
            with self.assertRaises(ClaimUncertain): await self.acquire()
            release.assert_not_awaited()
        self.assertEqual(self.collection.documents[self.identifier]["_operation"], foreign_token)

    async def test_verification_unavailable_or_unsupported_preserves_claim(self):
        original = self.collection.find_one_and_update
        async def lost(*args):
            await original(*args)
            raise ConnectionFailure("PRIVATE")
        for target in ("find_one", "with_options"):
            self.collection.documents[self.identifier] = copy.deepcopy(self.original)
            with patch.object(self.collection, "find_one_and_update", side_effect=lost), patch.object(
                    self.collection, target, side_effect=ConnectionFailure("PRIVATE")):
                with self.assertRaises(ClaimUncertain): await self.acquire()
            self.assertIn("_operation", self.collection.documents[self.identifier])

    async def test_verification_timeout_is_bounded(self):
        async def never(*args): await asyncio.Event().wait()
        with patch.object(self.collection, "find_one_and_update", side_effect=ConnectionFailure()), patch.object(
                self.collection, "find_one", side_effect=never), patch("document_operations.VERIFICATION_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(ClaimUncertain): await asyncio.wait_for(self.acquire(), 1)

    async def test_verification_rechecks_returned_identity(self):
        for key in ("owner_id", "knowledge_base_id", "_id", "_operation"):
            with self.subTest(key=key):
                async def bad_result(query):
                    return {**self.original, **query, key: ObjectId()}
                with patch.object(self.collection, "find_one_and_update", side_effect=ConnectionFailure()), patch.object(
                        self.collection, "find_one", side_effect=bad_result):
                    with self.assertRaises(ClaimUncertain):
                        await self.acquire(expected_knowledge_base_id=self.base)
                self.assertEqual(self.collection.documents[self.identifier], self.original)

    async def test_adapter_swallowing_cancellation_cannot_handoff(self):
        original = self.collection.find_one_and_update
        entered = asyncio.Event()
        async def swallow(*args):
            result = await original(*args)
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return result
        with patch.object(self.collection, "find_one_and_update", side_effect=swallow):
            task = asyncio.create_task(self.acquire())
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertIn("_operation", self.collection.documents[self.identifier])

    async def test_delayed_write_after_absence_not_retried_or_unlocked(self):
        captured = []
        async def delayed(query, update):
            captured.append((query, update))
            raise ConnectionFailure()
        with patch.object(self.collection, "find_one_and_update", side_effect=delayed) as write, patch.object(
                self.collection, "update_one", wraps=self.collection.update_one) as release:
            with self.assertRaises(ClaimUncertain): await self.acquire()
            release.assert_not_awaited()
            self.assertEqual(write.await_count, 1)
        # Simulate the already-dispatched server command committing afterwards.
        await self.collection.find_one_and_update(*captured[0])
        with self.assertRaises(DocumentBusy): await self.acquire()

    async def test_pre_dispatch_setup_failure_has_no_write(self):
        with patch("document_operations.ObjectId", side_effect=ValueError("local allocation")), patch.object(
                self.collection, "find_one_and_update", wraps=self.collection.find_one_and_update) as write:
            with self.assertRaises(ValueError): await self.acquire()
            write.assert_not_awaited()

    async def test_unacknowledged_configuration_rejected_before_dispatch(self):
        self.collection.write_concern = WriteConcern(w=0)
        with patch.object(self.collection, "find_one_and_update", wraps=self.collection.find_one_and_update) as write:
            with self.assertRaises(ClaimUncertain): await self.acquire()
            write.assert_not_awaited()

    async def test_cancellation_during_acquisition_never_hands_off_or_unlocks(self):
        for commit in (False, True):
            for service in ("process", "embed", "delete", "bulk_delete", "bulk_reindex"):
                with self.subTest(commit=commit, service=service):
                    self.collection.documents[self.identifier] = copy.deepcopy(self.original)
                    entered = asyncio.Event()
                    original = self.collection.find_one_and_update
                    async def interrupted(*args):
                        if commit: await original(*args)
                        entered.set()
                        await asyncio.Event().wait()
                    settings = SimpleNamespace()
                    storage = MagicMock()
                    calls = {
                        "process": lambda: document_processing.process_document(self.database, storage, self.owner, self.identifier, settings),
                        "embed": lambda: document_embeddings.generate_embeddings(self.database, self.owner, self.identifier, settings),
                        "delete": lambda: documents.delete_document(self.database, storage, self.owner, self.identifier),
                        "bulk_delete": lambda: document_bulk.delete_documents(self.database, storage, self.owner, self.base, [str(self.identifier), str(ObjectId())]),
                        "bulk_reindex": lambda: document_bulk.reindex_documents(self.database, self.owner, self.base, [str(self.identifier), str(ObjectId())], settings),
                    }
                    with patch.object(self.collection, "find_one_and_update", side_effect=interrupted) as write, patch.object(
                            self.collection, "update_one", side_effect=AssertionError("Downstream write or unlock")):
                        task = asyncio.create_task(calls[service]())
                        await asyncio.wait_for(entered.wait(), 1)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError): await task
                        self.assertEqual(write.await_count, 1)
                    self.assertEqual("_operation" in self.collection.documents[self.identifier], commit)
                    self.assertEqual(storage.mock_calls, [])

    async def test_cancellation_during_verification_propagates(self):
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.collection, "find_one_and_update", side_effect=ConnectionFailure()), patch.object(
                self.collection, "find_one", side_effect=wait):
            task = asyncio.create_task(self.acquire())
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task

    async def test_release_cannot_remove_other_token(self):
        _, token = await self.acquire()
        await release_document(self.database, self.owner, self.identifier, ObjectId())
        self.assertEqual(self.collection.documents[self.identifier]["_operation"], token)
        await release_document(self.database, ObjectId(), self.identifier, token)
        self.assertEqual(self.collection.documents[self.identifier]["_operation"], token)
        await release_document(self.database, self.owner, self.identifier, token)
        self.assertNotIn("_operation", self.collection.documents[self.identifier])

    async def test_uncertainty_uses_existing_http_service_error(self):
        with self.assertRaises(HTTPException) as caught:
            with safe_errors():
                raise ClaimUncertain()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(caught.exception.detail, "Document service is temporarily unavailable.")

    async def test_bulk_uncertainty_stops_without_mutation_or_new_token(self):
        ids = [str(self.identifier), str(ObjectId())]
        for operation in ("delete", "reindex"):
            with self.subTest(operation=operation):
                with patch.object(self.collection, "find_one_and_update", side_effect=ConnectionFailure()) as write:
                    if operation == "delete":
                        report = await document_bulk.delete_documents(self.database, MagicMock(), self.owner, self.base, ids)
                    else:
                        report = await document_bulk.reindex_documents(self.database, self.owner, self.base, ids, SimpleNamespace())
                self.assertEqual(write.await_count, 1)
                self.assertEqual((report.succeeded, report.failed, report.not_attempted), (0, 1, 1))
                self.assertEqual(report.results[0].code, "service_unavailable")
                self.assertEqual(report.results[1].outcome, "not_attempted")
                self.assertEqual(self.collection.documents[self.identifier], self.original)
