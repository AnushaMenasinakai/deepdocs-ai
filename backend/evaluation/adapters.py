"""Isolated execution of the real retrieval service, with synthetic Mongo records.
Fixture mode mocks scores/inference; local-model mode uses cached weights and in-memory Qdrant.
Neither mode loads .env, user data, or a remote Qdrant URL.
"""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from bson import ObjectId
from config import EmbeddingSettings, QdrantSettings
from search_schemas import SearchRequest
import embeddings
import retrieval
from vector_store import VectorStore, payload, point_id
from .dataset import identifier


class Cursor:
    def __init__(self, values):
        self.values = iter(copy.deepcopy(values))
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return next(self.values)
        except StopIteration:
            raise StopAsyncIteration


class Records:
    def __init__(self, values):
        self.values = values
    def find(self, query):
        return Cursor([v for v in self.values if all(v.get(k) == value for k, value in query.items())])
    async def find_one(self, query):
        return next((copy.deepcopy(v) for v in self.values if all(v.get(k) == value for k, value in query.items())), None)


class FixtureModel:
    def get_sentence_embedding_dimension(self):
        return 3
    def encode(self, texts, **kwargs):
        return [[1., 0., 0.] for _ in texts]


class FixtureStore:
    collection, target = "evaluation", "isolated-fixture"
    async def search(self, vector, dimension, owner, base, top_k, generations):
        return self.points[:top_k]


async def retrieve_dataset(chunks, cases, fixtures, mode="fixture"):
    settings = EmbeddingSettings()
    client = None
    if mode == "fixture":
        model, store = FixtureModel(), FixtureStore()
    elif mode == "local-model":
        # Opt-in only. Missing cached weights fail rather than download/fallback.
        import os
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer
        from qdrant_client import AsyncQdrantClient, models
        cache = Path(__file__).resolve().parents[1] / ".cache" / "embeddings"
        model = SentenceTransformer(settings.model_name, device="cpu", cache_folder=str(cache),
                                    trust_remote_code=False, local_files_only=True)
        client = AsyncQdrantClient(location=":memory:")
        store = VectorStore(QdrantSettings("http://evaluation.invalid", collection_name="evaluation"), client)
    else:
        raise ValueError("Unknown evaluation mode")
    dimension = embeddings.model_dimension(model)
    owner, base = ObjectId(identifier("evaluation-owner")), ObjectId(identifier("evaluation-base"))
    documents, records = [], []
    for filename in sorted({c["document"] for c in chunks}):
        selected = [c for c in chunks if c["document"] == filename]
        doc_id, generation = ObjectId(identifier(filename)), ObjectId(identifier(filename+":generation:1"))
        documents.append({"_id": doc_id, "owner_id": owner, "knowledge_base_id": base, "status": "processed",
            "chunk_generation": generation, "chunk_count": len(selected), "page_count": max(c["page"] for c in selected),
            "vector_index": {"status": "indexed", "chunk_generation": generation, "chunk_count": len(selected),
                "embedding_model": settings.model_name, "embedding_dimension": dimension,
                "collection_name": store.collection, "target": store.target}})
        for index, chunk in enumerate(selected):
            records.append({"_id": ObjectId(identifier(chunk["id"])), "owner_id": owner, "knowledge_base_id": base,
                "document_id": doc_id, "generation": generation, "source_filename": filename, "text": chunk["text"],
                "chunk_index": index, "page_start": chunk["page"], "page_end": chunk["page"]})
    database = SimpleNamespace(get_collection=lambda name: {"documents": Records(documents), "document_chunks": Records(records)}[name])
    by_id = {str(c["_id"]): c for c in records}
    try:
        with patch("embeddings.load_model", return_value=model), patch("retrieval.get_vector_store", return_value=store):
            if client:
                # Isolated RAM-only collection; no Cloud payload indexes needed.
                await client.create_collection(store.collection, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
                offset = 0
                for _, vectors in embeddings.embed_batches([c["text"] for c in records], settings):
                    await store.upsert(records[offset:offset+len(vectors)], vectors, dimension)
                    offset += len(vectors)
            rankings = {}
            for case in cases:
                if mode == "fixture":
                    store.points = [SimpleNamespace(id=point_id(by_id[identifier(h["chunk"])]),
                        payload=payload(by_id[identifier(h["chunk"])]), score=h["score"]) for h in fixtures[case["id"]]]
                rankings[case["id"]] = await retrieval.search_chunks(database, owner, base,
                    SearchRequest(query=case["question"], top_k=5), settings)
            return rankings, dimension
    finally:
        if client:
            await client.close()
