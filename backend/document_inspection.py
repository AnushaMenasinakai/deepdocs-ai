"""Read-only, point-in-time operational metadata; never a recovery authorization."""
from bson import ObjectId
from dashboard import indexed_expression
from document_schemas import OperationStatusResponse


def classify(document_id, snapshot):
    state = snapshot.get("status")
    known = state if state in ("uploaded", "processing", "processed", "failed") else "unknown"

    def result(document_state, attention, action, operation="idle"):
        return OperationStatusResponse(document_id=str(document_id), operation_state=operation,
            document_state=document_state, attention=attention, recommended_action=action)

    # Presence, including null/malformed legacy tokens, is what the claim boundary uses.
    if snapshot["claimed"]:
        return result(known, "outcome_uncertain", "refresh", "claimed_unknown")
    if snapshot["indexed"]:
        return result("indexed", "none", "none")
    review = result("unknown", "requires_review", "contact_support")
    if any(snapshot.get(key) not in ("missing", "object") for key in ("embedding_type", "vector_type")):
        return review
    embedding = snapshot.get("embedding", {})
    vector = snapshot.get("vector_index", {})
    if not isinstance(embedding, dict) or not isinstance(vector, dict):
        return review
    e, v = embedding.get("status"), vector.get("status")
    if e not in (None, "not_generated", "generating", "generated", "failed") or v not in (
            None, "not_generated", "indexing", "indexed", "stale", "failed"):
        return review
    if state == "failed":
        # Processing and incomplete deletion share this state. Do not guess a retry.
        return result("failed", "requires_review", "contact_support")
    if state == "uploaded" and e in (None, "not_generated") and v in (None, "not_generated"):
        if "chunk_generation" not in snapshot and snapshot.get("chunk_count", 0) == 0:
            return result("uploaded", "needs_processing", "process")
    if state != "processed" or not isinstance(snapshot.get("chunk_generation"), ObjectId):
        return review
    if type(snapshot.get("chunk_count")) is not int or snapshot["chunk_count"] <= 0:
        return review
    # Unlocked in-progress states and contradictory success metadata need review.
    if e == "generating" or v == "indexing" or v == "indexed":
        return review
    if e == "failed" or v == "failed":
        return result("failed", "needs_reindex", "reindex")
    if e in (None, "not_generated", "generated") and v in (None, "not_generated", "stale"):
        return result("processed", "needs_reindex", "reindex")
    return review


async def inspect_operation(database, owner_id, document_id):
    # One document snapshot: no separate reads that could mix lifecycle generations.
    # Reuse the exact Dashboard/management Indexed expression, evaluated in MongoDB.
    cursor = await database.get_collection("documents").aggregate([
        {"$match": {"_id": document_id, "owner_id": owner_id}},
        {"$limit": 1},
        {"$project": {"_id": 0, "status": 1, "chunk_generation": 1, "chunk_count": 1,
            "embedding.status": 1, "vector_index.status": 1,
            "embedding_type": {"$type": "$embedding"}, "vector_type": {"$type": "$vector_index"},
            "claimed": {"$ne": [{"$type": "$_operation"}, "missing"]},
            "indexed": indexed_expression()}},
    ], maxTimeMS=5000)
    rows = await cursor.to_list(length=1)
    return classify(document_id, rows[0]) if rows else None
