"""Disconnected, bounded read-only snapshot inspection. Never repairs state.

Call inspect through an operator-owned StorageWorkers.run callable. No client,
route, startup scan, retry, filesystem write or collection setup lives here.
"""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from bson import ObjectId
from pymongo import timeout
from storage_intents import IntentIdentity, EDGES
from document_storage import CHUNK_SIZE

MAX_BYTES = 5 * 1024**2
MAX_RELATED = 20
INTENT_FIELDS = ('_id', 'document_id', 'owner_id', 'knowledge_base_id', 'backend',
    'file_id', 'operation_type', 'schema_version', 'stage', 'last_acknowledged_stage',
    'checksum', 'expected_size', 'reservation_id', 'quota_state', 'worker_id',
    'heartbeat_at', 'reconciliation_status')
DOC_FIELDS = ('_id', 'owner_id', 'knowledge_base_id', 'storage_backend', 'storage_file_id',
              'storage_operation_id', 'storage_checksum', 'file_size', '_operation')


@dataclass(frozen=True)
class Inspection:
    operation_id: str
    classification: str
    reasons: tuple[str, ...]
    stage: str = 'unknown'
    quota: str = 'unknown'
    heartbeat: str = 'unknown'
    file_state: str = 'not_inspected'
    resume_scope: str = 'read_only_inspection'
    mutation_authorized: bool = False


