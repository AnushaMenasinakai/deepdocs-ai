"""Offline history API/persistence coverage; retrieval and Gemini remain independent."""
from citation_helpers import claims_for
import asyncio
import copy
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from main import app
from auth import get_current_user
from test_knowledge_bases import request
import test_rag as rag_helpers
from test_rag import hit
from gemini_provider import GeminiFailure
from vector_store import VectorFailure
from ask_history import ensure_history_indexes


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = rag_helpers.AskTests.asyncSetUp
    asyncTearDown = rag_helpers.AskTests.asyncTearDown
    setup_ask = rag_helpers.AskTests.setup_ask
    ask = rag_helpers.AskTests.ask
    make_document = rag_helpers.AskTests.make_document
    prepare = rag_helpers.AskTests.prepare
    process = rag_helpers.AskTests.process
    embed = rag_helpers.AskTests.embed

    def history_path(self, entry=None, base=None):
        return "/api/knowledge-bases/" + str(base or self.base_id) + "/ask-history" + ("/" + str(entry) if entry else "")

    async def completed(self, question="What is JWT?", supported=True):
        await self.setup_ask()
        provider = SimpleNamespace(answer=AsyncMock(side_effect=claims_for("Validated answer.")) if supported else AsyncMock(return_value=None))
        with patch("retrieval.search_chunks", AsyncMock(return_value=[hit()])), patch("rag.get_gemini_provider", return_value=provider):
            result = await self.ask({"question": question})
        self.assertEqual(result[0], 200)
        return result[1]

    async def test_answer_saved_once_with_safe_utc_snapshot(self):
        answer = await self.completed("  What is JWT?  ")
        self.assertEqual(len(self.history.documents), 1)
        record = next(iter(self.history.documents.values()))
        self.assertEqual(set(record), {"_id", "owner_id", "knowledge_base_id", "question", "status", "answer", "retrieved_chunk_count", "sources", "created_at", "citation_version", "claims"})
        self.assertEqual(record["owner_id"], self.owner)
        self.assertEqual(record["knowledge_base_id"], self.base_id)
        self.assertEqual(record["question"], "What is JWT?")
        self.assertEqual(record["created_at"].tzinfo, timezone.utc)
        for key, value in answer.items():
            self.assertEqual(record[key], value)
        status, items, _ = await request("GET", self.history_path())
        self.assertEqual(status, 200)
        self.assertEqual(set(items[0]), {"id", "question", "status", "answer", "retrieved_chunk_count", "sources", "created_at", "citation_version", "claims"})
        self.assertEqual(set(items[0]["sources"][0]), {"document_id", "source_filename", "page_start", "page_end", "citation_id"})
        self.assertEqual(items[0]["answer"], answer["answer"])

    async def test_provider_abstention_snapshot_zero_count_and_no_sources(self):
        response = await self.completed(supported=False)
        self.assertEqual(response["retrieved_chunk_count"], 1)  # Phase 9 response preserved.
        record = next(iter(self.history.documents.values()))
        self.assertEqual(record["status"], "insufficient_context")
        self.assertEqual(record["retrieved_chunk_count"], 0)
        self.assertEqual(record["sources"], [])

    async def test_empty_kb_abstention_saved(self):
        await self.setup_ask()
        with patch("rag.get_gemini_provider") as provider:
            self.assertEqual((await self.ask())[0], 200)
            provider.assert_not_called()
        self.assertEqual(len(self.history.documents), 1)
        self.assertEqual(next(iter(self.history.documents.values()))["sources"], [])

    async def test_failed_invalid_and_foreign_asks_never_saved(self):
        await self.setup_ask()
        for error in [VectorFailure("private"), ConnectionFailure("private")]:
            with patch("retrieval.search_chunks", AsyncMock(side_effect=error)):
                self.assertEqual((await self.ask())[0], 503)
        with patch("retrieval.search_chunks", AsyncMock(return_value=[hit()])), patch("rag.get_gemini_provider", side_effect=GeminiFailure("private")):
            self.assertEqual((await self.ask())[0], 503)
        for data in [{"question": " "}, {"question": "ok", "owner_id": str(self.other)}]:
            self.assertEqual((await self.ask(data))[0], 422)
        self.current_owner = self.other
        self.assertEqual((await self.ask())[0], 404)
        self.assertEqual(self.history.documents, {})

    async def test_malformed_final_result_never_saved(self):
        await self.setup_ask()
        with patch("rag_routes.answer_question", AsyncMock(return_value={"answer": "private"})):
            status, body, _ = await self.ask()
        self.assertEqual(status, 503)
        self.assertNotIn("private", str(body))
        self.assertEqual(self.history.documents, {})

    async def test_cancelled_generation_never_saved(self):
        await self.setup_ask()
        with patch("rag_routes.answer_question", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.ask()
        self.assertEqual(self.history.documents, {})

    async def test_save_failure_is_503_and_does_not_repeat_generation(self):
        await self.setup_ask()
        self.history.failure = ConnectionFailure("private db URI")
        provider = SimpleNamespace(answer=AsyncMock(side_effect=claims_for("A validated answer.")))
        with patch("retrieval.search_chunks", AsyncMock(return_value=[hit()])), patch("rag.get_gemini_provider", return_value=provider):
            status, body, _ = await self.ask()
        self.assertEqual(status, 503)
        self.assertNotIn("private", str(body))
        provider.answer.assert_awaited_once()
        self.assertFalse(self.history.documents)

    async def test_repeated_intentional_asks_create_separate_snapshots(self):
        await self.completed()
        await self.completed()
        self.assertEqual(len(self.history.documents), 2)

    async def test_history_never_enters_retrieval_or_context(self):
        await self.completed("Previous private question")
        record = next(iter(self.history.documents.values()))
        record["answer"] = "PREVIOUS_ANSWER_MARKER"
        provider = SimpleNamespace(answer=AsyncMock(side_effect=claims_for("New answer.")))
        with patch("retrieval.search_chunks", AsyncMock(return_value=[hit("Document text only")])) as search, patch("rag.get_gemini_provider", return_value=provider):
            await self.ask({"question": "New question?"})
        self.assertEqual(search.call_args.args[3].query, "New question?")
        self.assertEqual(provider.answer.call_args.args[0], "New question?")
        context = provider.answer.call_args.args[1]
        self.assertNotIn("PREVIOUS_ANSWER_MARKER", context.serialized)
        self.assertNotIn("Previous private question", context.serialized)
        self.assertEqual([c["text"] for c in context.chunks], ["Document text only"])

    async def test_authentication_required_for_all_history_endpoints(self):
        del app.dependency_overrides[get_current_user]
        for method, path in [("GET", self.history_path()), ("GET", self.history_path(ObjectId())), ("DELETE", self.history_path(ObjectId()))]:
            with self.subTest(method=method):
                status, _, headers = await request(method, path)
                self.assertEqual(status, 401)
                self.assertEqual(headers[b"www-authenticate"], b"Bearer")

    async def test_cross_user_and_cross_base_access_not_found(self):
        await self.completed()
        entry = next(iter(self.history.documents))
        self.current_owner = self.other
        for method, path in [("GET", self.history_path()), ("GET", self.history_path(entry)), ("DELETE", self.history_path(entry))]:
            self.assertEqual((await request(method, path))[0], 404)
        self.current_owner = self.owner
        other_base = ObjectId()
        self.bases.documents[other_base] = {**self.bases.documents[self.base_id], "_id": other_base}
        self.assertEqual((await request("GET", self.history_path(base=other_base)))[:2], (200, []))
        for method in ["GET", "DELETE"]:
            self.assertEqual((await request(method, self.history_path(entry, other_base)))[0], 404)
        self.assertEqual(len(self.history.documents), 1)

    async def test_list_order_limits_and_empty(self):
        self.assertEqual((await request("GET", self.history_path()))[:2], (200, []))
        await self.completed()
        sample = next(iter(self.history.documents.values()))
        self.history.documents.clear()
        for n in range(105):
            record = {**copy.deepcopy(sample), "_id": ObjectId(), "created_at": datetime(2026, 1, 1 if n < 50 else 2, tzinfo=timezone.utc)}
            self.history.documents[record["_id"]] = record
        expected = sorted(self.history.documents.values(), key=lambda d: (d["created_at"], d["_id"]), reverse=True)
        for suffix, count in [("", 20), ("?limit=1", 1), ("?limit=100", 100)]:
            status, items, _ = await request("GET", self.history_path()+suffix)
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in items], [str(item["_id"]) for item in expected[:count]])
        for value in ["0", "101", "-1", "abc", "true", "1.5"]:
            with self.subTest(limit=value):
                self.assertEqual((await request("GET", self.history_path()+"?limit="+value))[0], 422)

    async def test_detail_and_delete_only_target_record(self):
        await self.completed()
        await self.completed()
        entry = next(iter(self.history.documents))
        status, item, _ = await request("GET", self.history_path(entry))
        self.assertEqual(status, 200)
        self.assertEqual(item["id"], str(entry))
        self.assertEqual((await request("DELETE", self.history_path(entry)))[:2], (204, None))
        self.assertNotIn(entry, self.history.documents)
        self.assertEqual(len(self.history.documents), 1)
        for method in ["GET", "DELETE"]:
            self.assertEqual((await request(method, self.history_path(entry)))[0], 404)
            self.assertEqual((await request(method, self.history_path("bad")))[0], 422)

    async def test_database_read_delete_errors_are_safe(self):
        self.history.failure = ConnectionFailure("private")
        for method, path in [("GET", self.history_path()), ("GET", self.history_path(ObjectId())), ("DELETE", self.history_path(ObjectId()))]:
            status, body, _ = await request(method, path)
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(body))

    async def test_kb_deletion_cleans_only_its_owned_history(self):
        await self.completed()
        sample = next(iter(self.history.documents.values()))
        foreign = {**copy.deepcopy(sample), "_id": ObjectId(), "owner_id": self.other}
        self.history.documents[foreign["_id"]] = foreign
        self.assertEqual((await request("DELETE", "/api/knowledge-bases/"+str(self.base_id)))[0], 204)
        self.assertEqual(list(self.history.documents), [foreign["_id"]])

    async def test_kb_cleanup_failure_is_hidden_and_retryable(self):
        await self.completed()
        self.history.failure = ConnectionFailure("private")
        path = "/api/knowledge-bases/"+str(self.base_id)
        self.assertEqual((await request("DELETE", path))[0], 503)
        self.assertNotIn(self.base_id, self.bases.documents)
        self.assertEqual((await request("GET", self.history_path()))[0], 404)
        self.history.failure = None
        self.assertEqual((await request("DELETE", path))[0], 404)
        self.assertEqual(self.history.documents, {})

    async def test_kb_deleted_during_generation_cannot_leave_history(self):
        await self.setup_ask()
        async def answer(*args):
            self.bases.documents.clear()
            return await claims_for("A completed answer.")(*args)
        with patch("retrieval.search_chunks", AsyncMock(return_value=[hit()])), patch("rag.get_gemini_provider", return_value=SimpleNamespace(answer=answer)):
            self.assertEqual((await self.ask())[0], 404)
        self.assertFalse(self.history.documents)

    async def test_document_deletion_preserves_history_snapshot(self):
        document = await self.make_document()
        await self.completed()
        snapshot = copy.deepcopy(self.history.documents)
        self.assertEqual((await request("DELETE", "/api/documents/"+document["id"]))[0], 204)
        self.assertEqual(self.history.documents, snapshot)

    async def test_history_index(self):
        collection = SimpleNamespace(create_index=AsyncMock())
        await ensure_history_indexes(SimpleNamespace(get_collection=lambda name: collection))
        self.assertEqual(collection.create_index.call_args.args[0], [("owner_id", 1), ("knowledge_base_id", 1), ("created_at", -1), ("_id", -1)])
