"""Bounded management browsing, separate from legacy selector lists and retrieval."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from dashboard import indexed_expression
from knowledge_base_schemas import KnowledgeBaseResponse
from document_schemas import DocumentResponse
from knowledge_bases import public_knowledge_base
from documents import public_document


class BrowseQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1, le=1000000)
    limit: int = Field(default=20, ge=1, le=100)
    order: Literal["asc", "desc"] = "desc"

    @field_validator("page", "limit", mode="before")
    @classmethod
    def integer_query(cls, value):
        if isinstance(value, str) and not re.fullmatch(r"[0-9]+", value):
            raise ValueError("Use a positive whole number")
        return value

    @field_validator("search", mode="before")
    @classmethod
    def trim_search(cls, value):
        return value.strip() if isinstance(value, str) else value


class KnowledgeBaseQuery(BrowseQuery):
    sort: Literal["updated_at", "created_at", "name"] = "updated_at"


class DocumentQuery(BrowseQuery):
    sort: Literal["created_at", "updated_at", "filename"] = "created_at"
    status: Literal["all", "indexed", "processing", "failed"] = "all"


class DocumentItem(DocumentResponse):
    indexed: bool
    failed: bool


class PageMetadata(BaseModel):
    page: int
    limit: int
    total: int
    total_pages: int


class KnowledgeBasePage(PageMetadata):
    items: list[KnowledgeBaseResponse]


class DocumentPage(PageMetadata):
    items: list[DocumentItem]


def failed_expression():
    # Same failure categories as Dashboard, including embedding/vector failures.
    return {"$or": [{"$eq": ["$"+field, "failed"]}
                    for field in ("status", "embedding.status", "vector_index.status")]}


async def browse(database, owner, query, base_id=None):
    is_document = base_id is not None
    scope = {"owner_id": owner}
    if is_document:
        scope["knowledge_base_id"] = base_id
    if query.search:
        scope["original_filename" if is_document else "name"] = {
            "$regex": re.escape(query.search), "$options": "i"}
    if is_document:
        if query.status == "indexed": scope["$expr"] = indexed_expression()
        elif query.status == "processing": scope["status"] = "processing"
        elif query.status == "failed": scope["$expr"] = failed_expression()
    field = "original_filename" if query.sort == "filename" else query.sort
    direction = 1 if query.order == "asc" else -1
    stages = [{"$match": scope}, {"$sort": {field: direction, "_id": direction}}]
    item_stages = [{"$skip": (query.page-1)*query.limit}, {"$limit": query.limit}]
    if is_document:
        item_stages.append({"$addFields": {"_browse_indexed": indexed_expression(), "_browse_failed": failed_expression()}})
    # Facet gives count and bounded items from the same pipeline; no N+1 queries.
    stages.append({"$facet": {"items": item_stages, "count": [{"$count": "total"}]}})
    cursor = await database.get_collection("documents" if is_document else "knowledge_bases").aggregate(stages, maxTimeMS=5000)
    result = (await cursor.to_list(length=1))[0]
    total = result["count"][0]["total"] if result["count"] else 0
    items = []
    for row in result["items"]:
        if is_document:
            items.append(DocumentItem(**public_document(row).model_dump(), indexed=row["_browse_indexed"], failed=row["_browse_failed"]))
        else:
            items.append(public_knowledge_base(row))
    return dict(items=items, page=query.page, limit=query.limit, total=total,
                total_pages=(total+query.limit-1)//query.limit)
