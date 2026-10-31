"""Persist validated chunk vectors; publish generation-scoped MongoDB state last."""
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from document_operations import claim_document, release_document, record_operation_stage, DocumentBusy
from document_processing import inspect_chunks
from embeddings import EmbeddingFailure, embed_batches
from embedding_execution import Batches
from cpu_workers import WorkerInfrastructureFailure
from expensive_admission import Resource
from vector_store import get_vector_store, cleanup_document_vectors, VectorFailure


class RequiresProcessing(EmbeddingFailure):
    """Typed eligibility failure; bulk callers must not parse exception messages."""

    def __init__(self, message="Process this document before generating embeddings."):
        super().__init__(message, 409)


def require_processed_generation(document):
    if document.get("status") != "processed" or not isinstance(document.get("chunk_generation"), ObjectId):
        raise RequiresProcessing()


async def indexing_checkpoint():
    await asyncio.sleep(0)
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()


async def generate_embeddings(database, owner_id, document_id, settings, expected_knowledge_base_id=None,
                              *, workers=None, session=None):
    # Preserve ownership/missing/busy precedence before reserving execution.
    scope = {"_id": document_id, "owner_id": owner_id}
    if expected_knowledge_base_id is not None:
        scope["knowledge_base_id"] = expected_knowledge_base_id
    snapshot = await database.get_collection("documents").find_one(scope)
    if snapshot is None:
        return None
    if "_operation" in snapshot:
        raise DocumentBusy()
    if session is not None:
        return await _generate_embeddings(database, owner_id, document_id, settings,
                                          expected_knowledge_base_id, session)
    if workers is None:
        raise EmbeddingFailure()
    with workers.workflow(Resource.DOCUMENT) as workflow:
        with workers.session(workflow) as acquired:
            return await _generate_embeddings(database, owner_id, document_id, settings,
                                              expected_knowledge_base_id, acquired)


