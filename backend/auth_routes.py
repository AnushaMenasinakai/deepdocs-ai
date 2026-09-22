"""Registration, access-token login, and current-user routes."""
from argon2.exceptions import HashingError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from database import get_database
from user_schemas import LoginRequest, RegistrationRequest, TokenResponse, UserResponse
from auth import authentication_error, create_access_token, get_auth_settings, get_current_user
from config import JWTSettings
from security import verify_password
from users import create_user, find_user_by_email

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


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


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    settings: JWTSettings = Depends(get_auth_settings),
    database: AsyncDatabase = Depends(get_database),
):
    try:
        document = await find_user_by_email(database, str(credentials.email))
    except (PyMongoError, TimeoutError):
        raise HTTPException(503, "Authentication is temporarily unavailable.") from None
    stored_hash = document.get("password_hash") if document else None
    valid = await verify_password(credentials.password.get_secret_value(), stored_hash)
    if not valid or document is None:
        raise authentication_error("Invalid email or password")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=create_access_token(document["_id"], settings))


@router.get("/me", response_model=UserResponse)
async def me(response: Response, user: UserResponse = Depends(get_current_user)):
    response.headers["Cache-Control"] = "no-store"
    return user
