"""Bounded owner-scoped workspace aggregates; no model or remote vector calls."""
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class RecentKnowledgeBase(BaseModel):
    id: str
    name: str
    document_count: int = Field(ge=0)
    updated_at: datetime


class DashboardSummary(BaseModel):
    knowledge_base_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    indexed_document_count: int = Field(ge=0)
    processing_document_count: int = Field(ge=0)
    failed_document_count: int = Field(ge=0)
    ask_history_count: int = Field(ge=0)
    recent_knowledge_bases: list[RecentKnowledgeBase] = Field(max_length=5)


def indexed_expression():
    # Last confirmed synchronization for the active generation, not a live Qdrant audit.
    return {"$and": [
        {"$eq": ["$status", "processed"]},
        {"$eq": [{"$type": "$_operation"}, "missing"]},
        {"$eq": [{"$type": "$chunk_generation"}, "objectId"]},
        {"$eq": ["$vector_index.status", "indexed"]},
        {"$eq": ["$vector_index.chunk_generation", "$chunk_generation"]},
        {"$in": [{"$type": "$chunk_count"}, ["int", "long"]]},
        {"$gt": ["$chunk_count", 0]},
        {"$eq": ["$vector_index.chunk_count", "$chunk_count"]},
        {"$in": [{"$type": "$vector_index.embedding_dimension"}, ["int", "long"]]},
        {"$gt": ["$vector_index.embedding_dimension", 0]},
        {"$eq": [{"$type": "$vector_index.indexed_at"}, "date"]},
        *[{"$and": [{"$eq": [{"$type": "$vector_index."+key}, "string"]},
                     {"$ne": ["$vector_index."+key, ""]}]} for key in ("embedding_model", "collection_name", "target")],
        {"$eq": ["$embedding.status", "generated"]},
        {"$eq": ["$embedding.chunk_generation", "$chunk_generation"]},
        {"$eq": ["$embedding.model", "$vector_index.embedding_model"]},
        {"$eq": ["$embedding.dimension", "$vector_index.embedding_dimension"]},
        {"$eq": ["$embedding.chunk_count", "$chunk_count"]},
    ]}


async def summary(database, owner):
    scope = {"owner_id": owner}
    kb_count = await database.get_collection("knowledge_bases").count_documents(scope)
    history_count = await database.get_collection("ask_history").count_documents(scope)
    counts_cursor = await database.get_collection("documents").aggregate([
        {"$match": scope}, {"$group": {"_id": None, "document_count": {"$sum": 1},
            "indexed_document_count": {"$sum": {"$cond": [indexed_expression(), 1, 0]}},
            "processing_document_count": {"$sum": {"$cond": [{"$eq": ["$status", "processing"]}, 1, 0]}},
            "failed_document_count": {"$sum": {"$cond": [{"$or": [
                {"$eq": ["$status", "failed"]}, {"$eq": ["$embedding.status", "failed"]},
                {"$eq": ["$vector_index.status", "failed"]}]}, 1, 0]}}}},
    ], maxTimeMS=5000)
    counts = await counts_cursor.to_list(length=1)
    totals = {key: counts[0][key] if counts else 0 for key in
              ("document_count", "indexed_document_count", "processing_document_count", "failed_document_count")}
    recent_cursor = await database.get_collection("knowledge_bases").aggregate([
        {"$match": scope}, {"$sort": {"updated_at": -1, "_id": -1}}, {"$limit": 5},
        {"$lookup": {"from": "documents", "let": {"base": "$_id"}, "pipeline": [
            {"$match": {"owner_id": owner, "$expr": {"$eq": ["$knowledge_base_id", "$$base"]}}},
            {"$count": "count"}], "as": "document_totals"}},
        {"$project": {"name": 1, "updated_at": 1, "document_count": {
            "$ifNull": [{"$arrayElemAt": ["$document_totals.count", 0]}, 0]}}},
    ], maxTimeMS=5000)
    recent = []
    async for row in recent_cursor:
        date = row["updated_at"]
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        recent.append(RecentKnowledgeBase(id=str(row["_id"]), name=row["name"],
                      updated_at=date, document_count=row["document_count"]))
    return DashboardSummary(knowledge_base_count=kb_count, ask_history_count=history_count,
                            recent_knowledge_bases=recent, **totals)
