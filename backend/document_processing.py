"""Chunk generations: build fully, persist privately, then atomically activate."""
import logging
from datetime import datetime, timezone
from bson import ObjectId
from document_operations import claim_document, release_document
from pdf_processing import extract_chunks, ProcessingFailure
from vector_store import cleanup_document_vectors

logger = logging.getLogger(__name__)


async def ensure_chunk_indexes(database):
    await database.get_collection("document_chunks").create_index(
        [("owner_id", 1), ("document_id", 1), ("generation", 1), ("chunk_index", 1)],
        name="chunks_owner_document_generation_order",
    )


async def process_document(database, storage, owner_id, document_id, settings):
    docs = database.get_collection("documents")
    chunks = database.get_collection("document_chunks")
    owned = {"document_id": document_id, "owner_id": owner_id}
    generation = ObjectId()
    promotion_started = False
    activated = False
    document, token = await claim_document(database, owner_id, document_id)
    if document is None:
        return None
    claimed = {"_id": document_id, "owner_id": owner_id, "_operation": token}
    try:
        await docs.update_one(claimed, {"$set": {
            "status": "processing", "processing_error": None,
            "embedding": {"status": "not_generated"},
            "vector_index": {**document.get("vector_index", {}), "status": "stale"}, "updated_at": datetime.now(timezone.utc),
        }})
        # Keep native parser calls on the event-loop thread for this synchronous
        # local pipeline. Never run concurrent PyMuPDF work in a thread pool.
        path = storage.processing_path(document)
        page_count, values = extract_chunks(path, settings)
        now = datetime.now(timezone.utc)
        staged = [{
            "_id": ObjectId(), **owned, "knowledge_base_id": document["knowledge_base_id"],
            "source_filename": document["original_filename"], "generation": generation,
            "created_at": now, **value,
        } for value in values]
        await chunks.insert_many(staged, ordered=True)
        await cleanup_document_vectors(document)
        promotion_started = True
        changes = {
            "status": "processed", "chunk_generation": generation,
            "vector_index": {"status": "not_generated"},
            "page_count": page_count, "chunk_count": len(staged),
            "processed_at": now, "processing_error": None, "updated_at": now,
        }
        result = await docs.update_one(claimed, {"$set": changes})
        if result.matched_count != 1:
            raise ProcessingFailure("Processing could not be completed.", 503)
        activated = True
        document.update(changes)
        # Cleanup is not part of activation: an old generation is never exposed.
        try:
            await chunks.delete_many({**owned, "generation": {"$ne": generation}})
        except Exception:
            logger.warning("Inactive chunk cleanup requires reconciliation.")
        await release_document(database, owner_id, document_id, token)
        return document
    except BaseException as error:
        if activated:
            # Activation succeeded but unlocking failed. Don't remove active data.
            raise ProcessingFailure("Processing completed but requires service recovery.", 503) from None
        if promotion_started:
            # A lost acknowledgement may mean activation succeeded. Preserve
            # both generations and the lock rather than deleting possibly active data.
            logger.warning("Chunk activation requires reconciliation.")
            raise ProcessingFailure("Processing outcome could not be confirmed. Service recovery is required.", 503) from None
        safe = error if isinstance(error, ProcessingFailure) else ProcessingFailure("PDF processing failed. Please retry.", 503)
        try:
            await chunks.delete_many({**owned, "generation": generation})
        except Exception:
            logger.warning("Unpublished chunk cleanup requires reconciliation.")
        try:
            await docs.update_one(claimed, {"$set": {
                "status": "failed", "processing_error": str(safe), "updated_at": datetime.now(timezone.utc),
            }})
            await release_document(database, owner_id, document_id, token)
        except Exception:
            logger.warning("Processing state requires reconciliation.")
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
