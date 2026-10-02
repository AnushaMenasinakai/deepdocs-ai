"""Owned Knowledge Base questions; all provider failures are sanitized."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from config import load_rag_settings, ConfigurationError
from search_routes import search_settings
from knowledge_base_routes import parse_id, require_found
import knowledge_bases
from embeddings import EmbeddingFailure
from vector_store import VectorFailure
from gemini_provider import GeminiFailure
from rag_context import RAGFailure
from rag_schemas import AskRequest, AskResponse
from rag import answer_question

router = APIRouter(tags=["Questions"])


def rag_settings():
    try:
        return load_rag_settings()
    except ConfigurationError:
        raise HTTPException(503, "Question-answering configuration is unavailable.") from None


@router.post("/api/knowledge-bases/{knowledge_base_id}/ask", response_model=AskResponse)
async def ask(knowledge_base_id: str, data: AskRequest,
              current_user=Depends(get_current_user), database=Depends(get_database),
              embedding_settings=Depends(search_settings), settings=Depends(rag_settings)):
    owner, base_id = ObjectId(current_user.id), parse_id(knowledge_base_id)
    try:
        require_found(await knowledge_bases.get_knowledge_base(database, owner, base_id))
        return await answer_question(database, owner, base_id, data.question, embedding_settings, settings)
    except (PyMongoError, EmbeddingFailure, VectorFailure, GeminiFailure, RAGFailure, OSError, TimeoutError):
        raise HTTPException(503, "Question-answering service is temporarily unavailable.") from None
