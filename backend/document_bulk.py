"""Sequential bulk orchestration; per-document lifecycle services remain authoritative."""
from bson import ObjectId
from pymongo.errors import PyMongoError
import documents
import document_embeddings
from document_operations import DocumentBusy
from document_schemas import BulkDeleteItem, BulkDeleteResponse, BulkReindexItem, BulkReindexResponse
from embeddings import EmbeddingFailure
from pdf_processing import ProcessingFailure
from vector_store import VectorFailure


async def delete_documents(database, storage, owner_id, base_id, document_ids):
    """Caller validates the entire request and KB ownership before entering here.

    No rollback or automatic retry. A service failure may follow partial cleanup.
    Cancellation propagates: it must never become a fabricated completion report.
    """
    results = []
    stopped = False
    for identifier in document_ids:
        outcome, code = "not_attempted", None
        if not stopped:
            try:
                deleted = await documents.delete_document(
                    database, storage, owner_id, ObjectId(identifier),
                    expected_knowledge_base_id=base_id,
                )
                outcome = "succeeded" if deleted else "failed"
                code = None if deleted else "not_found"
            except DocumentBusy:
                outcome, code = "failed", "busy"
            except (VectorFailure, PyMongoError, OSError, TimeoutError):
                outcome, code = "failed", "service_unavailable"
                stopped = True
        results.append(BulkDeleteItem(document_id=identifier, outcome=outcome, code=code))
    counts = {key: sum(item.outcome == key for item in results)
              for key in ("succeeded", "failed", "not_attempted")}
    return BulkDeleteResponse(requested=len(document_ids), results=results, **counts)


async def reindex_documents(database, owner_id, base_id, document_ids, settings):
    """Sequential existing-chunk indexing; never extract PDFs or retry operations."""
    results = []
    stopped = False
    for identifier in document_ids:
        outcome, code = "not_attempted", None
        if not stopped:
            try:
                indexed = await document_embeddings.generate_embeddings(
                    database, owner_id, ObjectId(identifier), settings,
                    expected_knowledge_base_id=base_id,
                )
                outcome = "succeeded" if indexed is not None else "failed"
                code = None if indexed is not None else "not_found"
            except DocumentBusy:
                outcome, code = "failed", "busy"
            except document_embeddings.RequiresProcessing:
                outcome, code = "failed", "requires_processing"
            except (EmbeddingFailure, ProcessingFailure, VectorFailure, PyMongoError, OSError, TimeoutError):
                outcome, code = "failed", "service_unavailable"
                stopped = True
        results.append(BulkReindexItem(document_id=identifier, outcome=outcome, code=code))
    counts = {key: sum(item.outcome == key for item in results)
              for key in ("succeeded", "failed", "not_attempted")}
    return BulkReindexResponse(requested=len(document_ids), results=results, **counts)
