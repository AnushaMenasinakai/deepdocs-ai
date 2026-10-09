"""Atomic claims; uncertain acquisition is verified, never replayed or unlocked."""
import asyncio
from bson import ObjectId
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference

VERIFICATION_TIMEOUT_SECONDS = 5


class DocumentBusy(ValueError):
    pass


class ClaimUncertain(PyMongoError):
    """Maps to existing sanitized 503/bulk service_unavailable handling."""

    def __init__(self):
        super().__init__("Document claim outcome could not be confirmed.")


async def claim_document(database, owner_id, document_id, expected_knowledge_base_id=None):
    collection = database.get_collection("documents")
    token = ObjectId()
    owned = {"_id": document_id, "owner_id": owner_id}
    if expected_knowledge_base_id is not None:
        owned["knowledge_base_id"] = expected_knowledge_base_id
    # Do not change global concerns/retry settings. Unsupported unacknowledged
    # writes are rejected before dispatch rather than treated as confirmed claims.
    concern = getattr(collection, "write_concern", None)
    if concern is not None and not concern.acknowledged:
        raise ClaimUncertain()
    try:
        document = await collection.find_one_and_update(
            {**owned, "_operation": {"$exists": False}},
            {"$set": {"_operation": token}},
        )
    except asyncio.CancelledError:
        # A cancelled local await cannot prove the remote write stopped. No
        # verification/unlock/retry or downstream handoff on cancellation.
        raise
    except (PyMongoError, OSError, TimeoutError):
        # Exception classes/error labels alone cannot establish non-commit
        # across all driver attempts. Only positive exact-token evidence resumes.
        try:
            async with asyncio.timeout(VERIFICATION_TIMEOUT_SECONDS):
                verifier = collection.with_options(
                    read_preference=ReadPreference.PRIMARY,
                    read_concern=ReadConcern("majority"),
                )
                document = await verifier.find_one({**owned, "_operation": token})
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ClaimUncertain() from None
        if document is None or any(document.get(key) != value for key, value in {**owned, "_operation": token}.items()):
            # Absence/different ownership/token is not proof a delayed write
            # cannot commit. Do not convert this into not_found or busy.
            raise ClaimUncertain() from None
        # The claim changes only _operation. Return a detached business-state
        # snapshot without our new token, matching the normal pre-image contract.
        document = dict(document)
        document.pop("_operation")
    # Defensive cancellation check for drivers/adapters that returned after
    # cancellation was requested. Retain uncertainty rather than starting work.
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    if document is None and await collection.find_one(owned) is not None:
        raise DocumentBusy()
    return document, token


async def release_document(database, owner_id, document_id, token):
    await database.get_collection("documents").update_one(
        {"_id": document_id, "owner_id": owner_id, "_operation": token},
        {"$unset": {"_operation": ""}},
    )
