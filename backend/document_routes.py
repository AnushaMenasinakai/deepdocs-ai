"""HTTP upload boundaries; existing authentication remains authoritative."""
from contextlib import contextmanager
from datetime import timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from typing import Annotated
from management import DocumentQuery, DocumentPage, browse
from pymongo.errors import PyMongoError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException
from python_multipart.exceptions import MultipartParseError
from auth import get_current_user
from config import ConfigurationError, load_document_settings
from database import get_database
from document_schemas import DocumentResponse, ChunkResponse
from document_storage import get_document_storage, UploadRejected
import documents
import knowledge_bases
import document_processing
from document_operations import DocumentBusy
from pdf_processing import ProcessingFailure
from config import load_processing_settings, load_embedding_settings
from embeddings import EmbeddingFailure
from vector_store import VectorFailure, get_vector_store
from document_operations import claim_document, release_document
import document_embeddings
from document_schemas import EmbeddingResponse

router = APIRouter(tags=["Documents"])
UPLOAD_SCHEMA = {"requestBody": {"required": True, "content": {}}}
UPLOAD_SCHEMA["requestBody"]["content"]["multipart/form-data"] = {
    "schema": {"type": "object", "required": ["file"],
               "properties": {"file": {"type": "string", "format": "binary"}}}
}


@contextmanager
def safe_errors():
    try:
        yield
    except VectorFailure:
        raise HTTPException(503, "Vector service is temporarily unavailable or incompatible.") from None
    except DocumentBusy:
        raise HTTPException(409, "A document operation is already in progress. Please retry later.") from None
    except (ProcessingFailure, EmbeddingFailure) as error:
        raise HTTPException(error.status, str(error)) from None
    except UploadRejected as error:
        raise HTTPException(error.status, str(error)) from None
    except (PyMongoError, OSError, TimeoutError):
        raise HTTPException(503, "Document service is temporarily unavailable.") from None


def parse_id(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(422, "Invalid resource ID.")
    return ObjectId(value)


def found(value, resource="Document"):
    if value is None or value is False:
        raise HTTPException(404, resource + " not found.")
    return value


def upload_settings():
    try:
        return load_document_settings()
    except ConfigurationError:
        raise HTTPException(503, "Document upload configuration is unavailable.") from None


async def parse_upload(request, max_bytes):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Use multipart/form-data with one PDF file.")
    # Bound the entire multipart stream, not just the already-spooled file.
    # Small overhead allowance accommodates MIME headers/boundaries.
    limit = max_bytes + 64 * 1024

    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "PDF exceeds the configured upload limit.")
            yield chunk

    try:
        form = await MultiPartParser(
            request.headers, bounded_stream(), max_files=1, max_fields=0,
        ).parse()
    except (MultiPartException, MultipartParseError, ValueError):
        raise HTTPException(422, "Provide exactly one PDF in the file field.") from None
    if len(form.multi_items()) != 1 or not isinstance(form.get("file"), UploadFile):
        await form.close()
        raise HTTPException(422, "Provide exactly one PDF in the file field.")
    return form


@router.post(
    "/api/knowledge-bases/{knowledge_base_id}/documents",
    response_model=DocumentResponse, status_code=201,
    openapi_extra=UPLOAD_SCHEMA,
)
async def upload(
    knowledge_base_id: str, request: Request,
    current_user=Depends(get_current_user), database=Depends(get_database),
    storage=Depends(get_document_storage), settings=Depends(upload_settings),
):
    owner_id, base_id = ObjectId(current_user.id), parse_id(knowledge_base_id)
    with safe_errors():
        found(await knowledge_bases.get_knowledge_base(database, owner_id, base_id), "Knowledge Base")
        # Parse/spool only after authentication and ownership have succeeded.
        form = await parse_upload(request, settings.max_upload_bytes)
        try:
            return found(await documents.upload_document(
                database, storage, owner_id, base_id, form["file"], settings.max_upload_bytes,
            ), "Knowledge Base")
        finally:
            await form.close()


@router.get("/api/knowledge-bases/{knowledge_base_id}/documents", response_model=list[DocumentResponse])
async def list_owned(
    knowledge_base_id: str, current_user=Depends(get_current_user), database=Depends(get_database),
):
    owner_id, base_id = ObjectId(current_user.id), parse_id(knowledge_base_id)
    with safe_errors():
        found(await knowledge_bases.get_knowledge_base(database, owner_id, base_id), "Knowledge Base")
        return await documents.list_documents(database, owner_id, base_id)


