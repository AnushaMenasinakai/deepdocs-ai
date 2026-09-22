"""Small MongoDB operations for the users collection; no ODM."""
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase

from security import hash_password
from user_schemas import RegistrationRequest, UserResponse


async def ensure_user_indexes(database: AsyncDatabase) -> None:
    # A stable name makes repeat startup idempotent. Never run this per request.
    await database.get_collection("users").create_index(
        [("email", ASCENDING)], unique=True, name="users_email_unique",
    )


async def create_user(database: AsyncDatabase, registration: RegistrationRequest) -> UserResponse:
    password_hash = await hash_password(registration.password.get_secret_value())
    # Explicit allowlist: never insert a dump of the request model.
    document = {
        "_id": ObjectId(),
        "name": registration.name,
        "email": str(registration.email),
        "password_hash": password_hash,
        "created_at": datetime.now(timezone.utc),
    }
    await database.get_collection("users").insert_one(document)
    return UserResponse(
        id=str(document["_id"]),
        name=document["name"],
        email=document["email"],
        created_at=document["created_at"],
    )


async def find_user_by_email(database: AsyncDatabase, email: str):
    return await database.get_collection("users").find_one(
        {"email": email}, {"_id": 1, "password_hash": 1},
    )


async def find_user_by_id(database: AsyncDatabase, user_id: ObjectId):
    return await database.get_collection("users").find_one(
        {"_id": user_id}, {"_id": 1, "name": 1, "email": 1, "created_at": 1},
    )


def public_user(document) -> UserResponse:
    # BSON dates may be decoded as naive UTC by the existing client.
    created_at = document["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return UserResponse(
        id=str(document["_id"]), name=document["name"],
        email=document["email"], created_at=created_at,
    )
