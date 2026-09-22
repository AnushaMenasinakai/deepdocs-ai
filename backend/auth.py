"""JWT signing/validation and the reusable current-user dependency."""
from datetime import datetime, timedelta, timezone

import jwt
from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from config import ConfigurationError, JWTSettings, load_jwt_settings
from database import get_database
from user_schemas import UserResponse
from users import find_user_by_id, public_user


def authentication_error(message="Invalid authentication credentials"):
    return HTTPException(401, message, headers={"WWW-Authenticate": "Bearer"})


def get_auth_settings() -> JWTSettings:
    try:
        return load_jwt_settings()
    except ConfigurationError:
        raise HTTPException(503, "Authentication configuration is unavailable.") from None


def create_access_token(user_id: ObjectId, settings: JWTSettings) -> str:
    if not isinstance(user_id, ObjectId):
        raise ValueError("A valid user ID is required.")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now,
         "exp": now + timedelta(minutes=settings.access_token_expire_minutes)},
        settings.secret_key, algorithm=settings.algorithm,
    )


bearer_scheme = HTTPBearer(auto_error=False, bearerFormat="JWT")


def token_subject(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ObjectId:
    # HTTPBearer declares OpenAPI security. Keep manual parsing authoritative so
    # duplicate-header, whitespace, and generic 401 behavior remain unchanged.
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        raise authentication_error()
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise authentication_error()
    settings = get_auth_settings()
    try:
        claims = jwt.decode(
            parts[1], settings.secret_key, algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not ObjectId.is_valid(subject):
            raise authentication_error()
        # NumericDate must be an integer here, not a coerced string or boolean.
        if any(type(claims[key]) is not int for key in ("iat", "exp")):
            raise authentication_error()
        if claims["exp"] <= claims["iat"]:
            raise authentication_error()
        return ObjectId(subject)
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        raise authentication_error() from None


async def get_current_user(
    subject: ObjectId = Depends(token_subject),
    database: AsyncDatabase = Depends(get_database),
) -> UserResponse:
    try:
        document = await find_user_by_id(database, subject)
    except (PyMongoError, TimeoutError):
        raise HTTPException(503, "Authentication is temporarily unavailable.") from None
    if document is None:
        raise authentication_error()
    return public_user(document)
