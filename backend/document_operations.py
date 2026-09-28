"""Atomic MongoDB operation claims shared by processing and deletion."""
from bson import ObjectId


class DocumentBusy(ValueError):
    pass


async def claim_document(database, owner_id, document_id):
    collection = database.get_collection("documents")
    token = ObjectId()
    owned = {"_id": document_id, "owner_id": owner_id}
    document = await collection.find_one_and_update(
        {**owned, "_operation": {"$exists": False}},
        {"$set": {"_operation": token}},
    )
    if document is None and await collection.find_one(owned) is not None:
        raise DocumentBusy()
    return document, token


async def release_document(database, owner_id, document_id, token):
    await database.get_collection("documents").update_one(
        {"_id": document_id, "owner_id": owner_id, "_operation": token},
        {"$unset": {"_operation": ""}},
    )
