"""Disconnected synchronous metadata transactions; never performs GridFS IO.

Use from owned worker threads. Transaction support must be validated before use.
No with_transaction callback retries, TTL expiry, orphan deletion or client creation.
"""
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from storage_contract import StorageUncertain


class IntentConflict(ValueError):
    pass


class QuotaExceeded(ValueError):
    pass


@dataclass(frozen=True)
class QuotaPolicy:
    global_bytes: int = 50 * 1024**2
    user_bytes: int = 10 * 1024**2
    file_bytes: int = 5 * 1024**2
    global_documents: int = 20
    user_documents: int = 5

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError('Invalid quota policy.')
        if not self.file_bytes <= self.user_bytes <= self.global_bytes or not self.user_documents <= self.global_documents <= 20:
            raise ValueError('Invalid bounded quota policy.')


@dataclass(frozen=True)
class IntentIdentity:
    operation_id: ObjectId
    document_id: ObjectId
    owner_id: ObjectId
    knowledge_base_id: ObjectId
    operation_type: str = 'upload'
    backend: str = 'gridfs'

    def __post_init__(self):
        if any(not isinstance(v, ObjectId) for v in
               (self.operation_id, self.document_id, self.owner_id, self.knowledge_base_id)):
            raise ValueError('Server-owned ObjectIds required.')
        if self.operation_type not in ('upload', 'delete') or self.backend != 'gridfs':
            raise ValueError('Unsupported storage intent.')

    @classmethod
    def new(cls, document_id, owner_id, knowledge_base_id, operation_type='upload'):
        return cls(ObjectId(), document_id, owner_id, knowledge_base_id, operation_type)

    def fields(self):
        value = asdict(self)
        value['_id'] = value.pop('operation_id')
        value['file_id'] = self.document_id
        return value


EDGES = {
    'upload': {'prepared': {'write_dispatched', 'failed_before_dispatch'},
               'write_dispatched': {'file_confirmed'}, 'file_confirmed': {'published'},
               'published': set(), 'failed_before_dispatch': set(),
               'delete_confirmed': set()},
    'delete': {'prepared': {'delete_dispatched', 'failed_before_dispatch'},
               'delete_dispatched': {'delete_confirmed', 'vectors_deleted'},
               'vectors_deleted': {'file_deleted'}, 'file_deleted': {'delete_confirmed'},
               'delete_confirmed': set(),
               'failed_before_dispatch': set()},
}
ERRORS = {'database_unavailable', 'storage_unavailable', 'cancelled', 'unknown'}


