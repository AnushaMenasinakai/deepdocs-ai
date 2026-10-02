"""Public question/answer contract; context sources without provider internals."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class SupportingSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    document_id: str
    source_filename: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)


class AskResponse(BaseModel):
    status: Literal["answered", "insufficient_context"]
    answer: str
    retrieved_chunk_count: int
    sources: list[SupportingSource]
