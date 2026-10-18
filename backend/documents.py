"""Owner-scoped metadata operations and compensating filesystem cleanup."""
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from bson import BSON
from bson.errors import InvalidDocument
from pymongo.errors import PyMongoError
from fastapi.concurrency import run_in_threadpool
from document_schemas import DocumentResponse
from document_storage import StorageCleanupError
from document_operations import claim_document, release_document, record_operation_stage
from vector_store import cleanup_document_vectors
from upload_safety import UploadUncertain, require_acknowledged, verify, check_cancellation
from upload_safety import SaveLifetime, observe_save_result

ORDER = [("created_at", -1), ("_id", -1)]
SAVE_CANCEL_WAIT_SECONDS = 5


async def ensure_document_indexes(database):
    await database.get_collection("documents").create_index(
        [("owner_id", 1), ("knowledge_base_id", 1), *ORDER],
        name="documents_owner_base_created",
    )
    await database.get_collection("documents").create_index(
        [("owner_id", 1), *ORDER],
        name="documents_owner_created",
    )


def public_document(document):
    dates = {key: document[key].replace(tzinfo=timezone.utc)
             if document[key].tzinfo is None else document[key]
             for key in ("created_at", "updated_at")}
    return DocumentResponse(
        id=str(document["_id"]), knowledge_base_id=str(document["knowledge_base_id"]),
        filename=document["original_filename"], content_type=document["content_type"],
        file_size=document["file_size"], status=document["status"], **dates,
        page_count=document.get("page_count"), chunk_count=document.get("chunk_count"),
        processing_error=document.get("processing_error"),
        processed_at=(document["processed_at"].replace(tzinfo=timezone.utc)
                      if document.get("processed_at") and document["processed_at"].tzinfo is None
                      else document.get("processed_at")),
    )


async def release_reservation(database, owner_id, base_id, document_id):
    await database.get_collection("knowledge_bases").update_one(
        {"_id": base_id, "owner_id": owner_id},
        {"$pull": {"_document_ids": document_id}, "$inc": {"_document_revision": 1}},
    )


async def upload_document(database, storage, owner_id, base_id, upload, max_bytes):
    document_id = ObjectId()
    bases = database.get_collection("knowledge_bases")
    docs = database.get_collection("documents")
    require_acknowledged(bases)
    require_acknowledged(docs)
    scope = {"_id": base_id, "owner_id": owner_id}
    # Atomic reservation conflicts with a concurrent conditional KB deletion.
    try:
        base = await bases.find_one_and_update(scope,
            {"$addToSet": {"_document_ids": document_id}, "$inc": {"_document_revision": 1}})
    except asyncio.CancelledError:
        raise
    except (PyMongoError, OSError, TimeoutError):
        base = await verify(bases, scope)
        reservations = base.get("_document_ids")
        if not isinstance(reservations, list) or document_id not in reservations:
            raise UploadUncertain() from None
    check_cancellation()
    if base is None:
        return None
    stored = None
    insertion_attempted = False
    write = None
    save_uncertain = False
    try:
        lifetime = SaveLifetime(upload)
        upload._deepdocs_save_lifetime = lifetime
        write = asyncio.create_task(run_in_threadpool(lifetime.run, storage.save, upload, document_id, max_bytes))
        upload._deepdocs_save_task = write
        write.add_done_callback(observe_save_result)
        try:
            stored = await asyncio.shield(write)
        except asyncio.CancelledError as cancellation:
            # A cancelled await does not stop a filesystem worker thread. Wait
            # for its result before compensation or closing the upload stream.
            # Shield this second await too. If cancellation repeats, the route
            # defers stream closure to worker completion and retains resources.
            save_uncertain = True
            try:
                async with asyncio.timeout(SAVE_CANCEL_WAIT_SECONDS):
                    stored = await asyncio.shield(write)
                save_uncertain = False
            except BaseException:
                raise cancellation from None
            raise
        check_cancellation()
        now = datetime.now(timezone.utc)
        document = {
            "_id": document_id, "knowledge_base_id": base_id, "owner_id": owner_id,
            **stored, "status": "uploaded", "created_at": now, "updated_at": now,
        }
        # A known local serialization rejection occurs before insertion dispatch
        # and is safe to compensate. Driver/server errors are not such proof.
        try:
            BSON.encode(document)
        except InvalidDocument:
            raise UploadUncertain() from None
        insertion_attempted = True
        try:
            await docs.insert_one(document)
        except asyncio.CancelledError:
            raise
        except (PyMongoError, OSError, TimeoutError):
            confirmed = await verify(docs, {"_id": document_id, "owner_id": owner_id, "knowledge_base_id": base_id})
            # Lifecycle state and timestamps may legitimately change after commit.
            if any(confirmed.get(key) != value for key, value in stored.items()):
                raise UploadUncertain() from None
            document = confirmed
        check_cancellation()
    except BaseException as error:
        if insertion_attempted or save_uncertain or (write is not None and not write.done()):
            # Absence/error/cancellation is not proof a remote insert or local
            # worker cannot still finish. Never compensate these uncertain cases.
            raise
        # Retain the reservation if file cleanup fails: KB deletion stays blocked.
        if isinstance(error, StorageCleanupError):
            raise
        try:
            if stored:
                await run_in_threadpool(storage.delete, {"_id": document_id, **stored})
            await release_reservation(database, owner_id, base_id, document_id)
        except Exception:
            if isinstance(error, asyncio.CancelledError):
                raise error from None
            raise
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


