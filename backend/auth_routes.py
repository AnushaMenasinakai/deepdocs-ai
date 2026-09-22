"""Registration only; no sessions, login, or tokens."""
from argon2.exceptions import HashingError
from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from database import get_database
from user_schemas import RegistrationRequest, UserResponse
from users import create_user

router = APIRouter(prefix="/api/auth", tags=["Registration"])


@router.post("/register", status_code=201, response_model=UserResponse)
async def register(
    registration: RegistrationRequest,
    request: Request,
    database: AsyncDatabase = Depends(get_database),
):
    if not getattr(request.app.state, "users_index_ready", False):
        raise HTTPException(503, "Registration is unavailable. Please try again later.")
    try:
        return await create_user(database, registration)
    except DuplicateKeyError:
        # The database unique index is the final arbiter, including concurrent requests.
        raise HTTPException(409, "Registration conflicts with an existing account.") from None
    except (PyMongoError, TimeoutError):
        raise HTTPException(503, "Registration is unavailable. Please try again later.") from None
    except (HashingError, UnicodeError):
        raise HTTPException(500, "Registration could not be completed.") from None
