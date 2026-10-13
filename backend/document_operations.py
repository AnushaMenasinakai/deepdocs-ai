"""Atomic claims; uncertain acquisition is verified, never replayed or unlocked."""
import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference

VERIFICATION_TIMEOUT_SECONDS = 5
OPERATION_TYPES = frozenset({"processing", "indexing", "deletion", "vector_inspection", "unknown"})
STAGES = frozenset({"claimed", "validation", "extraction", "chunk_insertion",
    "vector_cleanup", "activation", "obsolete_cleanup", "encoding", "collection_setup",
    "indexing_metadata", "vector_replacement", "count_verification", "final_publication",
    "vector_invalidation", "vector_deletion", "pdf_deletion", "chunk_deletion",
    "reservation_release", "metadata_deletion", "inspection", "finalization"})
ERROR_CATEGORIES = frozenset({"validation", "database_unavailable", "vector_unavailable",
    "storage_unavailable", "model_unavailable", "cancelled", "unknown"})


class OperationMetadataFailure(PyMongoError):
    def __init__(self):
        super().__init__("Operation metadata could not be confirmed.")


def allowed(value, choices):
    return isinstance(value, str) and value in choices


class DocumentBusy(ValueError):
    pass


class ClaimUncertain(PyMongoError):
    """Maps to existing sanitized 503/bulk service_unavailable handling."""

    def __init__(self):
        super().__init__("Document claim outcome could not be confirmed.")


async def claim_document(database, owner_id, document_id, expected_knowledge_base_id=None, *, operation_type="unknown"):
    if not allowed(operation_type, OPERATION_TYPES):
        raise OperationMetadataFailure()
    collection = database.get_collection("documents")
    token = ObjectId()
    now = datetime.now(timezone.utc)
    metadata = {"schema_version": 1, "token": token, "type": operation_type,
                "started_at": now, "stage": "claimed", "stage_updated_at": now,
                "stage_sequence": 0, "last_acknowledged_stage": None,
                "outcome": "in_progress", "error_category": None}
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
            {"$set": {"_operation": token, "_operation_meta": metadata}},
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
        verified = document.get("_operation_meta")
        if (not isinstance(verified, dict) or verified.get("token") != token
                or type(verified.get("schema_version")) is not int
                or verified["schema_version"] != 1):
            raise ClaimUncertain() from None
        # Diagnostic fields are not part of the business-state pre-image.
        document = dict(document)
        document.pop("_operation")
    # Defensive cancellation check for drivers/adapters that returned after
    # cancellation was requested. Retain uncertainty rather than starting work.
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    if document is None and await collection.find_one(owned) is not None:
        raise DocumentBusy()
    if document is not None:
        document = dict(document)
        document.pop("_operation_meta", None)
    return document, token


async def release_document(database, owner_id, document_id, token):
    await database.get_collection("documents").update_one(
        {"_id": document_id, "owner_id": owner_id, "_operation": token},
        {"$unset": {"_operation": "", "_operation_meta": ""}},
    )


async def record_operation_stage(database, owner_id, document_id, token, expected_sequence,
                                 stage, *, expected_knowledge_base_id=None,
                                 last_acknowledged_stage=None, outcome="in_progress", error_category=None):
    """Diagnostic compare-and-set only. Failure never authorizes unlock or replay."""
    if (type(expected_sequence) is not int or not 0 <= expected_sequence < 2**31 - 1
            or not isinstance(token, ObjectId) or not allowed(stage, STAGES)
            or (last_acknowledged_stage is not None and not allowed(last_acknowledged_stage, STAGES))
            or not allowed(outcome, {"in_progress", "uncertain"})
            or (error_category is not None and not allowed(error_category, ERROR_CATEGORIES))):
        raise OperationMetadataFailure()
    collection = database.get_collection("documents")
    concern = getattr(collection, "write_concern", None)
    if concern is not None and not concern.acknowledged:
        raise OperationMetadataFailure()
    query = {"_id": document_id, "owner_id": owner_id, "_operation": token,
             "_operation_meta.token": token, "_operation_meta.schema_version": 1,
             "_operation_meta.stage_sequence": expected_sequence}
    if expected_knowledge_base_id is not None:
        query["knowledge_base_id"] = expected_knowledge_base_id
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    try:
        result = await collection.update_one(query, {"$set": {
            "_operation_meta.stage": stage,
            "_operation_meta.stage_updated_at": datetime.now(timezone.utc),
            "_operation_meta.stage_sequence": expected_sequence + 1,
            "_operation_meta.last_acknowledged_stage": last_acknowledged_stage,
            "_operation_meta.outcome": outcome,
            "_operation_meta.error_category": error_category,
        }})
        if result.matched_count != 1:
            raise OperationMetadataFailure()
    except asyncio.CancelledError:
        raise
    except Exception:
        raise OperationMetadataFailure() from None
    if task is not None and task.cancelling():
        raise asyncio.CancelledError()
    return expected_sequence + 1
