"""Owner-scoped metadata operations and compensating filesystem cleanup."""
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from fastapi.concurrency import run_in_threadpool
from document_schemas import DocumentResponse
from document_storage import StorageCleanupError

ORDER = [("created_at", -1), ("_id", -1)]


async def ensure_document_indexes(database):
    await database.get_collection("documents").create_index(
        [("owner_id", 1), ("knowledge_base_id", 1), *ORDER],
        name="documents_owner_base_created",
    )


def public_document(document):
    dates = {key: document[key].replace(tzinfo=timezone.utc)
             if document[key].tzinfo is None else document[key]
             for key in ("created_at", "updated_at")}
    return DocumentResponse(
        id=str(document["_id"]), knowledge_base_id=str(document["knowledge_base_id"]),
        filename=document["original_filename"], content_type=document["content_type"],
        file_size=document["file_size"], status=document["status"], **dates,
    )


async def release_reservation(database, owner_id, base_id, document_id):
    await database.get_collection("knowledge_bases").update_one(
        {"_id": base_id, "owner_id": owner_id},
        {"$pull": {"_document_ids": document_id}, "$inc": {"_document_revision": 1}},
    )


async def upload_document(database, storage, owner_id, base_id, upload, max_bytes):
    document_id = ObjectId()
    # Atomic reservation conflicts with a concurrent conditional KB deletion.
    base = await database.get_collection("knowledge_bases").find_one_and_update(
        {"_id": base_id, "owner_id": owner_id},
        {"$addToSet": {"_document_ids": document_id}, "$inc": {"_document_revision": 1}},
    )
    if base is None:
        return None
    stored = None
    try:
        write = asyncio.create_task(run_in_threadpool(storage.save, upload, document_id, max_bytes))
        try:
            stored = await asyncio.shield(write)
        except asyncio.CancelledError:
            # A cancelled await does not stop a filesystem worker thread. Wait
            # for its result before compensation or closing the upload stream.
            stored = await write
            raise
        now = datetime.now(timezone.utc)
        document = {
            "_id": document_id, "knowledge_base_id": base_id, "owner_id": owner_id,
            **stored, "status": "uploaded", "created_at": now, "updated_at": now,
        }
        await database.get_collection("documents").insert_one(document)
    except BaseException as error:
        # Retain the reservation if file cleanup fails: KB deletion stays blocked.
        if isinstance(error, StorageCleanupError):
            raise
        if stored:
            await run_in_threadpool(storage.delete, {"_id": document_id, **stored})
        await release_reservation(database, owner_id, base_id, document_id)
        raise
    return public_document(document)


async def list_documents(database, owner_id, base_id):
    cursor = database.get_collection("documents").find(
        {"owner_id": owner_id, "knowledge_base_id": base_id}
    ).sort(ORDER)
    return [public_document(document) async for document in cursor]


async def find_document(database, owner_id, document_id):
    return await database.get_collection("documents").find_one(
        {"_id": document_id, "owner_id": owner_id}
    )


async def delete_document(database, storage, owner_id, document_id):
    document = await find_document(database, owner_id, document_id)
    if document is None:
        return False
    # File first: on failure metadata remains for retry. Missing files are safe
    # to retry after an earlier interrupted delete; unsafe paths are never used.
    await run_in_threadpool(storage.delete, document)
    # Release before metadata deletion: a DB failure leaves retryable metadata.
    # The KB deletion policy also checks actual documents, not just reservations.
    await release_reservation(database, owner_id, document["knowledge_base_id"], document_id)
    result = await database.get_collection("documents").delete_one(
        {"_id": document_id, "owner_id": owner_id}
    )
    return result.deleted_count == 1