class ReconciliationInspector:
    def __init__(self, database):
        self.db = database

    def inspect(self, identity, *, now=None):
        if not isinstance(identity, IntentIdentity):
            raise ValueError('Explicit server-owned inspection identity required.')
        now = now or datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError('Timezone-aware inspection time required.')
        try:
            # Snapshot reads are mandatory; never downgrade to mixed-time reads.
            with timeout(5), self.db.client.start_session(snapshot=True) as session:
                return self._inspect(identity, now, session)
        except Exception:
            return Inspection(str(identity.operation_id), 'ambiguous_manual_review',
                              ('inspection_unavailable_or_bound_exceeded',))

    def _one(self, name, query, fields, session):
        return self.db.get_collection(name).find_one(query, {k: 1 for k in fields},
                                                    session=session, max_time_ms=1000)

    def _many(self, name, query, fields, index, limit, session, sort):
        cursor = self.db.get_collection(name).find(query, {k: 1 for k in fields}, session=session)
        try:
            cursor = cursor.hint(index).sort(sort).limit(limit).batch_size(1).max_time_ms(1000)
            rows = []
            for row in cursor:
                if 'data' in fields and (not isinstance(row.get('data'), bytes) or len(row['data']) > CHUNK_SIZE):
                    raise ValueError('Chunk size bound exceeded.')
                rows.append(row)
            return rows
        finally:
            cursor.close()

    def _inspect(self, identity, now, session):
        identifier = str(identity.operation_id)
        row = self._one('storage_operation_intents', {'_id': identity.operation_id}, INTENT_FIELDS, session)
        if row is None:
            return Inspection(identifier, 'ambiguous_manual_review', ('intent_missing',))
        stage = row.get('stage')
        valid = (all(row.get(k) == v for k, v in identity.fields().items())
            and type(row.get('schema_version')) is int and row['schema_version'] == 1
            and isinstance(stage, str) and stage in EDGES[identity.operation_type]
            and row.get('last_acknowledged_stage') == stage
            and type(row.get('expected_size')) is int and 0 < row['expected_size'] <= MAX_BYTES
            and isinstance(row.get('checksum'), str) and re.fullmatch('[0-9a-f]{64}', row['checksum'])
            and isinstance(row.get('reservation_id'), ObjectId)
            and row.get('quota_state') in ('none', 'reserved', 'committed', 'released')
            and row.get('reconciliation_status') in ('none', 'required')
            and isinstance(row.get('worker_id'), str) and re.fullmatch('[A-Za-z0-9_-]{1,64}', row['worker_id']))
        if not valid:
            return Inspection(identifier, 'identity_conflict', ('invalid_intent_identity_or_schema',))
        heartbeat = row.get('heartbeat_at')
        if isinstance(heartbeat, datetime):
            heartbeat = heartbeat.replace(tzinfo=timezone.utc) if heartbeat.tzinfo is None else heartbeat
            age = (now - heartbeat).total_seconds()
            heartbeat = 'future' if age < 0 else ('stale' if age > 300 else 'recent')
        else:
            heartbeat = 'unknown'
        file_state = 'not_inspected'
        quota = row['quota_state']

        def report(category, *reasons):
            return Inspection(identifier, category, tuple(reasons), stage, quota, heartbeat, file_state)

        source = row if identity.operation_type == 'upload' else self._one(
            'storage_operation_intents', {'_id': row['reservation_id']}, INTENT_FIELDS, session)
        same = ('document_id', 'owner_id', 'knowledge_base_id', 'backend', 'file_id', 'checksum', 'expected_size')
        if (source is None or any(source.get(k) != row[k] for k in same)
                or source.get('operation_type') != 'upload'
                or source.get('_id') != row['reservation_id']
                or source.get('reservation_id') != row['reservation_id']
                or source.get('schema_version') != 1
                or source.get('stage') not in EDGES['upload']
                or source.get('last_acknowledged_stage') != source.get('stage')
                or source.get('quota_state') not in ('none', 'reserved', 'committed', 'released')):
            return report('identity_conflict', 'reservation_identity_conflict')
        quota = source['quota_state']
        doc = self._one('documents', {'_id': identity.document_id}, DOC_FIELDS, session)
        expected_doc = {'owner_id': identity.owner_id, 'knowledge_base_id': identity.knowledge_base_id,
            'storage_backend': 'gridfs', 'storage_file_id': identity.document_id,
            'storage_operation_id': row['reservation_id'], 'storage_checksum': row['checksum'],
            'file_size': row['expected_size']}
        if doc is not None and any(doc.get(k) != v for k, v in expected_doc.items()):
            return report('identity_conflict', 'document_identity_conflict')
        ledger = self._one('storage_quota_ledger', {'_id': 'pdf_payload_v1'}, ('entries',), session)
        entries = [] if ledger is None else ledger.get('entries')
        if not isinstance(entries, list) or len(entries) > 20 or any(not isinstance(e, dict) for e in entries):
            return report('identity_conflict', 'invalid_quota_ledger')
        matches = [e for e in entries if isinstance(e, dict) and
                   (e.get('document_id') == identity.document_id or e.get('operation_id') == row['reservation_id'])]
        expected_entry = {'operation_id': row['reservation_id'], 'document_id': identity.document_id,
            'owner_id': identity.owner_id, 'knowledge_base_id': identity.knowledge_base_id,
            'bytes': row['expected_size'], 'state': quota}
        if ((quota in ('reserved', 'committed') and (len(matches) != 1 or
             any(matches[0].get(k) != v for k, v in expected_entry.items())))
                or (quota in ('none', 'released') and matches)):
            return report('identity_conflict', 'quota_identity_or_usage_conflict')
        related = self._many('storage_operation_intents',
            {'owner_id': identity.owner_id, 'document_id': identity.document_id}, INTENT_FIELDS,
            'storage_intent_owner_document', MAX_RELATED + 1, session, [('created_at', 1)])
        if len(related) > MAX_RELATED:
            return report('ambiguous_manual_review', 'related_operation_bound_exceeded')
        for other in related:
            if (any(other.get(k) != row[k] for k in same)
                    or other.get('schema_version') != 1
                    or other.get('operation_type') not in EDGES
                    or other.get('stage') not in EDGES.get(other.get('operation_type'), {})
                    or (other.get('operation_type') == 'upload' and other.get('_id') != row['reservation_id'])):
                return report('identity_conflict', 'related_operation_identity_conflict')
        active = [r for r in related if r.get('stage') not in ('published', 'delete_confirmed', 'failed_before_dispatch')]
        if len(active) > 1:
            return report('ambiguous_manual_review', 'overlapping_operations', 'worker_termination_unproven')
        file = self._one('pdf_blobs.files', {'_id': identity.document_id},
                         ('_id', 'metadata', 'length', 'chunkSize'), session)
        if file is not None and (file.get('metadata') != {'document_id': identity.document_id,
            'owner_id': identity.owner_id, 'knowledge_base_id': identity.knowledge_base_id}
                or file.get('length') != row['expected_size'] or file.get('chunkSize') != CHUNK_SIZE):
            return report('identity_conflict', 'gridfs_metadata_identity_or_length_conflict')
        # Do not fetch orphan contents when metadata/ownership cannot be verified.
        fields = ('n', 'data') if file is not None else ('n',)
        chunks = self._many('pdf_blobs.chunks', {'files_id': identity.document_id}, fields,
                           'files_id_1_n_1', MAX_BYTES // CHUNK_SIZE + 1, session, [('n', 1)])
        if file is None:
            file_state = 'orphan_chunks_observed' if chunks else 'absent_at_snapshot'
        else:
            digest, length = hashlib.sha256(), 0
            for n, chunk in enumerate(chunks):
                data = chunk.get('data')
                expected_length = min(CHUNK_SIZE, row['expected_size'] - n * CHUNK_SIZE)
                if chunk.get('n') != n or not isinstance(data, bytes) or not 0 < len(data) == expected_length:
                    return report('ambiguous_manual_review', 'invalid_gridfs_chunk_sequence')
                length += len(data)
                if length > MAX_BYTES:
                    return report('ambiguous_manual_review', 'file_byte_bound_exceeded')
                digest.update(data)
            if length != row['expected_size'] or digest.hexdigest() != row['checksum']:
                return report('ambiguous_manual_review', 'file_integrity_unconfirmed')
            file_state = 'checksum_verified_at_snapshot'
        base = self._one('knowledge_bases', {'_id': identity.knowledge_base_id},
                         ('owner_id', '_document_ids'), session)
        if base is not None and base.get('owner_id') != identity.owner_id:
            return report('identity_conflict', 'knowledge_base_identity_conflict')
        reserved = base is not None and identity.document_id in base.get('_document_ids', [])
        if identity.operation_type == 'delete' and doc is not None and doc.get('_operation') != identity.operation_id:
            return report('identity_conflict', 'deletion_claim_token_conflict')
        if file_state == 'orphan_chunks_observed':
            return report('ambiguous_manual_review', 'file_metadata_absent_chunks_present', 'retain_quota')
        if any(r.get('operation_type') == 'delete' and r.get('stage') == 'delete_confirmed' for r in related) \
                and (doc is not None or file is not None or quota != 'released' or reserved):
            return report('ambiguous_manual_review', 'completed_deletion_evidence_conflict')
        if stage == 'delete_confirmed' and identity.operation_type == 'delete':
            chunk = self._many('document_chunks', {'owner_id': identity.owner_id, 'document_id': identity.document_id},
                ('_id',), 'chunks_owner_document_generation_order', 1, session, [('generation', 1)])
            if doc is None and not chunk and not reserved and quota == 'released' and file_state == 'absent_at_snapshot':
                return report('confirmed_consistent', 'completed_deletion_at_snapshot')
            return report('ambiguous_manual_review', 'deletion_completion_inconsistent')
        if (row['reconciliation_status'] == 'required' or source.get('reconciliation_status') == 'required'
                or heartbeat in ('unknown', 'future')):
            return report('ambiguous_manual_review', 'reconciliation_or_worker_evidence_required', 'retain_quota')
        if identity.operation_type == 'upload' and stage == 'published' and quota == 'committed' and doc is not None \
                and '_operation' not in doc and reserved and file_state == 'checksum_verified_at_snapshot' and not active:
            return report('confirmed_consistent', 'published_upload_at_snapshot')
        if stage == 'prepared' and identity.operation_type == 'upload' and doc is None \
                and file_state == 'absent_at_snapshot' and quota in ('none', 'reserved') and reserved:
            return report('incomplete_safely_resumable', 'inspection_only', 'pre_dispatch_at_snapshot',
                          'worker_termination_unproven', 'retain_quota')
        reason = 'incomplete_deletion' if identity.operation_type == 'delete' else (
            'metadata_publication_uncertain' if file is not None else 'uncertain_file_write')
        return report('ambiguous_manual_review', reason, 'worker_termination_unproven', 'retain_quota')