@router.get("/api/knowledge-bases/{knowledge_base_id}/documents/browse", response_model=DocumentPage)
async def browse_owned(
    knowledge_base_id: str, query: Annotated[DocumentQuery, Query()],
    current_user=Depends(get_current_user), database=Depends(get_database),
):
    owner, base = ObjectId(current_user.id), parse_id(knowledge_base_id)
    with safe_errors():
        found(await knowledge_bases.get_knowledge_base(database, owner, base), "Knowledge Base")
        return await browse(database, owner, query, base)


@router.get("/api/documents/{document_id}", response_model=DocumentResponse)
async def get_one(document_id: str, current_user=Depends(get_current_user), database=Depends(get_database)):
    with safe_errors():
        document = found(await documents.find_document(database, ObjectId(current_user.id), parse_id(document_id)))
        return documents.public_document(document)


@router.delete("/api/documents/{document_id}", status_code=204)
async def delete(
    document_id: str, current_user=Depends(get_current_user), database=Depends(get_database),
    storage=Depends(get_document_storage),
):
    with safe_errors():
        found(await documents.delete_document(database, storage, ObjectId(current_user.id), parse_id(document_id)))
    return Response(status_code=204)


def processing_settings():
    try:
        return load_processing_settings()
    except ConfigurationError:
        raise HTTPException(503, "PDF processing configuration is unavailable.") from None


@router.post("/api/documents/{document_id}/process", response_model=DocumentResponse)
async def process(
    document_id: str, current_user=Depends(get_current_user), database=Depends(get_database),
    storage=Depends(get_document_storage), settings=Depends(processing_settings),
):
    with safe_errors():
        document = found(await document_processing.process_document(
            database, storage, ObjectId(current_user.id), parse_id(document_id), settings,
        ))
        return documents.public_document(document)


@router.get("/api/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def chunks(
    document_id: str, current_user=Depends(get_current_user), database=Depends(get_database),
):
    with safe_errors():
        values = found(await document_processing.inspect_chunks(
            database, ObjectId(current_user.id), parse_id(document_id),
        ))
        return [ChunkResponse(
            id=str(value["_id"]), document_id=str(value["document_id"]),
            knowledge_base_id=str(value["knowledge_base_id"]),
            **{key: value[key] for key in (
                "source_filename", "chunk_index", "text", "page_start", "page_end", "character_count",
            )},
            created_at=value["created_at"].replace(tzinfo=timezone.utc)
            if value["created_at"].tzinfo is None else value["created_at"],
        ) for value in values]


def embedding_settings():
    try:
        return load_embedding_settings()
    except ConfigurationError:
        raise HTTPException(503, "Embedding configuration is unavailable.") from None


@router.post("/api/documents/{document_id}/embeddings", response_model=EmbeddingResponse)
async def generate_embeddings(
    document_id: str, current_user=Depends(get_current_user), database=Depends(get_database),
    settings=Depends(embedding_settings),
):
    with safe_errors():
        return found(await document_embeddings.generate_embeddings(
            database, ObjectId(current_user.id), parse_id(document_id), settings,
        ))


@router.get("/api/health/qdrant")
async def qdrant_health():
    with safe_errors():
        await get_vector_store().health()
        return {"status": "ok", "message": "Qdrant is reachable"}


@router.get("/api/documents/{document_id}/vector-status")
async def vector_status(document_id: str, current_user=Depends(get_current_user), database=Depends(get_database)):
    owner, identifier = ObjectId(current_user.id), parse_id(document_id)
    with safe_errors():
        document, token = await claim_document(database, owner, identifier)
        found(document)
        try:
            state = document.get("vector_index", {})
            count = 0
            current_count = 0
            if state.get("collection_name"):
                store = get_vector_store()
                store.check_target(state)
                count = await store.count(document, collection=state["collection_name"])
                current_count = await store.count(document, document.get("chunk_generation"), state["collection_name"])
            expected = document.get("chunk_count", 0)
            synchronized = (state.get("status") == "indexed" and document.get("status") == "processed"
                            and state.get("chunk_generation") == document.get("chunk_generation")
                            and expected > 0 and count == current_count == expected)
            return {"document_id": document_id, "status": state.get("status", "not_generated"),
                    "collection_name": state.get("collection_name"), "expected_chunk_count": expected,
                    "stored_vector_count": count, "synchronized": synchronized}
        finally:
            await release_document(database, owner, identifier, token)
