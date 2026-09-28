"""Explicit metadata returned to API consumers; internal storage stays private."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


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
