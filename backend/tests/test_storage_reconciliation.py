"""Read-only snapshot double; every write/API outside inspection is forbidden."""
import copy
import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from bson import ObjectId
from storage_intents import IntentIdentity
from storage_reconciliation import ReconciliationInspector
from document_storage import CHUNK_SIZE


class Cursor:
    def __init__(self, rows, db): self.rows, self.db, self.bound = rows, db, None
    def hint(self, name):
        self.db.hints.append(name)
        if self.db.missing_index: raise RuntimeError('private database detail')
        return self
    def sort(self, value): return self
    def limit(self, value): self.bound = value; return self
    def batch_size(self, value):
        assert value == 1
        return self
    def max_time_ms(self, value):
        assert value == 1000
        return self
    def __iter__(self):
        assert self.bound is not None
        return iter(self.rows[:self.bound])
    def close(self): self.db.closed += 1


class Collection:
    def __init__(self, db, name): self.db, self.name = db, name
    def read(self, query, projection, session):
        self.db.reads.append((self.name, query, projection))
        result = []
        for row in session.rows.get(self.name, []):
            if all(row.get(k) == v for k, v in query.items()):
                result.append({k: copy.deepcopy(v) for k, v in row.items() if k in projection})
        return result
    def find_one(self, query, projection, *, session, max_time_ms):
        assert max_time_ms == 1000
        rows = self.read(query, projection, session)
        return rows[0] if rows else None
    def find(self, query, projection, *, session):
        return Cursor(self.read(query, projection, session), self.db)
    def __getattr__(self, name):
        self.db.forbidden.append(name)
        raise AssertionError('Mutation/API forbidden')


