"""Public history snapshot, excluding ownership and storage internals."""
from datetime import datetime
from pydantic import Field
from rag_schemas import AskResponse


class HistoryResponse(AskResponse):
    id: str
    question: str = Field(min_length=1, max_length=1000)
    created_at: datetime
