"""Read-only retrieval with MongoDB lifecycle checks around Qdrant queries."""
import math
from numbers import Real
from bson import ObjectId
import embeddings
from vector_store import get_vector_store, VectorFailure, point_id


MAX_SEARCHABLE_DOCUMENTS = 1000


def eligible(document, settings, store=None, dimension=None):
    state = document.get("vector_index", {})
    generation = document.get("chunk_generation")
    return (document.get("status") == "processed" and "_operation" not in document
            and isinstance(generation, ObjectId) and state.get("status") == "indexed"
            and state.get("chunk_generation") == generation
            and state.get("embedding_model") == settings.model_name
            and type(document.get("chunk_count")) is int and document["chunk_count"] > 0
            and state.get("chunk_count") == document["chunk_count"]
            and (store is None or (state.get("collection_name") == store.collection and state.get("target") == store.target))
            and (dimension is None or state.get("embedding_dimension") == dimension))


def validated_payload(point, owner_id, base_id, documents):
    """Reject external malformed/foreign data without echoing it or crashing."""
    value = getattr(point, "payload", None)
    score = getattr(point, "score", None)
    if not isinstance(value, dict) or isinstance(score, bool) or not isinstance(score, Real):
        return None
    try:
        if not math.isfinite(score):
            return None
    except (OverflowError, ValueError, TypeError):
        return None
    if value.get("owner_id") != str(owner_id) or value.get("knowledge_base_id") != str(base_id):
        return None
    for key in ("document_id", "chunk_id", "chunk_generation"):
        if not isinstance(value.get(key), str) or not ObjectId.is_valid(value[key]):
            return None
    document = documents.get(value["document_id"])
    if document is None or value["chunk_generation"] != str(document["chunk_generation"]):
        return None
    if any(type(value.get(key)) is not int for key in ("chunk_index", "page_start", "page_end")):
        return None
    if not (0 <= value["chunk_index"] < document["chunk_count"] and
            1 <= value["page_start"] <= value["page_end"] <= document.get("page_count", 0)):
        return None
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in ("text", "source_filename")):
        return None
    return value


async def search_chunks(database, owner_id, base_id, data, settings):
    docs = database.get_collection("documents")
    candidates = []
    async for document in docs.find({"owner_id": owner_id, "knowledge_base_id": base_id}):
        if eligible(document, settings):
            candidates.append(document)
            if len(candidates) > MAX_SEARCHABLE_DOCUMENTS:
                raise VectorFailure("Search scope is too large.")
    if not candidates:
        return []
    store = get_vector_store()
    candidates = [document for document in candidates if eligible(document, settings, store)]
    if not candidates:
        return []
    # Reuses the cached provider. Exactly one encode call for the trimmed query.
    dimension = embeddings.model_dimension(embeddings.load_model(settings.model_name))
    vector = embeddings.validate_vector(embeddings.embed_text(data.query, settings), dimension)
    candidates = [document for document in candidates if eligible(document, settings, store, dimension)]
    if not candidates:
        return []
    snapshots = {str(document["_id"]): document for document in candidates}
    points = await store.search(vector, dimension, owner_id, base_id, data.top_k,
                                [(doc["_id"], doc["chunk_generation"]) for doc in candidates])
    results, seen = [], set()
    # At most top_k external hits, no unbounded backfill for rejected payloads.
    for point in points[:data.top_k]:
        value = validated_payload(point, owner_id, base_id, snapshots)
        if value is None or value["chunk_id"] in seen:
            continue
        # The payload must match the authoritative active chunk. This also rejects
        # valid-looking forged IDs, text, and provenance from corrupted points.
        chunk = await database.get_collection("document_chunks").find_one({
            "_id": ObjectId(value["chunk_id"]), "owner_id": owner_id,
            "knowledge_base_id": base_id, "document_id": ObjectId(value["document_id"]),
            "generation": ObjectId(value["chunk_generation"]),
        })
        if chunk is None or str(getattr(point, "id", None)) != point_id(chunk):
            continue
        keys = ("chunk_index", "text", "source_filename", "page_start", "page_end")
        if any(value[key] != chunk.get(key) for key in keys):
            continue
        seen.add(value["chunk_id"])
        results.append({"score": float(point.score), "document_id": value["document_id"],
                        "chunk_id": value["chunk_id"], **{key: value[key] for key in keys}})
    # No writes/locks: discard hits invalidated by deletion, reprocessing, or
    # re-indexing while Qdrant/chunk reads were in flight.
    current = set()
    for identifier in {value["document_id"] for value in results}:
        latest = await docs.find_one({"_id": ObjectId(identifier), "owner_id": owner_id, "knowledge_base_id": base_id})
        if (latest is not None and eligible(latest, settings, store, dimension)
                and latest["vector_index"] == snapshots[identifier]["vector_index"]):
            current.add(identifier)
    results = [value for value in results if value["document_id"] in current]
    results.sort(key=lambda value: (-value["score"], value["document_id"], value["chunk_index"], value["chunk_id"]))
    return [{"rank": rank, **value} for rank, value in enumerate(results, 1)]
