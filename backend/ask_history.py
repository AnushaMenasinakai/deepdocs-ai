"""Completed public Q&A snapshots. Never consulted by retrieval or generation."""
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from rag_schemas import AskResponse
from history_schemas import HistoryResponse

COLLECTION = "ask_history"
ORDER = [("created_at", DESCENDING), ("_id", DESCENDING)]


async def ensure_history_indexes(database):
    await database.get_collection(COLLECTION).create_index(
        [("owner_id", ASCENDING), ("knowledge_base_id", ASCENDING), *ORDER],
        name="ask_history_owner_base_created")


def scope(owner_id, base_id):
    return {"owner_id": owner_id, "knowledge_base_id": base_id}


def public_history(record):
    created = record.get("created_at")
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return HistoryResponse(id=str(record["_id"]), question=record.get("question"), created_at=created,
        **{key: record.get(key) for key in ("status", "answer", "retrieved_chunk_count", "sources")},
        citation_version=record.get("citation_version", 0), claims=record.get("claims", [] if record.get("citation_version", 0) == 0 else None))


async def save_history(database, owner_id, base_id, question, result):
    # Validate before writing, and whitelist only the public finalized result.
    snapshot = AskResponse.model_validate(result).model_dump()
    if snapshot["status"] == "insufficient_context":
        snapshot.update(retrieved_chunk_count=0, sources=[])
    record = {"_id": ObjectId(), **scope(owner_id, base_id), "question": question,
              **snapshot, "created_at": datetime.now(timezone.utc)}
    await database.get_collection(COLLECTION).insert_one(record)
    # A concurrent KB deletion may have completed during inference/insertion.
    if await database.get_collection("knowledge_bases").find_one({"_id": base_id, "owner_id": owner_id}) is None:
        await database.get_collection(COLLECTION).delete_one({"_id": record["_id"], **scope(owner_id, base_id)})
        return False
    return True


async def list_history(database, owner_id, base_id, limit):
    cursor = database.get_collection(COLLECTION).find(scope(owner_id, base_id)).sort(ORDER).limit(limit)
    return [public_history(record) async for record in cursor]


async def get_history(database, owner_id, base_id, history_id):
    record = await database.get_collection(COLLECTION).find_one({"_id": history_id, **scope(owner_id, base_id)})
    return public_history(record) if record else None


async def delete_history(database, owner_id, base_id, history_id):
    result = await database.get_collection(COLLECTION).delete_one({"_id": history_id, **scope(owner_id, base_id)})
    return result.deleted_count == 1


async def delete_base_history(database, owner_id, base_id):
    await database.get_collection(COLLECTION).delete_many(scope(owner_id, base_id))
