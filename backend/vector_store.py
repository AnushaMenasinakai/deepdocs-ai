"""Official Qdrant client boundary. No search operations or secret diagnostics."""
import hashlib
from uuid import UUID, uuid5
from contextlib import asynccontextmanager
from config import load_qdrant_settings
from embeddings import validate_vector

_NAMESPACE = UUID("7f58aaec-436d-43db-8cd5-cb3132483323")
_store = None


class VectorFailure(ValueError):
    pass


@asynccontextmanager
async def safe_vector_errors():
    try:
        yield
    except Exception:
        raise VectorFailure("Vector service is temporarily unavailable or incompatible.") from None


def point_id(chunk):
    return str(uuid5(_NAMESPACE, ":".join(str(chunk[key]) for key in
               ("owner_id", "knowledge_base_id", "document_id", "generation", "_id"))))


def payload(chunk):
    return {**{key: str(chunk[key]) for key in
               ("owner_id", "knowledge_base_id", "document_id")},
            "chunk_id": str(chunk["_id"]), "chunk_generation": str(chunk["generation"]),
            **{key: chunk[key] for key in
               ("chunk_index", "source_filename", "page_start", "page_end", "text")}}


def document_filter(document, generation=None):
    from qdrant_client import models
    values = {"owner_id": document["owner_id"], "knowledge_base_id": document["knowledge_base_id"],
              "document_id": document["_id"]}
    if generation is not None:
        values["chunk_generation"] = generation
    return models.Filter(must=[models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))
                               for key, value in values.items()])


class VectorStore:
    def __init__(self, settings, client=None):
        if client is None:
            from qdrant_client import AsyncQdrantClient
            client = AsyncQdrantClient(url=settings.url, api_key=settings.api_key,
                                       timeout=15, check_compatibility=False)
        self.client = client
        self.collection = settings.collection_name
        self.target = hashlib.sha256(settings.url.encode()).hexdigest()

    def check_target(self, state):
        # Never silently abandon a prior endpoint when runtime config changes.
        if state.get("target") and state["target"] != self.target:
            raise VectorFailure("Restore the previous vector service configuration before cleanup.")

    async def health(self):
        async with safe_vector_errors():
            await self.client.get_collections()

    async def ensure_collection(self, dimension):
        from qdrant_client import models
        async with safe_vector_errors():
            if not await self.client.collection_exists(self.collection):
                try:
                    await self.client.create_collection(self.collection,
                        vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
                except Exception:
                    # Another worker may have created it. Inspect, never recreate.
                    if not await self.client.collection_exists(self.collection):
                        raise
            info = await self.client.get_collection(self.collection)
            config = info.config.params.vectors
            if not isinstance(config, models.VectorParams) or config.size != dimension or config.distance != models.Distance.COSINE:
                raise VectorFailure()
            # Required for filtered count/delete on strict-mode Cloud collections.
            for key in ("owner_id", "knowledge_base_id", "document_id", "chunk_generation"):
                await self.client.create_payload_index(self.collection, key,
                    field_schema=models.PayloadSchemaType.KEYWORD, wait=True)

    async def upsert(self, chunks, vectors, dimension):
        from qdrant_client import models
        async with safe_vector_errors():
            if len(chunks) != len(vectors):
                raise ValueError
            points = [models.PointStruct(id=point_id(chunk), vector=validate_vector(vector, dimension),
                                         payload=payload(chunk)) for chunk, vector in zip(chunks, vectors)]
            result = await self.client.upsert(self.collection, points=points, wait=True)
            if result.status != models.UpdateStatus.COMPLETED:
                raise VectorFailure()

    async def count(self, document, generation=None, collection=None):
        async with safe_vector_errors():
            name = collection or self.collection
            if not await self.client.collection_exists(name):
                return 0
            result = await self.client.count(name, count_filter=document_filter(document, generation), exact=True)
            return result.count

    async def delete_document(self, document, collection=None):
        from qdrant_client import models
        async with safe_vector_errors():
            name = collection or self.collection
            if not await self.client.collection_exists(name):
                return
            result = await self.client.delete(name, points_selector=models.FilterSelector(
                filter=document_filter(document)), wait=True)
            if result.status != models.UpdateStatus.COMPLETED or await self.count(document, collection=name) != 0:
                raise VectorFailure()


def get_vector_store():
    global _store
    try:
        if _store is None:
            _store = VectorStore(load_qdrant_settings())
        return _store
    except Exception:
        raise VectorFailure("Vector service configuration is unavailable.") from None


async def close_vector_store():
    global _store
    store, _store = _store, None
    if store is not None:
        try:
            await store.client.close()
        except Exception:
            pass


async def cleanup_document_vectors(document):
    state = document.get("vector_index", {})
    if not state.get("collection_name"):
        return
    store = get_vector_store()
    store.check_target(state)
    await store.delete_document(document, state["collection_name"])
