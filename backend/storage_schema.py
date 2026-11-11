"""Explicit optional schema preparation. No clients, activation, or startup writes.

Call in an owned synchronous worker with an explicitly selected database.
Verification is read-only; setup creates missing collections/indexes, never repairs.
"""
from dataclasses import dataclass
from pymongo import timeout
from pymongo.errors import CollectionInvalid, PyMongoError
from storage_contract import StorageUncertain


class StorageSchemaError(StorageUncertain):
    """Schema readiness is unconfirmed; never release capacity based on this error."""


@dataclass(frozen=True)
class Index:
    collection: str
    name: str
    keys: tuple
    unique: bool = False


COLLECTIONS = ('storage_operation_intents', 'storage_quota_ledger',
               'pdf_blobs.files', 'pdf_blobs.chunks', 'document_chunks')
INDEXES = tuple(Index(c, '_id_', (('_id', 1),), True) for c in COLLECTIONS) + (
    Index('storage_operation_intents', 'storage_intent_owner_document',
          (('owner_id', 1), ('document_id', 1), ('created_at', 1))),
    Index('storage_operation_intents', 'storage_intent_reconciliation',
          (('reconciliation_status', 1), ('updated_at', 1))),
    Index('pdf_blobs.files', 'filename_1_uploadDate_1', (('filename', 1), ('uploadDate', 1))),
    Index('pdf_blobs.chunks', 'files_id_1_n_1', (('files_id', 1), ('n', 1)), True),
    Index('document_chunks', 'chunks_owner_document_generation_order',
          (('owner_id', 1), ('document_id', 1), ('generation', 1), ('chunk_index', 1))),
)


def _check(index, actual):
    # MongoDB's intrinsic _id uniqueness need not be serialized as unique:true.
    unique = actual.get('unique', index.name == '_id_')
    if (tuple(actual.get('key', {}).items()) != index.keys
            or type(unique) is not bool or unique != index.unique
            or any(k in actual for k in ('partialFilterExpression', 'expireAfterSeconds', 'collation'))
            or actual.get('sparse', False) is not False
            or actual.get('hidden', False) is not False):
        raise StorageSchemaError('Incompatible optional storage index definition.')


def _inspect(database, *, allow_missing):
    present = set(database.list_collection_names())
    missing = []
    for collection in COLLECTIONS:
        actual = {} if collection not in present else {
            i['name']: i for i in database.get_collection(collection).list_indexes()}
        for index in (i for i in INDEXES if i.collection == collection):
            if index.name in actual:
                _check(index, actual[index.name])
            else:
                # A differently named equivalent index is not the required hinted index.
                if any(tuple(i.get('key', {}).items()) == index.keys for i in actual.values()):
                    raise StorageSchemaError('Optional storage index name conflict.')
                missing.append(index)
    if missing and not allow_missing:
        raise StorageSchemaError('Required optional storage indexes are missing.')
    return present, missing


def verify_storage_schema(database):
    """Read-only, bounded verification; does not establish transaction capability."""
    try:
        with timeout(5):
            _inspect(database, allow_missing=False)
    except (PyMongoError, OSError, TimeoutError):
        raise StorageSchemaError('Optional storage schema verification unavailable.') from None


def setup_storage_schema(database):
    """Explicit opt-in DDL, outside transactions. Safe to repeat after inspection.

    Partial setup or acknowledgement loss requires verification, not index deletion.
    """
    try:
        with timeout(5):
            present, _ = _inspect(database, allow_missing=True)
            for collection in COLLECTIONS:
                if collection not in present:
                    try:
                        database.create_collection(collection)
                    except CollectionInvalid:
                        pass  # Concurrent creation is acceptable only after reinspection.
            _, missing = _inspect(database, allow_missing=True)
            for index in missing:
                if index.name == '_id_':
                    raise StorageSchemaError('Required intrinsic identity index is missing.')
                database.get_collection(index.collection).create_index(
                    list(index.keys), name=index.name, unique=index.unique)
            _inspect(database, allow_missing=False)
    except (PyMongoError, OSError, TimeoutError):
        raise StorageSchemaError('Optional storage setup outcome requires verification.') from None
