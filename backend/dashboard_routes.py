"""Authenticated dashboard overview without infrastructure details."""
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from pymongo.errors import PyMongoError
from auth import get_current_user
from database import get_database
from dashboard import DashboardSummary, summary
from dashboard_recent import RecentDocumentsResponse, recent_documents

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


@router.get("/recent-documents", response_model=RecentDocumentsResponse)
async def dashboard_recent_documents(user=Depends(get_current_user), database=Depends(get_database)):
    try:
        return await recent_documents(database, ObjectId(user.id))
    except (PyMongoError, TimeoutError, ValidationError, KeyError, TypeError, AttributeError):
        raise HTTPException(503, "Recent documents are temporarily unavailable.") from None


@router.get("/summary", response_model=DashboardSummary)
async def dashboard_summary(user=Depends(get_current_user), database=Depends(get_database)):
    try:
        return await summary(database, ObjectId(user.id))
    except (PyMongoError, TimeoutError, ValidationError, KeyError, TypeError, AttributeError):
        raise HTTPException(503, "Workspace overview is temporarily unavailable.") from None
