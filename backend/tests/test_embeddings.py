"""Offline provider and real ASGI integration checks; no model/network/Atlas."""
import asyncio
import copy
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
import test_processing as processing_helpers
import test_documents as document_helpers
from test_documents import request
from config import EmbeddingSettings, load_embedding_settings, ConfigurationError
from embeddings import embed_batches, embed_text, validate_vector, model_dimension, load_model, _load_model, EmbeddingFailure
from document_routes import embedding_settings
from main import app
from auth import get_current_user


class FakeModel:
    def __init__(self):
        self.batches = []

    def get_sentence_embedding_dimension(self):
        return 3

    def encode(self, texts, **kwargs):
        self.batches.append(list(texts))
        return [[0.1, 0.2, 0.3] for text in texts]


class ProviderTests(unittest.TestCase):
    def test_batching_single_text_and_dimension(self):
        model = FakeModel()
        with patch("embeddings.load_model", return_value=model):
            batches = list(embed_batches(["one", "two", "three", "four", "five"], EmbeddingSettings(batch_size=2)))
            self.assertEqual([len(vectors) for dim, vectors in batches], [2, 2, 1])
            self.assertEqual([dim for dim, vectors in batches], [3, 3, 3])
            self.assertEqual(model.batches, [["one", "two"], ["three", "four"], ["five"]])
            self.assertEqual(embed_text("one", EmbeddingSettings()), [0.1, 0.2, 0.3])

    def test_invalid_vectors(self):
        for vector in [None, [], [1], [1, 2, 3, 4], [1, float("nan"), 2],
                       [1, float("inf"), 2], [1, -float("inf"), 2], [1, "2", 3],
                       [True, 2, 3], [[1], 2, 3], [None, 2, 3]]:
            with self.subTest(vector=vector), self.assertRaises(EmbeddingFailure):
                validate_vector(vector, 3)
        self.assertEqual(validate_vector([1, 2.5, -3], 3), [1., 2.5, -3.])

    def test_bad_dimensions_and_batch_lengths(self):
        for dimension in [None, 0, -1, True, "3", 3.5]:
            with self.subTest(dimension=dimension), self.assertRaises(EmbeddingFailure):
                model_dimension(SimpleNamespace(get_sentence_embedding_dimension=lambda: dimension))
        model = FakeModel()
        model.encode = lambda *args, **kwargs: []
        with patch("embeddings.load_model", return_value=model), self.assertRaises(EmbeddingFailure):
            list(embed_batches(["text"], EmbeddingSettings()))

    def test_empty_input_rejected(self):
        for texts in [[], [""], ["  "], [None]]:
            with self.subTest(texts=texts), self.assertRaises(EmbeddingFailure):
                list(embed_batches(texts, EmbeddingSettings()))

    def test_lazy_cached_model_and_safe_load_failure(self):
        _load_model.cache_clear()
        calls = []
        def factory(name, **kwargs):
            calls.append((name, kwargs))
            return FakeModel()
        with patch.dict(sys.modules, {"sentence_transformers": SimpleNamespace(SentenceTransformer=factory)}):
            first = load_model(EmbeddingSettings.model_name)
            self.assertIs(first, load_model(EmbeddingSettings.model_name))
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0][1]["trust_remote_code"])
            self.assertEqual(calls[0][1]["device"], "cpu")
        _load_model.cache_clear()
        with patch("embeddings._load_model", side_effect=RuntimeError("private-model-path")):
            with self.assertRaises(EmbeddingFailure) as result:
                load_model(EmbeddingSettings.model_name)
            self.assertNotIn("private", str(result.exception))

    def test_configuration(self):
        with patch("config.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_embedding_settings(), EmbeddingSettings())
        for values in [{"EMBEDDING_BATCH_SIZE": "0"}, {"EMBEDDING_BATCH_SIZE": "129"},
                       {"EMBEDDING_BATCH_SIZE": "private"}, {"EMBEDDING_MODEL_NAME": "../private"},
                       {"EMBEDDING_MODEL_NAME": "C:/private"}, {"EMBEDDING_MODEL_NAME": ""}]:
            with self.subTest(values=values), patch("config.dotenv_values", return_value=values), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as result:
                    load_embedding_settings()
                self.assertNotIn("private", str(result.exception))


class EmbeddingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await document_helpers.DocumentTests.asyncSetUp(self)
        from qdrant_helpers import FakeQdrant
        from vector_store import VectorStore
        from config import QdrantSettings
        self.qdrant = FakeQdrant()
        self.vector_store = VectorStore(QdrantSettings("http://localhost:6333"), self.qdrant)
        self.store_patch = patch("vector_store._store", self.vector_store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
    asyncTearDown = document_helpers.DocumentTests.asyncTearDown
    make_document = processing_helpers.ProcessingTests.make_document
    process = processing_helpers.ProcessingTests.process

    async def prepare(self):
        body = await self.make_document(["First page text.", "Second page text.", "Third page text."])
        self.assertEqual((await self.process(body))[0], 200)
        app.dependency_overrides[embedding_settings] = lambda: EmbeddingSettings(batch_size=2)
        return body

    async def embed(self, body):
        return await request("POST", "/api/documents/" + body["id"] + "/embeddings")

    async def test_success_order_repeat_safe_metadata_and_no_vectors(self):
        body = await self.prepare()
        before = copy.deepcopy(self.chunks.documents)
        # Deliberately reverse physical insertion order to test query sorting.
        self.chunks.documents = dict(reversed(list(self.chunks.documents.items())))
        model = FakeModel()
        with patch("embeddings.load_model", return_value=model):
            for _ in range(2):
                status, value, _ = await self.embed(body)
                self.assertEqual(status, 200)
                self.assertEqual(set(value), {"document_id", "chunk_count", "embedding_model", "embedding_dimension", "status", "vector_store", "collection_name", "vector_status"})
                self.assertEqual(value["embedding_dimension"], 3)
                self.assertEqual(value["chunk_count"], 3)
                self.assertEqual(value["status"], "generated")
        self.assertEqual([len(batch) for batch in model.batches], [2, 1, 2, 1])
        self.assertEqual(model.batches[0], ["First page text.", "Second page text."])
        self.assertEqual(self.chunks.documents, before)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertNotIn("_operation", record)
        self.assertEqual(record["embedding"]["chunk_generation"], record["chunk_generation"])
        self.assertEqual(set(record["embedding"]), {"status", "model", "dimension", "chunk_generation", "chunk_count", "embedded_at", "error"})
        self.assertIsNotNone(record["embedding"]["embedded_at"].tzinfo)

    async def test_auth_owner_missing_and_invalid_id(self):
        body = await self.prepare()
        self.current_owner = self.other
        with patch("embeddings.load_model") as model:
            self.assertEqual((await self.embed(body))[0], 404)
            self.assertEqual((await self.embed({"id": str(ObjectId())}))[0], 404)
            self.assertEqual((await self.embed({"id": "invalid"}))[0], 422)
            del app.dependency_overrides[get_current_user]
            self.assertEqual((await self.embed(body))[0], 401)
            model.assert_not_called()

    async def test_unprocessed_no_chunks_and_bad_order(self):
        body = await self.prepare()
        record = self.docs.documents[ObjectId(body["id"])]
        with patch("embeddings.load_model") as model:
            for state in ["uploaded", "processing", "failed"]:
                record["status"] = state
                self.assertEqual((await self.embed(body))[0], 409)
                self.assertNotIn("_operation", record)
            record["status"] = "processed"
            next(iter(self.chunks.documents.values()))["chunk_index"] = 9
            self.assertEqual((await self.embed(body))[0], 503)
            self.chunks.documents.clear()
            record["chunk_count"] = 0
            self.assertEqual((await self.embed(body))[0], 409)
            model.assert_not_called()

    async def test_model_encode_and_vector_failures_are_safe(self):
        body = await self.prepare()
        for failure in ["load", "encode", "vector"]:
            with self.subTest(failure=failure):
                model = FakeModel()
                if failure == "encode":
                    def fail(*args, **kwargs):
                        raise RuntimeError("private-cache-path")
                    model.encode = fail
                elif failure == "vector":
                    model.encode = lambda texts, **kwargs: [[float("nan"), 1, 2] for _ in texts]
                with patch("embeddings._load_model", side_effect=RuntimeError("private-cache-path") if failure == "load" else None, return_value=model):
                    status, value, _ = await self.embed(body)
                self.assertEqual(status, 503)
                self.assertNotIn("private", str(value))
                record = self.docs.documents[ObjectId(body["id"])]
                self.assertEqual(record["status"], "processed")
                self.assertEqual(record["embedding"]["status"], "failed")
                self.assertNotIn("_operation", record)

    async def test_metadata_update_failure_does_not_claim_success(self):
        body = await self.prepare()
        original = self.docs.update_one
        async def fail(query, update):
            if update.get("$set", {}).get("embedding", {}).get("status") == "generated":
                raise ConnectionFailure("private-driver")
            return await original(query, update)
        self.docs.update_one = fail
        with patch("embeddings.load_model", return_value=FakeModel()):
            status, value, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private", str(value))
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["embedding"]["status"], "failed")

    async def test_failed_retry_replaces_previous_success(self):
        body = await self.prepare()
        with patch("embeddings.load_model", return_value=FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        with patch("embeddings._load_model", side_effect=RuntimeError("private")):
            self.assertEqual((await self.embed(body))[0], 503)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["embedding"]["status"], "failed")

    async def test_reprocessing_resets_and_deletion_removes_metadata(self):
        body = await self.prepare()
        with patch("embeddings.load_model", return_value=FakeModel()):
            await self.embed(body)
        old = self.docs.documents[ObjectId(body["id"])]["chunk_generation"]
        self.assertEqual((await self.process(body))[0], 200)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertNotEqual(record["chunk_generation"], old)
        self.assertEqual(record["embedding"], {"status": "not_generated"})
        with patch("embeddings.load_model", return_value=FakeModel()):
            await self.embed(body)
        unrelated = copy.deepcopy(next(iter(self.chunks.documents.values())))
        unrelated["_id"], unrelated["owner_id"] = ObjectId(), self.other
        self.chunks.documents[unrelated["_id"]] = unrelated
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)
        self.assertNotIn(ObjectId(body["id"]), self.docs.documents)
        self.assertEqual(list(self.chunks.documents), [unrelated["_id"]])
        self.assertFalse((self.root / (body["id"] + ".pdf")).exists())

    async def test_busy_operation_and_safe_database_config_errors(self):
        body = await self.prepare()
        record = self.docs.documents[ObjectId(body["id"])]
        record["_operation"] = ObjectId()
        self.assertEqual((await self.embed(body))[0], 409)
        del record["_operation"]
        self.docs.failure = ConnectionFailure("private-driver")
        self.assertEqual((await self.embed(body))[0], 503)
        self.docs.failure = None
        app.dependency_overrides.pop(embedding_settings)
        with patch("document_routes.load_embedding_settings", side_effect=ConfigurationError("private-config")):
            status, value, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private", str(value))

    async def test_concurrent_process_delete_and_embed_rejected(self):
        body = await self.prepare()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.docs.update_one
        async def wait(query, update):
            result = await original(query, update)
            if update.get("$set", {}).get("embedding", {}).get("status") == "generating":
                entered.set()
                await release.wait()
            return result
        self.docs.update_one = wait
        with patch("embeddings.load_model", return_value=FakeModel()):
            task = asyncio.create_task(self.embed(body))
            await entered.wait()
            try:
                self.assertEqual((await self.embed(body))[0], 409)
                self.assertEqual((await self.process(body))[0], 409)
                self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 409)
            finally:
                release.set()
                self.assertEqual((await task)[0], 200)


    async def test_late_batch_failure_never_publishes_partial_success(self):
        body = await self.prepare()
        model = FakeModel()
        original = model.encode
        def encode(texts, **kwargs):
            if model.batches:
                return [[0.1, 0.2]]  # Wrong dimension only in the last batch.
            return original(texts, **kwargs)
        model.encode = encode
        with patch("embeddings.load_model", return_value=model):
            self.assertEqual((await self.embed(body))[0], 503)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["embedding"]["status"], "failed")
        self.assertEqual(len(self.chunks.documents), 3)

    async def test_lost_success_ack_is_safe_and_retryable(self):
        body = await self.prepare()
        original = self.docs.update_one
        async def lost_ack(query, update):
            result = await original(query, update)
            if update.get("$set", {}).get("embedding", {}).get("status") == "generated":
                raise ConnectionFailure("private-ack")
            return result
        self.docs.update_one = lost_ack
        with patch("embeddings.load_model", return_value=FakeModel()):
            status, value, _ = await self.embed(body)
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(value))
            record = self.docs.documents[ObjectId(body["id"])]
            self.assertEqual(record["embedding"]["status"], "generated")
            self.assertNotIn("_operation", record)
            self.docs.update_one = original
            self.assertEqual((await self.embed(body))[0], 200)

    async def test_persistent_database_failure_keeps_non_success_lock(self):
        body = await self.prepare()
        original = self.docs.update_one
        async def fail(query, update):
            state = update.get("$set", {}).get("embedding", {}).get("status")
            if state in {"generated", "failed"}:
                raise ConnectionFailure("private-driver")
            return await original(query, update)
        self.docs.update_one = fail
        with patch("embeddings.load_model", return_value=FakeModel()):
            self.assertEqual((await self.embed(body))[0], 503)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertEqual(record["embedding"]["status"], "generating")
        self.assertIn("_operation", record)
        self.assertEqual((await self.embed(body))[0], 409)

    async def test_failed_reprocessing_also_invalidates_embedding_state(self):
        from pdf_processing import ProcessingFailure
        body = await self.prepare()
        with patch("embeddings.load_model", return_value=FakeModel()):
            await self.embed(body)
        with patch("document_processing.extract_chunks", side_effect=ProcessingFailure("No extractable text was found.")):
            self.assertEqual((await self.process(body))[0], 422)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertEqual(record["embedding"], {"status": "not_generated"})
        self.assertEqual((await self.embed(body))[0], 409)

    async def test_chunk_database_failure_is_safe_and_unlocks(self):
        body = await self.prepare()
        self.chunks.failure = ConnectionFailure("private-database")
        status, value, _ = await self.embed(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private", str(value))
        self.assertNotIn("_operation", self.docs.documents[ObjectId(body["id"])])

    async def test_openapi_requires_bearer_and_no_client_model_parameter(self):
        schema = app.openapi()
        operation = schema["paths"]["/api/documents/{document_id}/embeddings"]["post"]
        self.assertEqual(operation["security"], [{"HTTPBearer": []}])
        self.assertEqual([value["name"] for value in operation["parameters"]], ["document_id"])
        self.assertNotIn("requestBody", operation)
