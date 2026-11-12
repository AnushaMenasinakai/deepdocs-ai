"""Explicit optional schema preparation. No clients, activation, or startup writes.

Call in an owned synchronous worker with an explicitly selected database.
Verification is read-only; setup creates missing collections/indexes, never repairs.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from pymongo import timeout
from pymongo.errors import CollectionInvalid, PyMongoError, OperationFailure, WriteConcernError
from storage_contract import StorageUncertain


class StorageSchemaError(StorageUncertain):
    """Schema readiness is unconfirmed; never release capacity based on this error."""
    def __init__(self, message, *, reason='schema_unconfirmed', operation='inspection',
                 index=None, driver_category='none', server_code=None, outcome='unknown'):
        super().__init__(message)
        self.reason = reason
        self.operation = operation
        self.index = index
        self.driver_category = driver_category
        self.server_code = server_code
        self.outcome = outcome


@contextmanager
def _operation(name, index=None):
    """Only fixed operation/reason codes escape; never provider messages/details."""
    try:
        yield
    except StorageSchemaError:
        raise
    except (PyMongoError, OSError, TimeoutError) as error:
        code = getattr(error, 'code', None)
        code = code if type(code) is int and 0 <= code <= 2147483647 else None
        reason = {2: 'bad_value', 9: 'parse_rejected', 13: 'permission_denied', 48: 'namespace_exists',
                  85: 'index_options_conflict', 86: 'index_keys_conflict',
                  67: 'cannot_create_index', 72: 'invalid_options', 197: 'invalid_index_specification',
                  11000: 'duplicate_key', 50: 'deadline_exceeded',
                  64: 'write_concern_unconfirmed', 14031: 'out_of_disk_space'}.get(code)
        if reason is None:
            reason = ('deadline_exceeded' if isinstance(error, TimeoutError)
                      or getattr(error, 'timeout', False) else 'database_operation_unconfirmed')
        category = type(error).__name__
        if category not in DRIVER_CATEGORIES:
            category = 'other_driver_or_os_error'
        # Restrict definitive classification to explicit command rejections.
        # Neither rejection nor readback authorizes compensation or quota release.
        rejected = (isinstance(error, OperationFailure) and not isinstance(error, WriteConcernError)
                    and code in {2, 9, 13, 67, 72, 85, 86, 197, 11000})
        raise StorageSchemaError('Optional storage schema operation requires verification.',
                                 reason=reason, operation=name, index=index,
                                 driver_category=category, server_code=code,
                                 outcome='command_rejected' if rejected else 'unknown') from None


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

DRIVER_CATEGORIES = frozenset({'OperationFailure', 'WriteConcernError', 'WTimeoutError',
    'ExecutionTimeout', 'NetworkTimeout', 'ServerSelectionTimeoutError', 'ConnectionFailure',
    'AutoReconnect', 'NotPrimaryError', 'DuplicateKeyError', 'ConfigurationError',
    'InvalidOperation', 'TimeoutError', 'OSError', 'other_driver_or_os_error'})


def inspect_failed_index(database, error):
    """One bounded readback after an unsuccessful create; never retries a mutation.

    Call outside setup's deadline. Absence is only a point-in-time observation,
    not proof that an in-flight index build cannot finish later.
    """
    index = error.index
    if not isinstance(index, Index) or index not in INDEXES or error.operation != 'create_index':
        return 'unavailable'
    try:
        with timeout(5):
            rows = list(database.get_collection(index.collection).list_indexes())
            matches = [r for r in rows if r.get('name') == index.name]
            if not matches:
                return 'not_observed'
            if len(matches) != 1 or 'buildUUID' in matches[0]:
                return 'unavailable'
            try:
                _check(index, matches[0])
            except StorageSchemaError:
                return 'incompatible_definition'
            return 'confirmed_definition'
    except (PyMongoError, OSError, TimeoutError, TypeError, ValueError, AttributeError):
        return 'unavailable'


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
                             reason=reason, operation='compare_index', index=index,
                             outcome='incompatible_definition')


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
                with _operation('create_index', index):
                    database.get_collection(index.collection).create_index(
                        list(index.keys), name=index.name, unique=index.unique)
            _inspect(database, allow_missing=False)
