"""Generation-scoped verification metadata, never persistent vector storage."""
from datetime import datetime, timezone
from document_operations import claim_document, release_document
from document_processing import inspect_chunks
from embeddings import EmbeddingFailure, embed_batches


async def generate_embeddings(database, owner_id, document_id, settings):
    document, token = await claim_document(database, owner_id, document_id)
    if document is None:
        return None
    docs = database.get_collection("documents")
    claimed = {"_id": document_id, "owner_id": owner_id, "_operation": token}
    started = False
    completed = False
    try:
        if document.get("status") != "processed" or not document.get("chunk_generation"):
            raise EmbeddingFailure("Process this document before generating embeddings.", 409)
        chunks = await inspect_chunks(database, owner_id, document_id)
        if not chunks:
            raise EmbeddingFailure("This document has no active chunks to embed.", 409)
        if [chunk["chunk_index"] for chunk in chunks] != list(range(len(chunks))):
            raise EmbeddingFailure("Document chunks are temporarily unavailable.")
        generation = document["chunk_generation"]
        metadata = {"status": "generating", "chunk_generation": generation,
                    "model": settings.model_name, "dimension": None,
                    "embedded_at": None, "chunk_count": 0, "error": None}
        # Replace the whole state before work; a failed retry cannot retain success.
        started = True
        result = await docs.update_one(claimed, {"$set": {"embedding": metadata}})
        if result.matched_count != 1:
            raise EmbeddingFailure()
        count, dimension = 0, None
        for batch_dimension, vectors in embed_batches([chunk["text"] for chunk in chunks], settings):
            if dimension is not None and dimension != batch_dimension:
                raise EmbeddingFailure("Embedding vector validation failed.")
            dimension = batch_dimension
            count += len(vectors)
            del vectors  # No full-document vector accumulation or MongoDB writes.
        if count != len(chunks) or dimension is None:
            raise EmbeddingFailure("Embedding vector validation failed.")
        metadata = {**metadata, "status": "generated", "dimension": dimension,
                    "chunk_count": count, "embedded_at": datetime.now(timezone.utc)}
        # Metadata and unlock are atomic on one document, guarded by generation.
        result = await docs.update_one({**claimed, "chunk_generation": generation}, {
            "$set": {"embedding": metadata}, "$unset": {"_operation": ""},
        })
        if result.matched_count != 1:
            raise EmbeddingFailure()
        completed = True
        return {"document_id": str(document_id), "chunk_count": count,
                "embedding_model": settings.model_name, "embedding_dimension": dimension,
                "status": "generated"}
    except Exception as error:
        if started:
            try:
                await docs.update_one(claimed, {
                    "$set": {"embedding": {"status": "failed", "error": "Embedding generation failed. Please retry."}},
                    "$unset": {"_operation": ""},
                })
            except Exception:
                # Leave the generating state/lock for reconciliation; never fake success.
                pass
        if isinstance(error, EmbeddingFailure):
            raise
        raise EmbeddingFailure() from None
    finally:
        if not started and not completed:
            await release_document(database, owner_id, document_id, token)
