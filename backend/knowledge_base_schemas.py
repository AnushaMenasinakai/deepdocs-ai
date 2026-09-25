"""Validated client input and explicit public Knowledge Base fields."""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name", "description", mode="before")
    @classmethod
    def trim_strings(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value):
        return value or None


class KnowledgeBaseUpdate(KnowledgeBaseCreate):
    name: str = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_changes(self):
        # Omission is allowed; an explicitly supplied null name is not.
        if not self.model_fields_set:
            raise ValueError("Provide name or description to update.")
        return self


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
