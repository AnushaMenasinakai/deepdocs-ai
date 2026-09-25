"""Owner-scoped operations using the existing shared MongoDB database."""
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from knowledge_base_schemas import KnowledgeBaseResponse

LIST_ORDER = [("updated_at", DESCENDING), ("_id", DESCENDING)]


async def ensure_knowledge_base_indexes(database):
    await database.get_collection("knowledge_bases").create_index(
        [("owner_id", ASCENDING), *LIST_ORDER],
        name="knowledge_bases_owner_updated",
    )


def public_knowledge_base(document):
    # BSON dates may be decoded as naive UTC by the shared client.
    dates = {
        key: value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        for key, value in ((key, document[key]) for key in ("created_at", "updated_at"))
    }
    return KnowledgeBaseResponse(
        id=str(document["_id"]), name=document["name"],
        description=document.get("description"), **dates,
    )


async def create_knowledge_base(database, owner_id, data):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(), "owner_id": owner_id,
        "name": data.name, "description": data.description,
        "created_at": now, "updated_at": now,
    }
    await database.get_collection("knowledge_bases").insert_one(document)
    return public_knowledge_base(document)


async def list_knowledge_bases(database, owner_id):
    cursor = database.get_collection("knowledge_bases").find(
        {"owner_id": owner_id}
    ).sort(LIST_ORDER)
    return [public_knowledge_base(document) async for document in cursor]


async def get_knowledge_base(database, owner_id, knowledge_base_id):
    document = await database.get_collection("knowledge_bases").find_one(
        {"_id": knowledge_base_id, "owner_id": owner_id}
    )
    return public_knowledge_base(document) if document is not None else None


async def update_knowledge_base(database, owner_id, knowledge_base_id, data):
    changes = data.model_dump(exclude_unset=True)
    changes["updated_at"] = datetime.now(timezone.utc)
    document = await database.get_collection("knowledge_bases").find_one_and_update(
        {"_id": knowledge_base_id, "owner_id": owner_id},
        {"$set": changes}, return_document=ReturnDocument.AFTER,
    )
    return public_knowledge_base(document) if document is not None else None


class KnowledgeBaseHasDocuments(ValueError):
    pass


async def delete_knowledge_base(database, owner_id, knowledge_base_id):
    bases = database.get_collection("knowledge_bases")
    owned = {"_id": knowledge_base_id, "owner_id": owner_id}
    base = await bases.find_one(owned)
    if base is None:
        return False
    document = await database.get_collection("documents").find_one(
        {"knowledge_base_id": knowledge_base_id, "owner_id": owner_id}
    )
    if document is not None or base.get("_document_ids"):
        raise KnowledgeBaseHasDocuments()
    # Reservations prevent upload/delete races. Revision also detects an entire
    # upload/delete cycle occurring between the reads and this conditional delete.
    result = await bases.delete_one({
        **owned, "_document_ids.0": {"$exists": False},
        "_document_revision": base.get("_document_revision", {"$exists": False}),
    })
    if result.deleted_count == 1:
        return True
    if await bases.find_one(owned) is not None:
        raise KnowledgeBaseHasDocuments()
    return False
