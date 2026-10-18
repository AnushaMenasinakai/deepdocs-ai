"""Bounded, read-only workspace document previews with verified KB ownership."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from dashboard import indexed_expression
from management import failed_expression


class RecentDocument(BaseModel):
    id: str
    knowledge_base_id: str
    knowledge_base_name: str | None
    filename: str
    created_at: datetime
    status: Literal["uploaded", "processing", "processed", "failed"]
    indexed: bool
    failed: bool


class RecentDocumentsResponse(BaseModel):
    documents: list[RecentDocument] = Field(max_length=3)


async def recent_documents(database, owner):
    cursor = await database.get_collection("documents").aggregate([
        {"$match": {"owner_id": owner}},
        {"$sort": {"created_at": -1, "_id": -1}},
        {"$lookup": {
            "from": "knowledge_bases", "let": {"base_id": "$knowledge_base_id"},
            "pipeline": [
                {"$match": {"owner_id": owner, "$expr": {"$eq": ["$_id", "$$base_id"]}}},
                {"$project": {"name": 1}},
            ], "as": "base",
        }},
        # Exclude orphaned/foreign references BEFORE limiting the global result.
        {"$unwind": "$base"},
        {"$limit": 3},
        {"$project": {
            "knowledge_base_id": 1, "original_filename": 1, "created_at": 1,
            "status": 1, "knowledge_base_name": "$base.name",
            "indexed": indexed_expression(), "failed": failed_expression(),
        }},
    ], maxTimeMS=5000)
    items = []
    for row in await cursor.to_list(length=3):
        date = row["created_at"]
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        name = row.get("knowledge_base_name")
        # Optional legacy metadata is never coerced from arbitrary objects.
        name = name if isinstance(name, str) and name.strip() and len(name) <= 100 else None
        items.append(RecentDocument(
            id=str(row["_id"]), knowledge_base_id=str(row["knowledge_base_id"]),
            knowledge_base_name=name, filename=row["original_filename"],
            created_at=date, status=row["status"], indexed=row["indexed"], failed=row["failed"],
        ))
    return RecentDocumentsResponse(documents=items)
