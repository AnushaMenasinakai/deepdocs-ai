"""Authenticated Knowledge Base CRUD; ownership is always server-derived."""
from contextlib import contextmanager
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from user_schemas import UserResponse
from knowledge_base_schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseResponse
import knowledge_bases

router = APIRouter(prefix="/api/knowledge-bases", tags=["Knowledge Bases"])


@contextmanager
def safe_database_errors():
    try:
        yield
    except (PyMongoError, TimeoutError):
        raise HTTPException(503, "Knowledge Base service is temporarily unavailable.") from None


def parse_id(knowledge_base_id: str) -> ObjectId:
    if not ObjectId.is_valid(knowledge_base_id):
        raise HTTPException(422, "Invalid Knowledge Base ID.")
    return ObjectId(knowledge_base_id)


def require_found(result):
    if result is None or result is False:
        raise HTTPException(404, "Knowledge Base not found.")
    return result


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create(
    data: KnowledgeBaseCreate,
    current_user: UserResponse = Depends(get_current_user),
    database: AsyncDatabase = Depends(get_database),
):
    with safe_database_errors():
        return await knowledge_bases.create_knowledge_base(database, ObjectId(current_user.id), data)


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_owned(
    current_user: UserResponse = Depends(get_current_user),
    database: AsyncDatabase = Depends(get_database),
):
    with safe_database_errors():
        return await knowledge_bases.list_knowledge_bases(database, ObjectId(current_user.id))


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_one(
    knowledge_base_id: str,
    current_user: UserResponse = Depends(get_current_user),
    database: AsyncDatabase = Depends(get_database),
):
    resource_id = parse_id(knowledge_base_id)
    with safe_database_errors():
        return require_found(await knowledge_bases.get_knowledge_base(
            database, ObjectId(current_user.id), resource_id,
        ))


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update(
    knowledge_base_id: str, data: KnowledgeBaseUpdate,
    current_user: UserResponse = Depends(get_current_user),
    database: AsyncDatabase = Depends(get_database),
):
    resource_id = parse_id(knowledge_base_id)
    with safe_database_errors():
        return require_found(await knowledge_bases.update_knowledge_base(
            database, ObjectId(current_user.id), resource_id, data,
        ))


@router.delete("/{knowledge_base_id}", status_code=204)
async def delete(
    knowledge_base_id: str,
    current_user: UserResponse = Depends(get_current_user),
    database: AsyncDatabase = Depends(get_database),
):
    resource_id = parse_id(knowledge_base_id)
    with safe_database_errors():
        require_found(await knowledge_bases.delete_knowledge_base(
            database, ObjectId(current_user.id), resource_id,
        ))
    return Response(status_code=204)
