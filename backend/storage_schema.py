"""Explicit optional schema preparation. No clients, activation, or startup writes.

Call in an owned synchronous worker with an explicitly selected database.
Verification is read-only; setup creates missing collections/indexes, never repairs.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from pymongo import timeout
from pymongo.errors import CollectionInvalid, PyMongoError
from storage_contract import StorageUncertain


class StorageSchemaError(StorageUncertain):
    """Schema readiness is unconfirmed; never release capacity based on this error."""
    def __init__(self, message, *, reason='schema_unconfirmed', operation='inspection'):
        super().__init__(message)
        self.reason = reason
        self.operation = operation


@contextmanager
def _operation(name):
    """Only fixed operation/reason codes escape; never provider messages/details."""
    try:
        yield
    except StorageSchemaError:
        raise
    except (PyMongoError, OSError, TimeoutError) as error:
        reason = {13: 'permission_denied', 48: 'namespace_exists',
                  85: 'index_options_conflict', 86: 'index_keys_conflict',
                  11000: 'duplicate_key', 50: 'deadline_exceeded',
                  64: 'write_concern_unconfirmed'}.get(getattr(error, 'code', None))
        if reason is None:
            reason = ('deadline_exceeded' if isinstance(error, TimeoutError)
                      or getattr(error, 'timeout', False) else 'database_operation_unconfirmed')
        raise StorageSchemaError('Optional storage schema operation requires verification.',
                                 reason=reason, operation=name) from None


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
    if tuple(actual.get('key', {}).items()) != index.keys:
        reason = 'index_keys_mismatch'
    elif type(unique) is not bool or unique != index.unique:
        reason = 'index_uniqueness_mismatch'
    elif (any(k in actual for k in ('partialFilterExpression', 'expireAfterSeconds', 'collation'))
            or actual.get('sparse', False) is not False
            or actual.get('hidden', False) is not False):
        reason = 'index_options_mismatch'
    else:
        return
    raise StorageSchemaError('Incompatible optional storage index definition.',
                             reason=reason, operation='compare_index')


def _inspect(database, *, allow_missing):
    with _operation('list_collections'):
        present = set(database.list_collection_names())
    missing = []
    for collection in COLLECTIONS:
        with _operation('list_indexes'):
            actual = {} if collection not in present else {
                i['name']: i for i in database.get_collection(collection).list_indexes()}
        for index in (i for i in INDEXES if i.collection == collection):
            if index.name in actual:
                _check(index, actual[index.name])
            else:
                # A differently named equivalent index is not the required hinted index.
                if any(tuple(i.get('key', {}).items()) == index.keys for i in actual.values()):
                    raise StorageSchemaError('Optional storage index name conflict.',
                                             reason='index_name_conflict', operation='compare_index')
                missing.append(index)
    if missing and not allow_missing:
        raise StorageSchemaError('Required optional storage indexes are missing.',
                                 reason='indexes_missing', operation='inspect_manifest')
    return present, missing


def verify_storage_schema(database):
    """Read-only, bounded verification; does not establish transaction capability."""
    with _operation('verify_schema'):
        with timeout(5):
            _inspect(database, allow_missing=False)


def setup_storage_schema(database):
    """Explicit opt-in DDL, outside transactions. Safe to repeat after inspection.

    Partial setup or acknowledgement loss requires verification, not index deletion.
    """
    with _operation('setup_schema'):
        with timeout(5):
            present, _ = _inspect(database, allow_missing=True)
            for collection in COLLECTIONS:
                if collection not in present:
                    with _operation('create_collection'):
                        try:
                            database.create_collection(collection)
                        except CollectionInvalid:
                            pass  # Concurrent creation is acceptable only after reinspection.
            _, missing = _inspect(database, allow_missing=True)
            for index in missing:
                if index.name == '_id_':
                    raise StorageSchemaError('Required intrinsic identity index is missing.',
                                             reason='identity_index_missing', operation='inspect_manifest')
                with _operation('create_index'):
                    database.get_collection(index.collection).create_index(
                        list(index.keys), name=index.name, unique=index.unique)
            _inspect(database, allow_missing=False)
