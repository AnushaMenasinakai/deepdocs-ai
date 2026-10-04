"""Public Q&A snapshots, with explicit legacy and citation-aware versions."""
from typing import Annotated, Literal
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CitationId = Annotated[int, Field(strict=True, ge=1, le=5)]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question", mode="before")
    @classmethod
    def trim_question(cls, value):
        return value.strip() if isinstance(value, str) else value


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    text: str = Field(min_length=1, max_length=4000)
    citation_ids: list[CitationId] = Field(min_length=1, max_length=5)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Invalid claim text")
        value.encode("utf-8")
        return value

    @field_validator("citation_ids")
    @classmethod
    def unique_ids(cls, value):
        return list(dict.fromkeys(value))  # Exact duplicates: first occurrence wins.


def plain_answer(claims):
    return "\n\n".join(claim.text for claim in claims)


class SupportingSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    document_id: str
    source_filename: str = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    citation_id: CitationId | None = None

    @model_validator(mode="after")
    def valid_source(self):
        if (not ObjectId.is_valid(self.document_id) or self.page_end < self.page_start
                or not self.source_filename.strip() or any(c in self.source_filename for c in '/\\:')
                or any(ord(c) < 32 for c in self.source_filename)):
            raise ValueError("Invalid source")
        self.source_filename.encode("utf-8")
        return self


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    status: Literal["answered", "insufficient_context"]
    answer: str = Field(min_length=1, max_length=4000)
    retrieved_chunk_count: int = Field(ge=0, le=5)
    sources: list[SupportingSource] = Field(max_length=5)
    citation_version: Literal[0, 1] = 0
    claims: list[AnswerClaim] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def require_versioned_claims(cls, value):
        if isinstance(value, dict) and value.get("citation_version") == 1 and "claims" not in value:
            raise ValueError("Missing citation claims")
        return value

    @field_validator("citation_version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Invalid citation version")
        return value

    @model_validator(mode="after")
    def consistent_answer(self):
        if not self.answer.strip() or len(self.sources) > self.retrieved_chunk_count:
            raise ValueError("Invalid answer")
        if self.status == "insufficient_context":
            if self.sources or self.claims:
                raise ValueError("Abstention cannot have citations")
        elif not self.sources:
            raise ValueError("Missing sources")
        if self.citation_version == 0:
            if self.claims or any(s.citation_id is not None for s in self.sources):
                raise ValueError("Legacy records cannot imply claim citations")
        elif self.status == "answered":
            ids = [s.citation_id for s in self.sources]
            provenance = [(s.document_id, s.page_start, s.page_end) for s in self.sources]
            used = {i for claim in self.claims for i in claim.citation_ids}
            if (not self.claims or None in ids or len(ids) != len(set(ids))
                    or len(provenance) != len(set(provenance)) or used != set(ids)
                    or self.answer != plain_answer(self.claims)):
                raise ValueError("Inconsistent citations")
        return self
