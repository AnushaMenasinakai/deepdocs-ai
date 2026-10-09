"""Explicit metadata returned to API consumers; internal storage stays private."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from bson import ObjectId


class OperationStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str
    operation_state: Literal["idle", "claimed_unknown"]
    document_state: Literal["uploaded", "processing", "processed", "indexed", "failed", "unknown"]
    attention: Literal["none", "needs_processing", "needs_reindex", "outcome_uncertain", "requires_review"]
    recommended_action: Literal["none", "process", "reindex", "refresh", "contact_support"]


class BulkDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("document_ids")
    @classmethod
    def canonical_ids(cls, values):
        if any(not ObjectId.is_valid(value) for value in values):
            raise ValueError("Every document ID must be a valid ObjectId string.")
        canonical = [str(ObjectId(value)) for value in values]
        if len(set(canonical)) != len(canonical):
            raise ValueError("Duplicate document IDs are not allowed.")
        return canonical


class BulkDeleteItem(BaseModel):
    document_id: str
    outcome: Literal["succeeded", "failed", "not_attempted"]
    code: Literal["not_found", "busy", "service_unavailable"] | None = None


class BulkDeleteResponse(BaseModel):
    operation: Literal["delete"] = "delete"
    requested: int
    succeeded: int
    failed: int
    not_attempted: int
    results: list[BulkDeleteItem]


class BulkReindexRequest(BulkDeleteRequest):
    document_ids: list[str] = Field(min_length=1, max_length=5)


class BulkReindexItem(BulkDeleteItem):
    code: Literal["not_found", "busy", "requires_processing", "service_unavailable"] | None = None


class BulkReindexResponse(BulkDeleteResponse):
    operation: Literal["reindex"] = "reindex"
    results: list[BulkReindexItem]


class DocumentResponse(BaseModel):
    id: str
    knowledge_base_id: str
    filename: str
    content_type: Literal["application/pdf"]
    file_size: int
    status: Literal["uploaded", "processing", "processed", "failed"]
    created_at: datetime
    updated_at: datetime

    page_count: int | None = None
    chunk_count: int | None = None
    processed_at: datetime | None = None
    processing_error: str | None = None


class ChunkResponse(BaseModel):
    id: str
    document_id: str
    knowledge_base_id: str
    source_filename: str
    chunk_index: int
    text: str
    page_start: int
    page_end: int
    character_count: int
    created_at: datetime


class EmbeddingResponse(BaseModel):
    document_id: str
    chunk_count: int
    embedding_model: str
    embedding_dimension: int
    status: Literal["generated"]
    vector_store: Literal["qdrant"]
    collection_name: str
    vector_status: Literal["indexed"]
