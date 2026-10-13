"""Chunk generations: build fully, persist privately, then atomically activate."""
import asyncio
import logging
from datetime import datetime, timezone
from bson import ObjectId
from document_operations import claim_document, release_document, record_operation_stage
from pdf_processing import extract_chunks, ProcessingFailure
from vector_store import cleanup_document_vectors

logger = logging.getLogger(__name__)


async def ensure_chunk_indexes(database):
    await database.get_collection("document_chunks").create_index(
        [("owner_id", 1), ("document_id", 1), ("generation", 1), ("chunk_index", 1)],
        name="chunks_owner_document_generation_order",
    )


async def processing_checkpoint():
    await asyncio.sleep(0)
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()

async def process_document(database, storage, owner_id, document_id, settings):
    docs = database.get_collection("documents")
    chunks = database.get_collection("document_chunks")
    generation = ObjectId()
    document, token = await claim_document(database, owner_id, document_id, operation_type="processing")
    if document is None:
        return None
    owned = {"document_id": document_id, "owner_id": owner_id,
             "knowledge_base_id": document["knowledge_base_id"]}
    claimed = {"_id": document_id, "owner_id": owner_id, "_operation": token,
               "knowledge_base_id": document["knowledge_base_id"]}
    # Only the authoritative prior generation is eligible for cleanup. Orphan
    # generations remain for review; no predicate may match future generations.
    previous = document.get("chunk_generation")
    had_generation = "chunk_generation" in document
    activation = {**claimed, "chunk_generation": previous if had_generation
                  else {"$exists": False}}
    phase = "initialization"
    sequence = 0

    async def record(stage, acknowledged):
        nonlocal sequence, phase
        # A diagnostic write is itself uncertain until acknowledged. It must
        # never enter the local extraction failure-and-release path.
        phase = "diagnostic"
        sequence = await record_operation_stage(
            database, owner_id, document_id, token, sequence, stage,
            expected_knowledge_base_id=document["knowledge_base_id"],
            last_acknowledged_stage=acknowledged,
        )
        await processing_checkpoint()

    try:
        await processing_checkpoint()
        result = await docs.update_one(claimed, {"$set": {
            "status": "processing", "processing_error": None,
            "embedding": {"status": "not_generated"},
            "vector_index": {**document.get("vector_index", {}), "status": "stale"},
            "updated_at": datetime.now(timezone.utc),
        }})
        if result.matched_count != 1:
            raise ProcessingFailure("Processing requires service review.", 503)
        await record("extraction", "claimed")
        phase = "local_extraction"
        # Synchronous PyMuPDF: no detached worker or concurrent parser execution.
        path = storage.processing_path(document)
        page_count, values = extract_chunks(path, settings)
        await processing_checkpoint()
        now = datetime.now(timezone.utc)
        staged = [{
            "_id": ObjectId(), **owned,
            "source_filename": document["original_filename"], "generation": generation,
            "created_at": now, **value,
        } for value in values]
        await record("chunk_insertion", "extraction")
        phase = "chunk_insertion"  # Set before dispatch; errors are uncertain.
        await chunks.insert_many(staged, ordered=True)
        await processing_checkpoint()
        await record("vector_cleanup", "chunk_insertion")
        phase = "vector_cleanup"
        await cleanup_document_vectors(document)
        await processing_checkpoint()
        await record("activation", "vector_cleanup")
        phase = "activation"
        changes = {
            "status": "processed", "chunk_generation": generation,
            "vector_index": {"status": "not_generated"},
            "page_count": page_count, "chunk_count": len(staged),
            "processed_at": now, "processing_error": None, "updated_at": now,
        }
        result = await docs.update_one(activation, {"$set": changes})
        if result.matched_count != 1:
            raise ProcessingFailure("Processing requires service review.", 503)
        document.update(changes)
        await processing_checkpoint()
        await record("obsolete_cleanup", "activation")
        phase = "obsolete_cleanup"
        if isinstance(previous, ObjectId) and previous != generation:
            await chunks.delete_many({**owned, "generation": previous})
        elif had_generation:
            raise ProcessingFailure("Processing requires service review.", 503)
        await processing_checkpoint()
        phase = "release"
        await release_document(database, owner_id, document_id, token)
        return document
    except asyncio.CancelledError:
        # Cancellation is never a failed-state publication or permission to
        # compensate/unlock. A dispatched command may still complete remotely.
        raise
    except Exception as error:
        if phase != "local_extraction":
            logger.warning("Processing outcome requires reconciliation.")
            raise ProcessingFailure("Processing outcome could not be confirmed. Service review is required.", 503) from None
        # Completed local failure: no chunk/vector writes have been dispatched.
        safe = error if isinstance(error, ProcessingFailure) else ProcessingFailure("PDF processing failed. Please retry.", 503)
        try:
            await processing_checkpoint()
            result = await docs.update_one(claimed, {"$set": {
                "status": "failed", "processing_error": str(safe), "updated_at": datetime.now(timezone.utc),
            }})
            if result.matched_count != 1:
                raise ProcessingFailure("Processing requires service review.", 503)
            await processing_checkpoint()
            await release_document(database, owner_id, document_id, token)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ProcessingFailure("Processing state requires service review.", 503) from None
        raise safe from None

async def inspect_chunks(database, owner_id, document_id):
    docs = database.get_collection("documents")
    owned = {"_id": document_id, "owner_id": owner_id}
    # Validate the generation again after reading, so a concurrent activation or
    # deletion cannot return a misleading partial snapshot.
    for _ in range(3):
        document = await docs.find_one(owned)
        if document is None:
            return None
        generation = document.get("chunk_generation")
        if generation is None:
            return []
        cursor = database.get_collection("document_chunks").find({
            "owner_id": owner_id, "document_id": document_id, "generation": generation,
        }).sort([("chunk_index", 1)])
        result = [chunk async for chunk in cursor]
        latest = await docs.find_one(owned)
        if latest is None:
            return None
        if latest.get("chunk_generation") == generation:
            if len(result) != latest.get("chunk_count"):
                raise ProcessingFailure("Chunk data is temporarily unavailable.", 503)
            return result
    raise ProcessingFailure("Document changed during inspection. Please retry.", 409)