async def deletion_checkpoint():
    await asyncio.sleep(0)
    check_cancellation()


async def delete_document(database, storage, owner_id, document_id, expected_knowledge_base_id=None):
    document, token = await claim_document(
        database, owner_id, document_id, expected_knowledge_base_id, operation_type="deletion",
    )
    if document is None:
        return False
    # Once any mutation is dispatched, errors cannot justify unlocking. Even an
    # acknowledged destructive step can leave an incomplete deletion lifecycle.
    dispatched = False
    cancelled = False
    sequence = 0
    diagnostic_uncertain = False

    async def record(stage, acknowledged):
        nonlocal sequence, diagnostic_uncertain
        diagnostic_uncertain = True
        sequence = await record_operation_stage(
            database, owner_id, document_id, token, sequence, stage,
            expected_knowledge_base_id=document["knowledge_base_id"],
            last_acknowledged_stage=acknowledged,
        )
        await deletion_checkpoint()
        diagnostic_uncertain = False

    try:
        await deletion_checkpoint()
        base_id = document["knowledge_base_id"]
        claimed = {"_id": document_id, "owner_id": owner_id,
                   "knowledge_base_id": base_id, "_operation": token}
        # Pure path validation before destructive dispatch; delete revalidates it.
        storage.reference(document)
        acknowledged = "claimed"
        if document.get("vector_index", {}).get("collection_name"):
            await record("vector_invalidation", acknowledged)
            dispatched = True  # Invalidation may commit despite a lost response.
            result = await database.get_collection("documents").update_one(
                claimed,
                {"$set": {"vector_index": {**document["vector_index"], "status": "stale"}}},
            )
            if result.matched_count != 1:
                raise PyMongoError("Document deletion requires service review.")
            await deletion_checkpoint()
            await record("vector_deletion", "vector_invalidation")
            await cleanup_document_vectors(document)
            await deletion_checkpoint()
            acknowledged = "vector_deletion"
        await record("pdf_deletion", acknowledged)
        dispatched = True  # A cancelled await does not stop the filesystem worker.
        await run_in_threadpool(storage.delete, document)
        await deletion_checkpoint()
        await record("chunk_deletion", "pdf_deletion")
        await database.get_collection("document_chunks").delete_many(
            {"document_id": document_id, "owner_id": owner_id, "knowledge_base_id": base_id}
        )
        await deletion_checkpoint()
        await record("reservation_release", "chunk_deletion")
        await release_reservation(database, owner_id, base_id, document_id)
        await deletion_checkpoint()
        await record("metadata_deletion", "reservation_release")
        result = await database.get_collection("documents").delete_one(claimed)
        if result.deleted_count != 1:
            raise PyMongoError("Document deletion outcome could not be confirmed.")
        await deletion_checkpoint()
        return True
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        # No compensation or failed-state publication after dispatch. The claim
        # remains unless final metadata deletion itself committed. Never replay.
        if not dispatched and not diagnostic_uncertain:
            try:
                await release_document(database, owner_id, document_id, token)
            except Exception:
                if not cancelled:
                    raise