class StorageIntents:
    def __init__(self, database, policy=QuotaPolicy()):
        self.db, self.policy = database, policy
        self.intents = database.get_collection('storage_operation_intents')
        self.ledger = database.get_collection('storage_quota_ledger')

    def ensure_indexes(self):
        """Explicit future setup only; constructor never creates collections/indexes."""
        try:
            with timeout(5):
                self.intents.create_index([('owner_id',1), ('document_id',1), ('created_at',1)],
                                          name='storage_intent_owner_document')
                self.intents.create_index([('reconciliation_status',1), ('updated_at',1)],
                                          name='storage_intent_reconciliation')
        except (PyMongoError, OSError, TimeoutError):
            raise StorageUncertain('Storage index setup requires verification.') from None

    @contextmanager
    def transaction(self):
        try:
            with timeout(5), self.db.client.start_session() as session:
                with session.start_transaction(read_concern=ReadConcern('snapshot'),
                                               write_concern=WriteConcern('majority'),
                                               max_commit_time_ms=5000):
                    yield session
        except (PyMongoError, OSError, TimeoutError):
            raise StorageUncertain('Storage metadata outcome requires reconciliation.') from None

    def _record(self, identity, session):
        record = self.intents.find_one({'_id': identity.operation_id}, session=session)
        if record is None or any(record.get(k) != v for k, v in identity.fields().items()):
            raise IntentConflict('Storage intent identity conflict.')
        return record

    def _ledger(self, session):
        row = self.ledger.find_one({'_id': 'pdf_payload_v1'}, session=session)
        if row is None:
            row = {'_id': 'pdf_payload_v1', 'policy': asdict(self.policy), 'entries': [], 'revision': 0}
        if row['policy'] != asdict(self.policy) or len(row['entries']) > self.policy.global_documents:
            raise IntentConflict('Quota policy or ledger requires review.')
        return row

    def _write_ledger(self, row, session):
        row['revision'] += 1
        self.ledger.replace_one({'_id': row['_id']}, row, upsert=True, session=session)

    def create(self, identity, expected_size, checksum, worker_id, reservation_id=None):
        if type(expected_size) is not int or not 0 < expected_size <= self.policy.file_bytes:
            raise QuotaExceeded('PDF size exceeds quota.')
        if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Expected SHA256 required.')
        if not isinstance(worker_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', worker_id):
            raise ValueError('Invalid worker identity.')
        reservation_id = reservation_id or identity.operation_id
        if not isinstance(reservation_id, ObjectId):
            raise ValueError('Invalid reservation reference.')
        if identity.operation_type == 'upload' and reservation_id != identity.operation_id:
            raise ValueError('Upload owns its reservation.')
        with self.transaction() as session:
            old = self.intents.find_one({'_id': identity.operation_id}, session=session)
            if old is not None:
                self._record(identity, session)
                if any(old[k] != v for k, v in {'expected_size':expected_size, 'checksum':checksum,
                                               'reservation_id':reservation_id, 'worker_id':worker_id}.items()):
                    raise IntentConflict('Conflicting duplicate intent.')
                return old
            now = datetime.now(timezone.utc)
            record = {**identity.fields(), 'schema_version':1, 'stage':'prepared',
                      'last_acknowledged_stage':'prepared', 'expected_size':expected_size,
                      'checksum':checksum, 'reservation_id':reservation_id, 'worker_id':worker_id,
                      'created_at':now, 'updated_at':now, 'heartbeat_at':now,
                      'error_category':None, 'reconciliation_status':'none', 'quota_state':'none'}
            self.intents.insert_one(record, session=session)
            return record

    def reserve(self, identity):
        with self.transaction() as session:
            record = self._record(identity, session)
            if identity.operation_type != 'upload' or record['reconciliation_status'] != 'none':
                raise IntentConflict('Reservation requires a certain upload intent.')
            if record['quota_state'] in ('reserved', 'committed'):
                return record['quota_state']
            if record['quota_state'] != 'none' or record['stage'] != 'prepared':
                raise IntentConflict('Reservation cannot be reopened.')
            row = self._ledger(session)
            entries = row['entries']
            if any(e['document_id'] == identity.document_id for e in entries):
                raise IntentConflict('Document already has a reservation.')
            own = [e for e in entries if e['owner_id'] == identity.owner_id]
            size = record['expected_size']
            if (len(entries) >= self.policy.global_documents or len(own) >= self.policy.user_documents
                    or sum(e['bytes'] for e in entries) + size > self.policy.global_bytes
                    or sum(e['bytes'] for e in own) + size > self.policy.user_bytes):
                raise QuotaExceeded('PDF storage quota is exhausted.')
            entries.append({'operation_id':identity.operation_id, 'document_id':identity.document_id,
                            'owner_id':identity.owner_id, 'knowledge_base_id':identity.knowledge_base_id,
                            'bytes':size, 'state':'reserved'})
            self._write_ledger(row, session)
            record['quota_state'] = 'reserved'
            record['updated_at'] = datetime.now(timezone.utc)
            self.intents.replace_one({'_id': identity.operation_id}, record, session=session)
            return 'reserved'

    def transition(self, identity, expected, target, worker_id):
        if target not in EDGES[identity.operation_type].get(expected, set()):
            raise IntentConflict('Invalid storage stage transition.')
        with self.transaction() as session:
            record = self._record(identity, session)
            if record['worker_id'] != worker_id or record['reconciliation_status'] != 'none':
                raise IntentConflict('Worker or reconciliation conflict.')
            if record['stage'] == target and record.get('previous_stage') == expected:
                return
            if record['stage'] != expected:
                raise IntentConflict('Storage stage conflict.')
            if target == 'write_dispatched' and record['quota_state'] != 'reserved':
                raise IntentConflict('Reservation required before dispatch.')
            # Publication/deletion confirmation is supplied only by future trusted
            # orchestration, never inferred here from a timeout or missing file.
            record.update(stage=target, previous_stage=expected, last_acknowledged_stage=target,
                          updated_at=datetime.now(timezone.utc))
            self.intents.replace_one({'_id':identity.operation_id, 'stage':expected}, record, session=session)

    def quota_transition(self, identity, action):
        if action not in ('commit', 'release'):
            raise ValueError('Invalid quota transition.')
        with self.transaction() as session:
            return self._quota_transition(identity, action, session)

    def _quota_transition(self, identity, action, session):
        record = self._record(identity, session)
        if record['reconciliation_status'] != 'none':
            raise IntentConflict('Uncertain quota must be retained.')
        if action == 'commit':
            allowed, target = {'published'}, 'committed'
        else:
            allowed = {'failed_before_dispatch'} if identity.operation_type == 'upload' else {'delete_confirmed'}
            target = 'released'
        if record['stage'] not in allowed:
            raise IntentConflict('Confirmed lifecycle evidence required.')
        source = self.intents.find_one({'_id':record['reservation_id']}, session=session)
        if source is None or any(source[k] != record[k] for k in
                ('document_id','owner_id','knowledge_base_id','file_id','backend','expected_size','checksum')):
            raise IntentConflict('Reservation identity conflict.')
        if source['reconciliation_status'] != 'none':
            raise IntentConflict('Uncertain reservation must be retained.')
        if source['quota_state'] == target:
            return target
        if source['quota_state'] not in ('reserved','committed'):
            raise IntentConflict('Invalid reservation state.')
        row = self._ledger(session)
        matches = [e for e in row['entries'] if e['operation_id'] == source['_id']]
        if len(matches) != 1:
            raise IntentConflict('Missing quota entry.')
        if action == 'release':
            row['entries'].remove(matches[0])
        else:
            matches[0]['state'] = target
        self._write_ledger(row, session)
        source['quota_state'] = target
        source['updated_at'] = datetime.now(timezone.utc)
        self.intents.replace_one({'_id':source['_id']}, source, session=session)
        return target

    def mark_uncertain(self, identity, expected, category='unknown'):
        if category not in ERRORS:
            raise ValueError('Invalid sanitized error category.')
        with self.transaction() as session:
            record = self._record(identity, session)
            if record['stage'] != expected:
                raise IntentConflict('Storage stage conflict.')
            record.update(reconciliation_status='required', error_category=category,
                          updated_at=datetime.now(timezone.utc))
            self.intents.replace_one({'_id':identity.operation_id, 'stage':expected}, record, session=session)

    def finalize_deletion(self, identity, worker_id):
        """Atomic metadata cleanup + quota release, only after file/vector ACKs.

        The deletion operation ID is the document's exclusive claim token.
        Never retry automatically after an uncertain transaction commit.
        """
        if identity.operation_type != 'delete':
            raise IntentConflict('Deletion intent required.')
        with self.transaction() as session:
            record = self._record(identity, session)
            if (record['stage'] != 'file_deleted' or record['worker_id'] != worker_id
                    or record['reconciliation_status'] != 'none'):
                raise IntentConflict('Confirmed deletion stages required.')
            scope = {'_id': identity.document_id, 'owner_id': identity.owner_id,
                     'knowledge_base_id': identity.knowledge_base_id,
                     '_operation': identity.operation_id, 'storage_backend': 'gridfs',
                     'storage_file_id': identity.document_id,
                     'storage_operation_id': record['reservation_id'],
                     'storage_checksum': record['checksum'], 'file_size': record['expected_size']}
            docs = self.db.get_collection('documents')
            if docs.find_one(scope, session=session) is None:
                raise IntentConflict('Deletion claim or original identity conflict.')
            self.db.get_collection('document_chunks').delete_many({
                'document_id': identity.document_id, 'owner_id': identity.owner_id,
                'knowledge_base_id': identity.knowledge_base_id}, session=session)
            result = self.db.get_collection('knowledge_bases').update_one({
                '_id': identity.knowledge_base_id, 'owner_id': identity.owner_id},
                {'$pull': {'_document_ids': identity.document_id}, '$inc': {'_document_revision': 1}},
                session=session)
            if result.matched_count != 1 or docs.delete_one(scope, session=session).deleted_count != 1:
                raise IntentConflict('Deletion metadata cleanup is unconfirmed.')
            record.update(stage='delete_confirmed', previous_stage='file_deleted',
                          last_acknowledged_stage='delete_confirmed', updated_at=datetime.now(timezone.utc))
            self.intents.replace_one({'_id': identity.operation_id, 'stage': 'file_deleted'}, record, session=session)
            self._quota_transition(identity, 'release', session)

    def heartbeat(self, identity, worker_id):
        with self.transaction() as session:
            record = self._record(identity, session)
            if record['worker_id'] != worker_id:
                raise IntentConflict('Worker identity conflict.')
            record['heartbeat_at'] = datetime.now(timezone.utc)
            self.intents.replace_one({'_id':identity.operation_id}, record, session=session)
