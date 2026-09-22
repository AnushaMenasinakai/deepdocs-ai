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
