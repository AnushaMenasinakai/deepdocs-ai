"""Read-only ASGI retrieval tests with fake inference/Qdrant and synthetic PDFs."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from qdrant_client import models
import test_embeddings as helpers
from test_knowledge_bases import request
from main import app
from auth import get_current_user
from config import EmbeddingSettings, ConfigurationError
from search_routes import search_settings
from vector_store import point_id


class SearchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = helpers.EmbeddingTests.asyncSetUp
    asyncTearDown = helpers.EmbeddingTests.asyncTearDown
    make_document = helpers.EmbeddingTests.make_document
    prepare = helpers.EmbeddingTests.prepare
    process = helpers.EmbeddingTests.process
    embed = helpers.EmbeddingTests.embed

    async def ready(self):
        body = await self.prepare()
        app.dependency_overrides[search_settings] = lambda: EmbeddingSettings()
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            self.assertEqual((await self.embed(body))[0], 200)
        return body

    async def search(self, data=None, base_id=None):
        return await request("POST", "/api/knowledge-bases/" + str(base_id or self.base_id) + "/search",
                             {"query": "JWT authentication"} if data is None else data)

    async def test_trim_default_single_embedding_safe_ranked_results_and_no_writes(self):
        await self.ready()
        before = copy.deepcopy((self.docs.documents, self.chunks.documents, self.qdrant.points))
        model = helpers.FakeModel()
        with patch("embeddings.load_model", return_value=model):
            status, body, _ = await self.search({"query": "  JWT authentication  "})
        self.assertEqual(status, 200)
        self.assertEqual(body["query"], "JWT authentication")
        self.assertEqual(model.batches, [["JWT authentication"]])
        self.assertEqual(self.qdrant.last_query["limit"], 5)
        self.assertFalse(self.qdrant.last_query["with_vectors"])
        self.assertEqual([value["rank"] for value in body["results"]], [1, 2, 3])
        self.assertEqual([value["score"] for value in body["results"]], [1., .9, .8])
        self.assertEqual(set(body["results"][0]), {"rank", "score", "document_id", "chunk_id", "chunk_index", "text", "source_filename", "page_start", "page_end"})
        self.assertEqual((self.docs.documents, self.chunks.documents, self.qdrant.points), before)
        filter_ = self.qdrant.last_query["filter"]
        self.assertEqual({condition.key: condition.match.value for condition in filter_.must},
                         {"owner_id": str(self.owner), "knowledge_base_id": str(self.base_id)})
        self.assertTrue(filter_.should)

    async def test_request_validation_boundaries_and_no_filter_injection(self):
        await self.ready()
        bad = [{}, {"query": ""}, {"query": "  "}, {"query": "x" * 1001}, {"query": 12},
               {"query": None}, {"query": "ok", "owner_id": "injected"}, {"query": "ok", "filter": {}},
               *[{"query": "ok", "top_k": value} for value in [0, 21, True, 1.5, "5", None]]]
        with patch("embeddings.load_model") as model:
            for payload in bad:
                with self.subTest(payload=payload):
                    self.assertEqual((await self.search(payload))[0], 422)
            model.assert_not_called()
        for k in (1, 20):
            with self.subTest(top_k=k), patch("embeddings.load_model", return_value=helpers.FakeModel()):
                status, body, _ = await self.search({"query": "x" * 1000, "top_k": k})
                self.assertEqual(status, 200)
                self.assertEqual(self.qdrant.last_query["limit"], k)
                self.assertLessEqual(len(body["results"]), k)

    async def test_auth_missing_and_foreign_kb_before_embedding(self):
        await self.ready()
        with patch("embeddings.load_model") as model:
            self.assertEqual((await self.search(base_id=ObjectId()))[0], 404)
            self.assertEqual((await self.search(base_id="bad"))[0], 422)
            self.current_owner = self.other
            self.assertEqual((await self.search())[0], 404)
            del app.dependency_overrides[get_current_user]
            status, _, headers = await self.search()
            self.assertEqual(status, 401)
            self.assertEqual(headers[b"www-authenticate"], b"Bearer")
            model.assert_not_called()

    async def test_no_searchable_documents_no_model_or_qdrant_calls(self):
        app.dependency_overrides[search_settings] = lambda: EmbeddingSettings()
        with patch("embeddings.load_model") as model, patch("retrieval.get_vector_store") as store:
            self.assertEqual((await self.search())[1]["results"], [])
            body = await self.prepare()
            record = self.docs.documents[ObjectId(body["id"])]
            for status in ("uploaded", "processing", "processed", "failed"):
                record["status"] = status
                self.assertEqual((await self.search())[1]["results"], [])
            model.assert_not_called()
            store.assert_not_called()

    async def test_mixed_indexed_and_unindexed(self):
        indexed = await self.ready()
        await self.prepare()
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            status, body, _ = await self.search()
        self.assertEqual(status, 200)
        self.assertEqual({value["document_id"] for value in body["results"]}, {indexed["id"]})

    async def test_stale_generations_foreign_owner_and_other_kb_excluded_in_qdrant(self):
        await self.ready()
        original = copy.deepcopy(next(iter(self.chunks.documents.values())))
        for key in ("owner_id", "knowledge_base_id", "generation", "document_id"):
            chunk = {**original, key: ObjectId()}
            await self.vector_store.upsert([chunk], [[1., 0., 0.]], 3)
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            status, body, _ = await self.search()
        self.assertEqual(len(body["results"]), 3)
        selected = [point for point in self.qdrant.points[self.vector_store.collection].values()
                    if self.qdrant.matches(point, self.qdrant.last_query["filter"])]
        self.assertEqual(len(selected), 3)

    async def test_model_target_collection_and_dimension_mismatch_excluded(self):
        document = await self.ready()
        record = self.docs.documents[ObjectId(document["id"])]
        for key, value in [("embedding_model", "other/model"), ("target", "other-endpoint"),
                           ("collection_name", "other"), ("embedding_dimension", 4),
                           ("status", "failed"), ("chunk_generation", ObjectId())]:
            original = copy.deepcopy(record["vector_index"])
            with self.subTest(key=key), patch("embeddings.load_model", return_value=helpers.FakeModel()):
                record["vector_index"][key] = value
                self.assertEqual((await self.search())[1]["results"], [])
            record["vector_index"] = original

    async def test_empty_qdrant_and_missing_collection_no_creation(self):
        await self.ready()
        self.qdrant.points[self.vector_store.collection].clear()
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            self.assertEqual((await self.search())[1]["results"], [])
            self.qdrant.collections.clear()
            self.assertEqual((await self.search())[1]["results"], [])
        self.assertEqual(self.qdrant.creates, 1)

    async def test_model_and_query_vector_failures_are_sanitized(self):
        await self.ready()
        with patch("embeddings._load_model", side_effect=RuntimeError("private-cache-path")):
            status, body, _ = await self.search()
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(body))
        for vector in [[], [1.], [True, 1., 2.], [float("nan"), 1., 2.], [float("inf"), 1., 2.], ["1", 1., 2.]]:
            model = helpers.FakeModel()
            model.encode = lambda *args, **kwargs: [vector]
            with self.subTest(vector=vector), patch("embeddings.load_model", return_value=model):
                self.assertEqual((await self.search())[0], 503)

    async def test_qdrant_failure_and_incompatible_config_sanitized(self):
        await self.ready()
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            self.qdrant.failure = RuntimeError("private-qdrant-key")
            status, body, _ = await self.search()
            self.assertEqual(status, 503)
            self.assertNotIn("private", str(body))
            self.qdrant.failure = None
            for config in [models.VectorParams(size=4, distance=models.Distance.COSINE),
                           models.VectorParams(size=3, distance=models.Distance.DOT)]:
                self.qdrant.collections[self.vector_store.collection] = config
                self.assertEqual((await self.search())[0], 503)
            self.qdrant.collections[self.vector_store.collection] = models.VectorParams(size=3, distance=models.Distance.COSINE)
            self.qdrant.query_points = AsyncMock(side_effect=RuntimeError("private-search"))
            self.assertEqual((await self.search())[0], 503)

    async def test_malformed_payload_and_scores_skipped(self):
        await self.ready()
        point = next(iter(self.qdrant.points[self.vector_store.collection].values()))
        original = copy.deepcopy(point.payload)
        bad = [{}, None, [], {**original, "text": ""}, {**original, "text": 1},
               {**original, "source_filename": None}, {**original, "chunk_id": "bad"},
               {**original, "document_id": None}, {**original, "page_start": True},
               {**original, "page_start": 0}, {**original, "page_end": -1},
               {**original, "page_end": 99}, {**original, "chunk_index": "0"},
               {**original, "chunk_index": -1}, {**original, "owner_id": str(self.other)},
               {**original, "text": "forged valid-looking text"}]
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            for payload in bad:
                with self.subTest(payload=payload):
                    self.qdrant.query_points = AsyncMock(return_value=SimpleNamespace(points=[SimpleNamespace(id=point.id, payload=payload, score=.5)]))
                    self.assertEqual((await self.search())[1]["results"], [])
            for score in [None, "0.5", True, float("nan"), float("inf"), -float("inf"), 10 ** 1000]:
                with self.subTest(score=score):
                    self.qdrant.query_points = AsyncMock(return_value=SimpleNamespace(points=[SimpleNamespace(id=point.id, payload=original, score=score)]))
                    self.assertEqual((await self.search())[1]["results"], [])

    async def test_recheck_concurrent_reprocess_delete_and_reindex(self):
        document = await self.ready()
        record = self.docs.documents[ObjectId(document["id"])]
        original = copy.deepcopy(record)
        query = self.qdrant.query_points
        for change in ("lock", "generation", "delete", "indexing"):
            async def raced(*args, **kwargs):
                result = await query(*args, **kwargs)
                if change == "delete":
                    del self.docs.documents[ObjectId(document["id"])]
                elif change == "lock":
                    record["_operation"] = ObjectId()
                elif change == "generation":
                    record["chunk_generation"] = ObjectId()
                else:
                    record["vector_index"]["status"] = "indexing"
                return result
            with self.subTest(change=change), patch("embeddings.load_model", return_value=helpers.FakeModel()):
                self.qdrant.query_points = raced
                self.assertEqual((await self.search())[1]["results"], [])
            record = copy.deepcopy(original)
            self.docs.documents[ObjectId(document["id"])] = record

    async def test_mongo_and_config_failures_safe(self):
        await self.ready()
        for collection in (self.bases, self.docs, self.chunks):
            with self.subTest(collection=collection), patch("embeddings.load_model", return_value=helpers.FakeModel()):
                collection.failure = ConnectionFailure("private-mongodb")
                status, body, _ = await self.search()
                self.assertEqual(status, 503)
                self.assertNotIn("private", str(body))
                collection.failure = None
        app.dependency_overrides.pop(search_settings)
        with patch("search_routes.load_embedding_settings", side_effect=ConfigurationError("private-config")):
            self.assertEqual((await self.search())[0], 503)

    async def test_openapi_public_safe_schema(self):
        schema = app.openapi()
        op = schema["paths"]["/api/knowledge-bases/{knowledge_base_id}/search"]["post"]
        self.assertEqual(op["security"], [{"HTTPBearer": []}])
        self.assertEqual(set(schema["components"]["schemas"]["SearchRequest"]["properties"]), {"query", "top_k"})


    async def test_unsorted_and_duplicate_results_are_ranked_without_threshold(self):
        await self.ready()
        points = list(self.qdrant.points[self.vector_store.collection].values())
        response = [SimpleNamespace(id=point.id, payload=point.payload, score=score)
                    for point, score in zip(points, [-.3, .1, .8])]
        response.append(copy.deepcopy(response[0]))
        self.qdrant.query_points = AsyncMock(return_value=SimpleNamespace(points=response))
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            values = (await self.search())[1]["results"]
        self.assertEqual([value["score"] for value in values], [.8, .1, -.3])
        self.assertEqual([value["rank"] for value in values], [1, 2, 3])

    async def test_corrupt_entire_response_is_safe_service_error(self):
        await self.ready()
        self.qdrant.query_points = AsyncMock(return_value=SimpleNamespace(points=None))
        with patch("embeddings.load_model", return_value=helpers.FakeModel()):
            self.assertEqual((await self.search())[0], 503)

    async def test_excessive_scope_rejected_without_query_or_model(self):
        await self.ready()
        with patch("retrieval.MAX_SEARCHABLE_DOCUMENTS", 0), patch("embeddings.load_model") as model:
            self.assertEqual((await self.search())[0], 503)
            model.assert_not_called()

    async def test_official_local_client_filtered_read_only_query(self):
        import warnings
        from qdrant_client import AsyncQdrantClient
        from vector_store import VectorStore
        from config import QdrantSettings
        document = await self.ready()
        client = AsyncQdrantClient(location=":memory:")
        store = VectorStore(QdrantSettings("http://localhost:6333"), client)
        chunks = list(self.chunks.documents.values())
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Payload indexes have no effect.*")
                await store.ensure_collection(3)
            await store.upsert(chunks, [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], 3)
            foreign = {**chunks[0], "owner_id": self.other}
            await store.upsert([foreign], [[1., 0., 0.]], 3)
            other_kb = {**chunks[0], "knowledge_base_id": ObjectId()}
            await store.upsert([other_kb], [[1., 0., 0.]], 3)
            old = {**chunks[0], "generation": ObjectId()}
            await store.upsert([old], [[1., 0., 0.]], 3)
            with patch("vector_store._store", store), patch("embeddings.load_model", return_value=helpers.FakeModel()):
                status, body, _ = await self.search()
            self.assertEqual(status, 200)
            self.assertEqual(len(body["results"]), 3)
            self.assertEqual([value["chunk_index"] for value in body["results"]], [2, 1, 0])
            self.assertEqual(await store.count(self.docs.documents[ObjectId(document["id"])]), 4)
        finally:
            await client.close()