class Database:
    def __init__(self):
        self.rows, self.reads, self.hints, self.forbidden = {}, [], [], []
        self.closed, self.missing_index = 0, False
        self.client = SimpleNamespace(start_session=self.session)
    def get_collection(self, name): return Collection(self, name)
    def session(self, *, snapshot):
        assert snapshot is True
        rows = copy.deepcopy(self.rows)
        class Session:
            def __enter__(self): self.rows = rows; return self
            def __exit__(self, *args): pass
        return Session()


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.now = datetime.now(timezone.utc)
        self.i = IntentIdentity.new(ObjectId(), ObjectId(), ObjectId())
        self.data = b'%PDF-synthetic-private-content'
        self.row = {**self.i.fields(), 'schema_version': 1, 'stage': 'published',
            'last_acknowledged_stage': 'published', 'checksum': hashlib.sha256(self.data).hexdigest(),
            'expected_size': len(self.data), 'reservation_id': self.i.operation_id,
            'quota_state': 'committed', 'worker_id': 'worker_1', 'heartbeat_at': self.now,
            'reconciliation_status': 'none'}
        self.doc = {'_id': self.i.document_id, 'owner_id': self.i.owner_id,
            'knowledge_base_id': self.i.knowledge_base_id, 'storage_backend': 'gridfs',
            'storage_file_id': self.i.document_id, 'storage_operation_id': self.i.operation_id,
            'storage_checksum': self.row['checksum'], 'file_size': len(self.data),
            'original_filename': 'private-document-name'}
        self.entry = {'operation_id': self.i.operation_id, 'document_id': self.i.document_id,
            'owner_id': self.i.owner_id, 'knowledge_base_id': self.i.knowledge_base_id,
            'bytes': len(self.data), 'state': 'committed'}
        self.file = {'_id': self.i.document_id, 'metadata': {'document_id': self.i.document_id,
            'owner_id': self.i.owner_id, 'knowledge_base_id': self.i.knowledge_base_id},
            'length': len(self.data), 'chunkSize': CHUNK_SIZE}
        self.db.rows = {'storage_operation_intents': [self.row], 'documents': [self.doc],
            'storage_quota_ledger': [{'_id': 'pdf_payload_v1', 'entries': [self.entry]}],
            'pdf_blobs.files': [self.file], 'pdf_blobs.chunks': [
                {'files_id': self.i.document_id, 'n': 0, 'data': self.data}],
            'knowledge_bases': [{'_id': self.i.knowledge_base_id, 'owner_id': self.i.owner_id,
                                '_document_ids': [self.i.document_id]}]}
        self.inspector = ReconciliationInspector(self.db)

    def inspect(self, identity=None):
        before = copy.deepcopy(self.db.rows)
        result = self.inspector.inspect(identity or self.i, now=self.now)
        self.assertEqual(before, self.db.rows)
        self.assertFalse(result.mutation_authorized)
        self.assertEqual(result.resume_scope, 'read_only_inspection')
        self.assertEqual(self.db.forbidden, [])
        self.assertNotIn('private', repr(result))
        self.assertNotIn(self.row['checksum'], repr(result))
        return result

    def stage(self, value):
        self.row.update(stage=value, last_acknowledged_stage=value)

    def reserved(self):
        self.row['quota_state'] = self.entry['state'] = 'reserved'

    def deletion(self, stage='vectors_deleted'):
        identity = IntentIdentity.new(self.i.document_id, self.i.owner_id, self.i.knowledge_base_id, 'delete')
        row = {**self.row, **identity.fields(), 'stage': stage, 'last_acknowledged_stage': stage,
               'quota_state': 'none', 'worker_id': 'delete_worker'}
        self.db.rows['storage_operation_intents'].append(row)
        self.doc['_operation'] = identity.operation_id
        return identity, row

    def test_published_upload_checksum_verified(self):
        result = self.inspect()
        self.assertEqual(result.classification, 'confirmed_consistent')
        self.assertEqual(result.file_state, 'checksum_verified_at_snapshot')
        self.assertGreater(self.db.closed, 0)

    def test_crash_before_dispatch_inspection_only(self):
        self.stage('prepared'); self.reserved()
        self.db.rows['documents'] = self.db.rows['pdf_blobs.files'] = self.db.rows['pdf_blobs.chunks'] = []
        result = self.inspect()
        self.assertEqual(result.classification, 'incomplete_safely_resumable')
        self.assertIn('worker_termination_unproven', result.reasons)

    def test_crash_during_upload_orphan_chunks(self):
        self.stage('write_dispatched'); self.reserved()
        self.db.rows['documents'] = self.db.rows['pdf_blobs.files'] = []
        result = self.inspect()
        self.assertEqual(result.classification, 'ambiguous_manual_review')
        self.assertEqual(result.file_state, 'orphan_chunks_observed')
        self.assertTrue(all('data' not in projection for name, _, projection in self.db.reads if name == 'pdf_blobs.chunks'))

    def test_file_committed_metadata_unpublished(self):
        self.stage('file_confirmed'); self.reserved(); self.db.rows['documents'] = []
        self.assertIn('metadata_publication_uncertain', self.inspect().reasons)

    def test_lost_metadata_ack_retains_quota(self):
        self.stage('file_confirmed'); self.reserved()
        self.assertIn('metadata_publication_uncertain', self.inspect().reasons)
        self.assertEqual(self.entry['state'], 'reserved')

    def test_crash_during_delete_missing_metadata_not_completion(self):
        identity, row = self.deletion()
        self.db.rows['pdf_blobs.files'] = []
        self.assertEqual(self.inspect(identity).file_state, 'orphan_chunks_observed')
        self.db.rows['pdf_blobs.chunks'] = []
        self.assertIn('incomplete_deletion', self.inspect(identity).reasons)

    def test_lost_quota_release_ack_consistent_committed_snapshot(self):
        identity, row = self.deletion('delete_confirmed')
        self.row['quota_state'] = 'released'
        self.db.rows['storage_quota_ledger'][0]['entries'] = []
        for name in ('documents', 'pdf_blobs.files', 'pdf_blobs.chunks'): self.db.rows[name] = []
        self.db.rows['knowledge_bases'][0]['_document_ids'] = []
        self.assertEqual(self.inspect(identity).classification, 'confirmed_consistent')
        self.db.rows['document_chunks'] = [{'owner_id': self.i.owner_id, 'document_id': self.i.document_id, '_id': ObjectId()}]
        self.assertIn('deletion_completion_inconsistent', self.inspect(identity).reasons)

    def test_stale_heartbeat_never_proves_worker_stopped(self):
        self.stage('write_dispatched'); self.reserved()
        self.row['heartbeat_at'] -= timedelta(days=1)
        result = self.inspect()
        self.assertEqual(result.heartbeat, 'stale')
        self.assertEqual(result.classification, 'ambiguous_manual_review')

    def test_overlapping_workers(self):
        self.stage('write_dispatched'); self.reserved()
        identity, row = self.deletion()
        self.assertIn('overlapping_operations', self.inspect(identity).reasons)

    def test_identity_conflicts(self):
        cases = [(self.row, 'owner_id'), (self.doc, 'knowledge_base_id'),
                 (self.entry, 'operation_id'), (self.file['metadata'], 'owner_id')]
        for target, field in cases:
            original = target[field]; target[field] = ObjectId()
            with self.subTest(field=field): self.assertEqual(self.inspect().classification, 'identity_conflict')
            target[field] = original

    def test_wrong_deletion_claim(self):
        identity, _ = self.deletion()
        self.doc['_operation'] = ObjectId()
        self.assertIn('deletion_claim_token_conflict', self.inspect(identity).reasons)

    def test_corrupt_bytes_and_sequence(self):
        chunk = self.db.rows['pdf_blobs.chunks'][0]
        chunk['data'] = b'x' * len(self.data)
        self.assertIn('file_integrity_unconfirmed', self.inspect().reasons)
        chunk['n'] = 2
        self.assertIn('invalid_gridfs_chunk_sequence', self.inspect().reasons)

    def test_bounds_and_sanitized_errors(self):
        self.db.missing_index = True
        self.assertEqual(self.inspect().reasons, ('inspection_unavailable_or_bound_exceeded',))
        self.db.missing_index = False
        self.db.rows['storage_operation_intents'] += [dict(self.row, _id=ObjectId()) for _ in range(21)]
        self.assertIn('related_operation_bound_exceeded', self.inspect().reasons)

    def test_malformed_intent_fails_before_storage_reads(self):
        self.row['checksum'] = 'private malicious input'
        self.assertEqual(self.inspect().classification, 'identity_conflict')
        self.assertFalse(any(name.startswith('pdf_blobs') for name, _, _ in self.db.reads))

    def test_missing_intent_and_untrusted_request(self):
        self.db.rows['storage_operation_intents'] = []
        self.assertEqual(self.inspect().reasons, ('intent_missing',))
        with self.assertRaises(ValueError): self.inspector.inspect({'owner_id': 'untrusted'})

    def test_terminal_history_conflict_is_not_consistent_upload(self):
        self.deletion('delete_confirmed')
        del self.doc['_operation']
        self.assertIn('completed_deletion_evidence_conflict', self.inspect().reasons)

    def test_large_chunk_and_future_heartbeat_fail_closed(self):
        self.db.rows['pdf_blobs.chunks'][0]['data'] = b'x' * (CHUNK_SIZE + 1)
        self.assertEqual(self.inspect().classification, 'ambiguous_manual_review')
        self.db.rows['pdf_blobs.chunks'][0]['data'] = self.data
        self.row['heartbeat_at'] = self.now + timedelta(days=1)
        result = self.inspect()
        self.assertEqual(result.heartbeat, 'future')
        self.assertEqual(result.classification, 'ambiguous_manual_review')
