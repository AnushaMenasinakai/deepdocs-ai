"""Strict retrieval inputs and explicit safe public results."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query", mode="before")
    @classmethod
    def trim_query(cls, value):
        return value.strip() if isinstance(value, str) else value


class SearchResult(BaseModel):
    rank: int
    score: float
    document_id: str
    chunk_id: str
    chunk_index: int
    text: str
    source_filename: str
    page_start: int
    page_end: int


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
