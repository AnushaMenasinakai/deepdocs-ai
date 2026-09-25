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
    status: Literal["uploaded"]
    created_at: datetime
    updated_at: datetime