async def _generate_embeddings(database, owner_id, document_id, settings, expected_knowledge_base_id, session):
    docs = database.get_collection("documents")
    if expected_knowledge_base_id is not None:
        # Read-only early rejection for clearly ineligible bulk selections. This
        # is not authorization for the mutation: the claim repeats the full scope.
        snapshot = await database.get_collection("documents").find_one({
            "_id": document_id, "owner_id": owner_id,
            "knowledge_base_id": expected_knowledge_base_id,
        })
        if snapshot is None:
            return None
        if "_operation" in snapshot:
            raise DocumentBusy()
        require_processed_generation(snapshot)
    document, token = await claim_document(database, owner_id, document_id, expected_knowledge_base_id,
                                         operation_type="indexing")
    if document is None:
        return None
    claimed = {"_id": document_id, "owner_id": owner_id, "_operation": token,
               "knowledge_base_id": document["knowledge_base_id"]}
    started = False
    completed = False
    cancelled = False
    uncertain = False
    phase = "validation"
    vector_state = document.get("vector_index", {})
    store = None
    sequence = 0

    async def record(stage, acknowledged):
        nonlocal sequence, uncertain, phase
        # Set before dispatch: an uncertain diagnostic must never authorize
        # failed-state publication or unlock, even before vector work starts.
        phase = "diagnostic"
        uncertain = True
        sequence = await record_operation_stage(
            database, owner_id, document_id, token, sequence, stage,
            expected_knowledge_base_id=document["knowledge_base_id"],
            last_acknowledged_stage=acknowledged,
        )
        await indexing_checkpoint()
        uncertain = False

    try:
        await indexing_checkpoint()
        require_processed_generation(document)
        chunks = await inspect_chunks(database, owner_id, document_id)
        if not chunks:
            raise RequiresProcessing("This document has no active chunks to embed.")
        if [chunk["chunk_index"] for chunk in chunks] != list(range(len(chunks))):
            raise EmbeddingFailure("Document chunks are temporarily unavailable.")
        generation = document["chunk_generation"]
        claimed = {**claimed, "chunk_generation": generation}
        metadata = {"status": "generating", "chunk_generation": generation,
                    "model": settings.model_name, "dimension": None,
                    "embedded_at": None, "chunk_count": 0, "error": None}
        store = get_vector_store()
        vector_state = {**vector_state, "status": "indexing"}
        # Replace the whole state before work; a failed retry cannot retain success.
        await indexing_checkpoint()
        phase = "initial_metadata"
        started = True
        uncertain = True
        result = await docs.update_one(claimed, {"$set": {"embedding": metadata, "vector_index": vector_state}})
        if result.matched_count != 1:
            raise EmbeddingFailure()
        uncertain = False
        await record("encoding", "validation")
        phase = "encoding"
        await indexing_checkpoint()
        count, dimension = 0, None
        batches = Batches([chunk["text"] for chunk in chunks], settings, factory=embed_batches)
        while True:
            batch = await session.run(batches.next_batch)
            if batch is None:
                break
            batch_dimension, vectors = batch
            await indexing_checkpoint()
            if dimension is not None and dimension != batch_dimension:
                raise EmbeddingFailure("Embedding vector validation failed.")
            if dimension is None:
                await record("collection_setup", "encoding")
                phase = "collection_setup"
                await store.ensure_collection(batch_dimension)
                await indexing_checkpoint()
                # Validate the destination before removing any previous vectors.
                await record("vector_cleanup", "collection_setup")
                phase = "previous_vector_cleanup"
                uncertain = True
                await cleanup_document_vectors(document)
                uncertain = False
                await indexing_checkpoint()
                vector_state = {"status": "indexing", "collection_name": store.collection,
                                "target": store.target, "chunk_generation": generation,
                                "embedding_model": settings.model_name, "indexed_at": None}
                await record("indexing_metadata", "vector_cleanup")
                phase = "indexing_metadata"
                uncertain = True
                result = await docs.update_one(claimed, {"$set": {"vector_index": vector_state}})
                if result.matched_count != 1:
                    raise EmbeddingFailure()
                uncertain = False
                await indexing_checkpoint()
                await record("vector_replacement", "indexing_metadata")
                phase = "document_vector_delete"
                uncertain = True
                await store.delete_document(document)
                uncertain = False
                await indexing_checkpoint()
            dimension = batch_dimension
            phase = "upsert"
            uncertain = True
            await store.upsert(chunks[count:count + len(vectors)], vectors, dimension)
            uncertain = False
            await indexing_checkpoint()
            count += len(vectors)
            del vectors  # No full-document vector accumulation or MongoDB writes.
            phase = "encoding"
        if count != len(chunks) or dimension is None:
            raise EmbeddingFailure("Embedding vector validation failed.")
        await record("count_verification", "vector_replacement")
        phase = "count_verification"
        await indexing_checkpoint()
        if await store.count(document, generation) != count or await store.count(document) != count:
            raise VectorFailure("Vector persistence could not be confirmed.")
        await indexing_checkpoint()
        vector_state = {**vector_state, "status": "indexed", "embedding_dimension": dimension,
                        "chunk_count": count, "indexed_at": datetime.now(timezone.utc)}
        metadata = {**metadata, "status": "generated", "dimension": dimension,
                    "chunk_count": count, "embedded_at": datetime.now(timezone.utc)}
        # Metadata and unlock are atomic on one document, guarded by generation.
        await record("final_publication", "count_verification")
        phase = "final_publication"
        uncertain = True
        result = await docs.update_one({**claimed, "chunk_generation": generation}, {
            "$set": {"embedding": metadata, "vector_index": vector_state},
            "$unset": {"_operation": "", "_operation_meta": ""},
        })
        if result.matched_count != 1:
            raise EmbeddingFailure()
        completed = True
        uncertain = False
        await indexing_checkpoint()
        return {"document_id": str(document_id), "chunk_count": count,
                "embedding_model": settings.model_name, "embedding_dimension": dimension,
                "status": "generated", "vector_store": "qdrant",
                "collection_name": store.collection, "vector_status": "indexed"}
    except asyncio.CancelledError:
        # Never turn cancellation into a normal failure/success response. Once
        # writes may have started, retain the existing claim/state for recovery;
        # an interrupted remote write has an uncertain outcome. Before writes,
        # the finally block releases the claim. No automatic expiry or retry.
        cancelled = True
        raise
    except WorkerInfrastructureFailure:
        # Unconfirmed dispatch/completion is not a completed local failure.
        uncertain = True
        raise EmbeddingFailure() from None
    except Exception as error:
        # Generic transport/vector errors never prove non-commit. Only a local
        # failure after acknowledged writes may publish failed state and unlock.
        # Count/publication uncertainty must also preserve potentially good data.
        if started and not uncertain and phase not in ("count_verification", "final_publication"):
            try:
                await indexing_checkpoint()
                await docs.update_one(claimed, {
                    "$set": {"embedding": {"status": "failed", "error": "Embedding generation failed. Please retry."},
                             "vector_index": {**vector_state, "status": "failed"}},
                    "$unset": {"_operation": "", "_operation_meta": ""},
                })
            except Exception:
                # Leave the generating state/lock for reconciliation; never fake success.
                pass
        if isinstance(error, (EmbeddingFailure, VectorFailure)):
            raise
        raise EmbeddingFailure() from None
    finally:
        if not started and not completed and not uncertain:
            try:
                await release_document(database, owner_id, document_id, token)
            except Exception:
                if not cancelled:
                    raise
                # Preserve cancellation even if unlocking fails. The retained
                # claim requires recovery, not a fabricated batch failure report.
