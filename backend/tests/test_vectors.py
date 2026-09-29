"""Offline Qdrant boundary and lifecycle tests; no credentials or servers."""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from uuid import UUID
from bson import ObjectId
from qdrant_client import models
from pymongo.errors import ConnectionFailure
from config import QdrantSettings, load_qdrant_settings, ConfigurationError
from vector_store import VectorStore, VectorFailure, point_id, payload, get_vector_store, close_vector_store
from qdrant_helpers import FakeQdrant
import test_embeddings as embedding_helpers
from test_documents import request
from main import app
from auth import get_current_user


class VectorBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = FakeQdrant()
        self.store = VectorStore(QdrantSettings("http://localhost:6333"), self.client)

    async def test_config_and_secret_safe_repr(self):
        with patch("config.dotenv_values", return_value={"QDRANT_URL": "https://example.test", "QDRANT_API_KEY": "fake-key"}), patch.dict(os.environ, {}, clear=True):
            value = load_qdrant_settings()
            self.assertEqual(value.collection_name, "deepdocs_chunks")
            self.assertNotIn("fake-key", repr(value))
            self.assertNotIn("example.test", repr(value))
        for url in ["", "http://example.test", "https://user:pass@example.test", "https://example.test?key=private", "file:///private", "https://example.test/path", "https://example.test:99999"]:
            with self.subTest(url=url), patch("config.dotenv_values", return_value={"QDRANT_URL": url}), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as error:
                    load_qdrant_settings()
                self.assertNotIn("private", str(error.exception))
        with patch("config.dotenv_values", return_value={"QDRANT_URL": "http://localhost:6333"}), patch.dict(os.environ, {"QDRANT_COLLECTION_NAME": "override"}, clear=True):
            self.assertEqual(load_qdrant_settings().collection_name, "override")

    async def test_collection_create_reuse_and_incompatible_configs(self):
        await self.store.ensure_collection(3)
        await self.store.ensure_collection(3)
        self.assertEqual(self.client.creates, 1)
        for config in [models.VectorParams(size=4, distance=models.Distance.COSINE),
                       models.VectorParams(size=3, distance=models.Distance.DOT), {"named": models.VectorParams(size=3, distance=models.Distance.COSINE)}]:
            with self.subTest(config=config):
                self.client.collections[self.store.collection] = config
                with self.assertRaises(VectorFailure):
                    await self.store.ensure_collection(3)
                self.assertIs(self.client.collections[self.store.collection], config)
        self.assertEqual(self.client.creates, 1)

    async def test_creation_failure_and_auth_errors_sanitized(self):
        self.client.create_collection = AsyncMock(side_effect=RuntimeError("private-key"))
        with self.assertRaises(VectorFailure) as error:
            await self.store.ensure_collection(3)
        self.assertNotIn("private", str(error.exception))
        self.client.failure = RuntimeError("401 private-key")
        with self.assertRaises(VectorFailure):
            await self.store.health()

    async def test_concurrent_collection_creation_rechecks_config(self):
        original = self.client.create_collection
        async def race(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("already exists")
        self.client.create_collection = race
        await self.store.ensure_collection(3)

    async def test_client_reused_closed_and_configuration_error_safe(self):
        with patch("vector_store._store", None), patch("vector_store.load_qdrant_settings", return_value=QdrantSettings("http://localhost:6333")), patch("vector_store.VectorStore", return_value=self.store) as factory:
            self.assertIs(get_vector_store(), get_vector_store())
            factory.assert_called_once()
            await close_vector_store()
            self.assertTrue(self.client.closed)
        with patch("vector_store._store", None), patch("vector_store.load_qdrant_settings", side_effect=ConfigurationError("private-key")):
            with self.assertRaises(VectorFailure) as error:
                get_vector_store()
            self.assertNotIn("private", str(error.exception))

    async def test_incomplete_upsert_ack_and_bad_vectors_rejected(self):
        await self.store.ensure_collection(3)
        chunk = {key: ObjectId() for key in ("_id", "owner_id", "knowledge_base_id", "document_id", "generation")}
        chunk.update(chunk_index=0, source_filename="test.pdf", page_start=1, page_end=1, text="Text")
        with self.assertRaises(VectorFailure):
            await self.store.upsert([chunk], [[float("nan"), 1, 2]], 3)
        self.client.upsert = AsyncMock(return_value=SimpleNamespace(status=models.UpdateStatus.ACKNOWLEDGED))
        with self.assertRaises(VectorFailure):
            await self.store.upsert([chunk], [[1., 0., 0.]], 3)


class VectorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = embedding_helpers.EmbeddingTests.asyncSetUp
    asyncTearDown = embedding_helpers.EmbeddingTests.asyncTearDown
    prepare = embedding_helpers.EmbeddingTests.prepare
    make_document = embedding_helpers.EmbeddingTests.make_document
    process = embedding_helpers.EmbeddingTests.process
    embed = embedding_helpers.EmbeddingTests.embed

    async def index(self):
        body = await self.prepare()
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        return body

    async def status(self, body):
        return await request("GET", "/api/documents/" + body["id"] + "/vector-status")

    async def test_ids_payload_counts_repeat_and_response(self):
        body = await self.index()
        points = self.qdrant.points[self.vector_store.collection]
        self.assertEqual(len(points), 3)
        ids = set(points)
        for chunk in self.chunks.documents.values():
            identifier = point_id(chunk)
            self.assertEqual(str(UUID(identifier)), identifier)
            self.assertEqual(identifier, point_id(copy.deepcopy(chunk)))
            self.assertEqual(points[identifier].payload, payload(chunk))
            self.assertEqual(set(points[identifier].payload), {"owner_id", "knowledge_base_id", "document_id", "chunk_id", "chunk_index", "chunk_generation", "source_filename", "page_start", "page_end", "text"})
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            status, value, _ = await self.embed(body)
        self.assertEqual(status, 200)
        self.assertEqual(value["vector_status"], "indexed")
        self.assertNotIn("vector", value)
        self.assertEqual(set(self.qdrant.points[self.vector_store.collection]), ids)
        status, value, _ = await self.status(body)
        self.assertTrue(value["synchronized"])
        self.assertEqual(value["stored_vector_count"], 3)
        self.assertNotIn("owner_id", value)

    async def test_cross_user_cannot_index_inspect_delete(self):
        body = await self.index()
        ids = set(self.qdrant.points[self.vector_store.collection])
        self.current_owner = self.other
        self.assertEqual((await self.embed(body))[0], 404)
        self.assertEqual((await self.status(body))[0], 404)
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 404)
        self.assertEqual(set(self.qdrant.points[self.vector_store.collection]), ids)
        del app.dependency_overrides[get_current_user]
        self.assertEqual((await self.status(body))[0], 401)

    async def test_partial_failure_state_and_retry_cleanup(self):
        body = await self.prepare()
        self.qdrant.fail_upsert = True
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            status, value, _ = await self.embed(body)
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(value))
            state = self.docs.documents[ObjectId(body["id"])]["vector_index"]
            self.assertEqual(state["status"], "failed")
            self.assertFalse((await self.status(body))[1]["synchronized"])
            self.qdrant.fail_upsert = False
            self.assertEqual((await self.embed(body))[0], 200)
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 3)

    async def test_mongo_failure_after_vectors_preserves_cleanup_location(self):
        body = await self.prepare()
        original = self.docs.update_one
        async def fail(query, update):
            if update.get("$set", {}).get("vector_index", {}).get("status") == "indexed":
                raise ConnectionFailure("private-mongo")
            return await original(query, update)
        self.docs.update_one = fail
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 503)
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 3)
        state = self.docs.documents[ObjectId(body["id"])]["vector_index"]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["collection_name"], self.vector_store.collection)
        self.assertFalse((await self.status(body))[1]["synchronized"])
        self.docs.update_one = original
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)
        self.assertFalse(self.qdrant.points[self.vector_store.collection])

    async def test_reprocess_fewer_chunks_removes_old_vectors(self):
        body = await self.index()
        old_generation = self.docs.documents[ObjectId(body["id"])]["chunk_generation"]
        with patch("document_processing.extract_chunks", return_value=(1, [{"text": "new text", "chunk_index": 0, "page_start": 1, "page_end": 1, "character_count": 8}])):
            self.assertEqual((await self.process(body))[0], 200)
        self.assertFalse(self.qdrant.points[self.vector_store.collection])
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertNotEqual(record["chunk_generation"], old_generation)
        self.assertEqual(record["vector_index"]["status"], "not_generated")
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 1)

    async def test_reprocess_cleanup_failure_preserves_old_chunks_and_reports_stale(self):
        body = await self.index()
        old_chunks = copy.deepcopy(self.chunks.documents)
        self.qdrant.fail_delete = True
        self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual(self.chunks.documents, old_chunks)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertEqual(record["vector_index"]["status"], "stale")
        self.assertEqual(record["status"], "failed")
        self.assertFalse((await self.status(body))[1]["synchronized"])
        self.qdrant.fail_delete = False
        self.assertEqual((await self.process(body))[0], 200)
        self.assertFalse(self.qdrant.points[self.vector_store.collection])

    async def test_delete_failure_preserves_file_metadata_then_retry_cleans_only_owned(self):
        body = await self.index()
        chunk = copy.deepcopy(next(iter(self.chunks.documents.values())))
        chunk["owner_id"] = self.other
        await self.vector_store.upsert([chunk], [[1., 0., 0.]], 3)
        foreign = point_id(chunk)
        self.qdrant.fail_delete = True
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertTrue((self.root / (body["id"] + ".pdf")).exists())
        self.assertIn(ObjectId(body["id"]), self.docs.documents)
        self.assertFalse((await self.status(body))[1]["synchronized"])
        self.qdrant.fail_delete = False
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)
        self.assertEqual(set(self.qdrant.points[self.vector_store.collection]), {foreign})
        self.assertFalse(self.chunks.documents)
        self.assertFalse((self.root / (body["id"] + ".pdf")).exists())

    async def test_unavailable_health_index_and_status_are_safe(self):
        body = await self.index()
        self.assertEqual((await request("GET", "/api/health/qdrant"))[0], 200)
        self.qdrant.failure = RuntimeError("private-api-key")
        for method, path in [("GET", "/api/health/qdrant"), ("POST", "/api/documents/" + body["id"] + "/embeddings")]:
            with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
                status, value, _ = await request(method, path)
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(value))
        self.assertEqual((await self.status(body))[0], 503)

    async def test_deleted_collection_and_count_mismatch_not_synchronized(self):
        body = await self.index()
        self.qdrant.points[self.vector_store.collection].popitem()
        self.assertFalse((await self.status(body))[1]["synchronized"])
        self.qdrant.collections.clear()
        self.assertEqual((await self.status(body))[1]["stored_vector_count"], 0)
        self.assertFalse((await self.status(body))[1]["synchronized"])

    async def test_target_change_refused_without_losing_cleanup_location(self):
        model_patch = patch("embeddings.load_model", return_value=embedding_helpers.FakeModel())
        model_patch.start()
        self.addCleanup(model_patch.stop)
        body = await self.index()
        old = self.docs.documents[ObjectId(body["id"])]["vector_index"]["target"]
        self.vector_store.target = "changed-endpoint"
        self.assertEqual((await self.embed(body))[0], 503)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["vector_index"]["target"], old)
        self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertEqual(len(self.qdrant.points[self.vector_store.collection]), 3)


    async def test_wrong_persistence_count_never_marks_indexed(self):
        body = await self.prepare()
        original = self.vector_store.count
        async def wrong_count(document, generation=None, collection=None):
            if generation is not None:
                return 0
            return await original(document, generation, collection)
        self.vector_store.count = wrong_count
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 503)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["vector_index"]["status"], "failed")

    async def test_different_document_and_base_points_survive_cleanup(self):
        body = await self.index()
        original = copy.deepcopy(next(iter(self.chunks.documents.values())))
        foreign_ids = set()
        for key in ("document_id", "knowledge_base_id"):
            chunk = {**original, key: ObjectId()}
            await self.vector_store.upsert([chunk], [[1., 0., 0.]], 3)
            foreign_ids.add(point_id(chunk))
        self.assertEqual((await self.process(body))[0], 200)
        self.assertEqual(set(self.qdrant.points[self.vector_store.collection]), foreign_ids)

    async def test_collection_change_cleans_previous_collection(self):
        body = await self.index()
        old_name = self.vector_store.collection
        self.vector_store.collection = "replacement"
        with patch("embeddings.load_model", return_value=embedding_helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        self.assertFalse(self.qdrant.points[old_name])
        self.assertEqual(len(self.qdrant.points["replacement"]), 3)

    async def test_official_in_memory_client_without_network(self):
        import warnings
        from qdrant_client import AsyncQdrantClient
        client = AsyncQdrantClient(location=":memory:")
        store = VectorStore(QdrantSettings("http://localhost:6333"), client)
        body = await self.prepare()
        document = self.docs.documents[ObjectId(body["id"])]
        chunks = list(self.chunks.documents.values())
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")
                await store.ensure_collection(3)
            await store.upsert(chunks, [[1., 0., 0.]] * len(chunks), 3)
            self.assertEqual(await store.count(document), 3)
            await store.upsert(chunks, [[1., 0., 0.]] * len(chunks), 3)
            self.assertEqual(await store.count(document), 3)
            await store.delete_document(document)
            self.assertEqual(await store.count(document), 0)
        finally:
            await client.close()
