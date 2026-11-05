"""Owned Knowledge Base questions; all provider failures are sanitized."""
from bson import ObjectId
import asyncio
from contextlib import nullcontext
from pydantic import ValidationError
import ask_history
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from config import load_rag_settings, load_verified_rag_enabled, ConfigurationError
from search_routes import search_settings
from knowledge_base_routes import parse_id, require_found
import knowledge_bases
from embeddings import EmbeddingFailure
from vector_store import VectorFailure
from gemini_provider import GeminiFailure
from rag_context import RAGFailure
from rag_schemas import AskRequest, AskResponse
from rag import answer_question
from cpu_workers import get_cpu_workers
from expensive_admission import CapacityExceeded, Resource, capacity_unavailable

router = APIRouter(tags=["Questions"])
VERIFIED_REQUEST_SECONDS = 50


def rag_settings():
    try:
        return load_rag_settings()
    except ConfigurationError:
        raise HTTPException(503, "Question-answering configuration is unavailable.") from None


@router.post("/api/knowledge-bases/{knowledge_base_id}/ask", response_model=AskResponse)
async def ask(knowledge_base_id: str, data: AskRequest,
              current_user=Depends(get_current_user), database=Depends(get_database),
              embedding_settings=Depends(search_settings), settings=Depends(rag_settings),
              workers=Depends(get_cpu_workers)):
    owner, base_id = ObjectId(current_user.id), parse_id(knowledge_base_id)
    try:
        enabled = load_verified_rag_enabled()
        deadline = asyncio.get_running_loop().time() + VERIFIED_REQUEST_SECONDS if enabled else None
        async with asyncio.timeout_at(deadline) if enabled else nullcontext():
            require_found(await knowledge_bases.get_knowledge_base(database, owner, base_id))
            with workers.workflow(Resource.QUERY, Resource.GEMINI) as workflow:
                gemini = workflow.reservation.split(Resource.GEMINI)
                options = {"verified": True, "deadline": deadline} if enabled else {}
                result = AskResponse.model_validate(await answer_question(
                    database, owner, base_id, data.question, embedding_settings, settings,
                    workers=workers, workflow=workflow, generation_unneeded=gemini.release, **options))
                if result.citation_version != 1:
                    raise RAGFailure("Invalid answer format.")
                require_found(await ask_history.save_history(database, owner, base_id, data.question, result))
                return result
    except CapacityExceeded:
        raise capacity_unavailable() from None
    except (ConfigurationError, ValidationError, PyMongoError, EmbeddingFailure, VectorFailure, GeminiFailure, RAGFailure, OSError, TimeoutError):
        raise HTTPException(503, "Question-answering service is temporarily unavailable.") from None
