"""History reads/deletes require both authenticated ownership and an owned KB."""
from contextlib import contextmanager
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from knowledge_base_routes import parse_id, require_found
from history_schemas import HistoryResponse
import knowledge_bases
import ask_history

router = APIRouter(prefix="/api/knowledge-bases/{knowledge_base_id}/ask-history", tags=["Ask history"])


@contextmanager
def safe_history_errors():
    try:
        yield
    except (PyMongoError, TimeoutError, ValidationError):
        raise HTTPException(503, "Ask history is temporarily unavailable.") from None


async def owned_base(knowledge_base_id: str, user=Depends(get_current_user), database=Depends(get_database)):
    owner, base = ObjectId(user.id), parse_id(knowledge_base_id)
    with safe_history_errors():
        require_found(await knowledge_bases.get_knowledge_base(database, owner, base))
    return database, owner, base


def history_id(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(422, "Invalid history ID.")
    return ObjectId(value)


@router.get("", response_model=list[HistoryResponse])
async def list_owned(limit: int = Query(20, ge=1, le=100), owned=Depends(owned_base)):
    with safe_history_errors():
        return await ask_history.list_history(*owned, limit)


@router.get("/{entry_id}", response_model=HistoryResponse)
async def get_one(entry_id: str, owned=Depends(owned_base)):
    with safe_history_errors():
        result = await ask_history.get_history(*owned, history_id(entry_id))
        if result is None:
            raise HTTPException(404, "Ask history not found.")
        return result


@router.delete("/{entry_id}", status_code=204)
async def delete_one(entry_id: str, owned=Depends(owned_base)):
    with safe_history_errors():
        if not await ask_history.delete_history(*owned, history_id(entry_id)):
            raise HTTPException(404, "Ask history not found.")
    return Response(status_code=204)
