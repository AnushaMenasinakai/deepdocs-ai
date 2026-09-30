"""Authenticated retrieval only: no answer generation or search side effects."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from config import load_embedding_settings, ConfigurationError
from embeddings import EmbeddingFailure
from vector_store import VectorFailure
from knowledge_base_routes import parse_id, require_found
import knowledge_bases
from search_schemas import SearchRequest, SearchResponse
from retrieval import search_chunks

router = APIRouter(tags=["Search"])


def search_settings():
    try:
        return load_embedding_settings()
    except ConfigurationError:
        raise HTTPException(503, "Search configuration is unavailable.") from None


@router.post("/api/knowledge-bases/{knowledge_base_id}/search", response_model=SearchResponse)
async def search(knowledge_base_id: str, data: SearchRequest,
                 current_user=Depends(get_current_user), database=Depends(get_database),
                 settings=Depends(search_settings)):
    owner_id, base_id = ObjectId(current_user.id), parse_id(knowledge_base_id)
    try:
        require_found(await knowledge_bases.get_knowledge_base(database, owner_id, base_id))
        results = await search_chunks(database, owner_id, base_id, data, settings)
        return SearchResponse(query=data.query, results=results)
    except (PyMongoError, EmbeddingFailure, VectorFailure, OSError, TimeoutError):
        raise HTTPException(503, "Search service is temporarily unavailable.") from None
