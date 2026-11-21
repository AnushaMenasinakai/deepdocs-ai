"""Combined orchestration with existing transaction/GridFS doubles; no services."""
import asyncio
import copy
import io
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bson import ObjectId
from config import GrowthSettings, ExpensiveAdmissionSettings
from gridfs_uploads import GridFSUploads
from gridfs_deletion import GridFSDeletion
from gridfs_storage import GridFSPDFStorage
from storage_workers import StorageWorkers
from storage_intents import IntentIdentity, IntentConflict, QuotaPolicy
from storage_contract import StorageUncertain
from document_storage import UploadRejected
from admission_lifecycle import AdmissionService
from expensive_admission import Resource
from test_gridfs_deletion import DeletionDatabase, DeletionCollection
from test_gridfs_storage import Bucket, PDF
import gridfs_growth_recovery as recovery


class Cursor(list):
    def limit(self, n): return Cursor(self[:n])
    def hint(self, name): return self
    def sort(self, fields): return Cursor(sorted(self, key=lambda r: tuple(str(r.get(k, '')) for k, _ in fields)))
    def batch_size(self, n): return self
    def max_time_ms(self, n): return self
    def close(self): pass


class Collection(DeletionCollection):
    def find_one(self, query, projection=None, **kwargs):
        row = super().find_one(query, **kwargs)
        return {k: v for k, v in row.items() if k in projection} if row is not None and projection else row

    def find(self, query, *args, **kwargs):
        def matches(row):
            return all(bool(row.get('_document_ids')) if key == '_document_ids.0' else row.get(key) == value
                       for key, value in query.items())
        return Cursor(copy.deepcopy([r for r in self.db.rows.setdefault(self.name, {}).values() if matches(r)]))

    def update_one(self, query, update, **kwargs):
        with self.db.lock:
            row = self.find_one(query)
            if row is None and kwargs.get('upsert'):
                row = {**query, **copy.deepcopy(update.get('$setOnInsert', {}))}
                self.db.rows.setdefault(self.name, {})[row['_id']] = row
            if row is None: return SimpleNamespace(matched_count=0)
            target = self.db.rows[self.name][row['_id']]
            for k, v in update.get('$inc', {}).items(): target[k] = target.get(k, 0) + v
            for k, v in update.get('$addToSet', {}).items():
                if v not in target.setdefault(k, []): target[k].append(v)
            for k, v in update.get('$pull', {}).items(): target[k] = [i for i in target.get(k, []) if i != v]
            target.update(update.get('$set', {}))
            return SimpleNamespace(matched_count=1)

    def find_one_and_update(self, query, update, **kwargs):
        with self.db.lock:
            before = self.find_one(query)
            self.update_one(query, update, **kwargs)
            return self.find_one(query) if kwargs.get('return_document') else before


class Database(DeletionDatabase):
    def __init__(self):
        super().__init__(); self.lock = threading.RLock()
    def get_collection(self, name): return Collection(self, name)


class CombinedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(); self.owner, self.base = ObjectId(), ObjectId()
        self.db.rows['knowledge_bases'] = {self.base: {'_id': self.base, 'owner_id': self.owner}}
        self.bucket = Bucket(); self.workers = StorageWorkers(Mock())
        self.factory = lambda db, **kw: GridFSPDFStorage(db, bucket=self.bucket, **kw)
        self.service = GridFSUploads(self.workers, self.db, storage_factory=self.factory)
        self.settings = GrowthSettings(True, documents=2)
        p = patch('gridfs_uploads.load_growth_settings', side_effect=lambda: self.settings)
        p.start(); self.addCleanup(p.stop)
        self.uploads = []

    async def asyncTearDown(self):
        await self.workers.aclose()
        for upload in self.uploads:
            lifetime = getattr(upload, '_deepdocs_save_lifetime', None)
            if lifetime: lifetime.close_when_finished()
            else: upload.file.close()

    def upload(self):
        value = SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf', file=io.BytesIO(PDF))
        self.uploads.append(value); return value

    async def call(self): return await self.service.upload(self.owner, self.base, self.upload(), 100)
    def held(self): return self.db.rows.get('resource_usage', {}).get(self.owner, {}).get('documents', [])
    def entries(self): return self.db.rows.get('storage_quota_ledger', {}).get('pdf_payload_v1', {}).get('entries', [])

    async def test_success_counts_once_and_deletion_releases_both(self):
        result = await self.call(); identifier = ObjectId(result.id)
        self.assertEqual(self.held(), [identifier]); self.assertEqual(len(self.entries()), 1)
        doc = copy.deepcopy(self.db.rows['documents'][identifier]); token = ObjectId()
        self.db.rows['documents'][identifier]['_operation'] = token
        service = GridFSDeletion(self.workers, self.db, storage_factory=self.factory)
        admission = AdmissionService(ExpensiveAdmissionSettings())
        try:
            with admission.workflow(Resource.DOCUMENT) as workflow:
                self.assertTrue(await service.delete(doc, self.owner, identifier, token, workflow))
        finally: await admission.aclose()
        self.assertEqual(self.held(), []); self.assertEqual(self.entries(), [])
        self.assertEqual(self.bucket.files, {})

    async def test_growth_exhaustion_preserves_gridfs_quota(self):
        self.settings = GrowthSettings(True, documents=1)
        await self.call()
        with self.assertRaises(UploadRejected) as error: await self.call()
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(len(self.held()), 1); self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.bucket.files), 1)

    async def test_gridfs_exhaustion_preserves_growth_quota(self):
        self.service.intents.policy = QuotaPolicy(global_documents=1, user_documents=1)
        await self.call()
        with self.assertRaises(UploadRejected) as error: await self.call()
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(len(self.held()), 1); self.assertEqual(len(self.entries()), 1)

    async def test_separate_worker_admission_cannot_exceed_limit(self):
        self.settings = GrowthSettings(True, documents=1)
        other = StorageWorkers(Mock())
        service = GridFSUploads(other, self.db, storage_factory=self.factory)
        try:
            results = await asyncio.gather(self.call(), service.upload(self.owner, self.base, self.upload(), 100), return_exceptions=True)
        finally: await other.aclose()
        self.assertLessEqual(len(self.bucket.files), 1)
        self.assertEqual(len(self.held()), len(self.entries()))
        self.assertTrue(any(isinstance(r, UploadRejected) for r in results))

    async def test_interruption_and_metadata_ack_loss_retain_both(self):
        for failure in ('close', 'metadata'):
            if failure == 'close': self.bucket.failure = 'close'
            else: self.bucket.failure = None; self.db.publication_failure = True
            with self.assertRaises(StorageUncertain): await self.call()
        self.assertEqual(len(self.held()), 2); self.assertEqual(len(self.entries()), 2)
        self.assertEqual(self.bucket.deletes, 0)
        for row in self.db.rows['storage_operation_intents'].values():
            identity = IntentIdentity(row['_id'], row['document_id'], self.owner, self.base)
            with self.assertRaises(IntentConflict): recovery.review(self.db, identity)

    async def prepared_cancel(self):
        identity = IntentIdentity.new(ObjectId(), self.owner, self.base); cancelled = threading.Event()
        original = self.service.intents.reserve
        def reserve(*a, **kw):
            result = original(*a, **kw); cancelled.set(); return result
        with patch.object(self.service.intents, 'reserve', side_effect=reserve):
            with self.assertRaises(StorageUncertain):
                await asyncio.to_thread(self.service._execute, identity, self.upload(), 100, cancelled)
        return identity

    async def test_cancel_before_storage_reviewed_recovery_fences_worker(self):
        identity = await self.prepared_cancel()
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(len(self.held()), 1); self.assertEqual(len(self.entries()), 1)
        plan = recovery.review(self.db, identity)
        self.assertEqual(recovery.apply(self.db, identity, review_token=plan['review_token'], operator='test_operator'), 'released')
        self.assertEqual(self.held(), []); self.assertEqual(self.entries(), [])
        with self.assertRaises(IntentConflict):
            self.service.intents.transition(identity, 'prepared', 'write_dispatched', self.service.worker_id)
        plan = recovery.review(self.db, identity)
        self.assertEqual(recovery.apply(self.db, identity, review_token=plan['review_token'], operator='test_operator'), 'already_released')

    async def test_recovery_rejects_changed_state_or_orphan_chunks(self):
        identity = await self.prepared_cancel(); plan = recovery.review(self.db, identity)
        self.db.rows['resource_usage'][self.owner]['revision'] += 1
        with self.assertRaises(IntentConflict): recovery.apply(self.db, identity, review_token=plan['review_token'], operator='test')
        self.db.rows['pdf_blobs.chunks'] = {ObjectId(): {'files_id': identity.document_id}}
        with self.assertRaises(IntentConflict): recovery.review(self.db, identity)
        self.assertEqual(len(self.held()), 1)

    async def test_recovery_commit_ack_loss_is_idempotently_inspected(self):
        identity = await self.prepared_cancel(); plan = recovery.review(self.db, identity)
        self.db.failure = 'after_commit'
        with self.assertRaises(StorageUncertain): recovery.apply(self.db, identity, review_token=plan['review_token'], operator='test')
        self.assertEqual(recovery.review(self.db, identity)['action'], 'already_released')
        self.assertEqual(self.held(), []); self.assertEqual(self.entries(), [])

    async def test_combined_reservation_ack_loss_retains_both_without_payload(self):
        original = self.service.intents.reserve
        def lost(*args, **kwargs):
            self.db.failure = 'after_commit'
            return original(*args, **kwargs)
        with patch.object(self.service.intents, 'reserve', side_effect=lost):
            with self.assertRaises(StorageUncertain): await self.call()
        self.assertEqual(len(self.held()), 1); self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.bucket.files, {})

    async def test_deletion_accounting_failure_rolls_back_both_ledgers(self):
        result = await self.call(); identifier = ObjectId(result.id)
        doc = copy.deepcopy(self.db.rows['documents'][identifier]); token = ObjectId()
        self.db.rows['documents'][identifier]['_operation'] = token
        service = GridFSDeletion(self.workers, self.db, storage_factory=self.factory)
        admission = AdmissionService(ExpensiveAdmissionSettings())
        try:
            with patch('gridfs_growth.release', side_effect=OSError('private failure')):
                with admission.workflow(Resource.DOCUMENT) as workflow:
                    with self.assertRaises(StorageUncertain):
                        await service.delete(doc, self.owner, identifier, token, workflow)
        finally: await admission.aclose()
        self.assertEqual(self.held(), [identifier]); self.assertEqual(len(self.entries()), 1)
        self.assertIn(identifier, self.db.rows['documents'])
        self.assertEqual(self.bucket.files, {})
        self.assertEqual(self.db.rows['storage_operation_intents'][token]['stage'], 'file_deleted')

class RecoveryFinalizationTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        from storage_intents import StorageIntents
        self.db = Database()
        self.i = IntentIdentity.new(ObjectId(), ObjectId(), ObjectId())
        self.store = StorageIntents(self.db)
        self.db.rows['knowledge_bases'] = {self.i.knowledge_base_id: {'_id': self.i.knowledge_base_id, 'owner_id': self.i.owner_id}}
        key = str(self.i.document_id)+'.pdf'
        self.descriptor = dict(original_filename='synthetic.pdf', stored_filename=key, storage_path=key, file_size=len(PDF), content_type='application/pdf')
        self.store.prepare_upload(self.i, self.descriptor, hashlib.sha256(PDF).hexdigest(), 'worker')
        self.store.reserve(self.i)
        self.store.transition(self.i, 'prepared', 'write_dispatched', 'worker')
        self.db.rows['pdf_blobs.files'] = {self.i.document_id: {'_id': self.i.document_id,
            'metadata': dict(document_id=self.i.document_id, owner_id=self.i.owner_id, knowledge_base_id=self.i.knowledge_base_id),
            'length': len(PDF), 'chunkSize': 65536}}
        chunk = ObjectId()
        self.db.rows['pdf_blobs.chunks'] = {chunk: {'_id': chunk, 'files_id': self.i.document_id, 'n': 0, 'data': PDF}}
        self.maintenance = dict(operator='offline_test', evidence_id='maintenance_1', writers_stopped=True, inflight_resolved=True)

    def review(self): return recovery.review_publication(self.db, self.i, maintenance=self.maintenance)
    def apply(self, plan): return recovery.apply_publication(self.db, self.i, review_token=plan['review_token'], maintenance=self.maintenance)

    def test_unpublished_and_repeated_finalization(self):
        self.assertEqual(self.apply(self.review()), 'finalized')
        self.assertEqual(self.apply(self.review()), 'already_finalized')
        self.assertEqual(len(self.db.rows['documents']), 1)
        self.assertEqual(self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries'][0]['state'], 'committed')

    def test_matching_publication_after_lost_ack(self):
        self.db.failure = 'after_commit'
        # Set failure after review so it applies to the mutation commit.
        self.db.failure = None
        plan = self.review(); self.db.failure = 'after_commit'
        with self.assertRaises(StorageUncertain): self.apply(plan)
        self.assertEqual(self.review()['action'], 'already_finalized')

    def test_rollback_preserves_capacity_and_payload(self):
        plan = self.review(); before = copy.deepcopy(self.db.rows); self.db.failure = 'before_commit'
        with self.assertRaises(StorageUncertain): self.apply(plan)
        self.assertEqual(self.db.rows, before)

    def test_quarantine_corruption_identity_overlap_and_legacy(self):
        before = copy.deepcopy(self.db.rows)
        changes = [lambda: self.db.rows['pdf_blobs.chunks'].clear(),
            lambda: self.db.rows['pdf_blobs.files'][self.i.document_id]['metadata'].update(owner_id=ObjectId()),
            lambda: self.db.rows['storage_operation_intents'][self.i.operation_id].pop('publication_descriptor'),
            lambda: self.db.rows.setdefault('documents', {}).update({self.i.document_id: {'_id': self.i.document_id}}),
            lambda: self.db.rows['storage_operation_intents'].update({ObjectId(): {**self.db.rows['storage_operation_intents'][self.i.operation_id], '_id': ObjectId()}})]
        for change in changes:
            self.db.rows = copy.deepcopy(before)
            with self.subTest(change=changes.index(change)):
                change()
                with self.assertRaises(IntentConflict): self.review()

    def test_review_token_and_maintenance_fail_closed(self):
        plan = self.review()
        self.db.rows['knowledge_bases'][self.i.knowledge_base_id]['_document_revision'] += 1
        with self.assertRaises(IntentConflict): self.apply(plan)
        self.maintenance['inflight_resolved'] = False
        with self.assertRaises(IntentConflict): self.review()

    def deletion(self):
        self.apply(self.review())
        d = IntentIdentity.new(self.i.document_id, self.i.owner_id, self.i.knowledge_base_id, 'delete')
        row = self.db.rows['storage_operation_intents'][self.i.operation_id]
        self.store.create(d, len(PDF), row['checksum'], 'delete_worker', self.i.operation_id)
        self.db.rows['documents'][self.i.document_id]['_operation'] = d.operation_id
        self.store.transition(d, 'prepared', 'delete_dispatched', 'delete_worker')
        self.store.transition(d, 'delete_dispatched', 'vectors_deleted', 'delete_worker')
        self.db.rows['pdf_blobs.files'].clear(); self.db.rows['pdf_blobs.chunks'].clear()
        self.store.mark_uncertain(d, 'vectors_deleted')
        return d

    def test_deletion_finalizes_once_with_uncertain_ack(self):
        d = self.deletion()
        plan = recovery.review_deletion(self.db, d, maintenance=self.maintenance)
        self.db.failure = 'after_commit'
        with self.assertRaises(StorageUncertain):
            recovery.apply_deletion(self.db, d, review_token=plan['review_token'], maintenance=self.maintenance)
        plan = recovery.review_deletion(self.db, d, maintenance=self.maintenance)
        self.assertEqual(plan['action'], 'already_finalized')
        self.assertEqual(self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries'], [])
        self.assertEqual(self.db.rows['documents'], {})

    def test_deletion_orphans_claim_and_vector_evidence(self):
        d = self.deletion(); before = copy.deepcopy(self.db.rows)
        for kind in ('orphan', 'claim', 'vectors'):
            self.db.rows = copy.deepcopy(before)
            with self.subTest(kind=kind):
                if kind == 'orphan': self.db.rows['pdf_blobs.chunks'][ObjectId()] = {'files_id': d.document_id, 'n': 0}
                elif kind == 'claim': self.db.rows['documents'][d.document_id]['_operation'] = ObjectId()
                else: self.db.rows['storage_operation_intents'][d.operation_id].update(stage='delete_dispatched', last_acknowledged_stage='delete_dispatched')
                with self.assertRaises(IntentConflict): recovery.review_deletion(self.db, d, maintenance=self.maintenance)

    def test_growth_enabled_publication_and_deletion_release(self):
        row = self.db.rows['storage_operation_intents'][self.i.operation_id]
        row['growth_reserved'] = True
        self.db.rows['resource_usage'] = {self.i.owner_id: {'_id': self.i.owner_id, 'documents': [self.i.document_id], 'revision': 1}}
        d = self.deletion()
        plan = recovery.review_deletion(self.db, d, maintenance=self.maintenance)
        self.assertEqual(recovery.apply_deletion(self.db,d,review_token=plan['review_token'],maintenance=self.maintenance),'finalized')
        self.assertEqual(self.db.rows['resource_usage'][self.i.owner_id]['documents'], [])

    def test_existing_matching_unfinalized_metadata(self):
        self.apply(self.review())
        row = self.db.rows['storage_operation_intents'][self.i.operation_id]
        row.update(stage='file_confirmed',last_acknowledged_stage='file_confirmed',quota_state='reserved',reconciliation_status='required')
        self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries'][0]['state']='reserved'
        doc = copy.deepcopy(self.db.rows['documents'][self.i.document_id])
        self.assertEqual(self.apply(self.review()),'finalized')
        self.assertEqual(self.db.rows['documents'][self.i.document_id],doc)

    def test_payload_change_between_review_and_apply(self):
        plan = self.review()
        next(iter(self.db.rows['pdf_blobs.chunks'].values()))['data'] = b'x'*len(PDF)
        with self.assertRaises(IntentConflict): self.apply(plan)

    def test_deletion_transaction_rollback(self):
        d = self.deletion(); plan = recovery.review_deletion(self.db,d,maintenance=self.maintenance)
        before=copy.deepcopy(self.db.rows); self.db.failure='before_commit'
        with self.assertRaises(StorageUncertain): recovery.apply_deletion(self.db,d,review_token=plan['review_token'],maintenance=self.maintenance)
        self.assertEqual(before,self.db.rows)

    def test_missing_maintenance_and_wrong_growth_state(self):
        self.maintenance['writers_stopped']=False
        with self.assertRaises(IntentConflict): self.review()
        self.maintenance['writers_stopped']=True
        self.db.rows['storage_operation_intents'][self.i.operation_id]['growth_reserved']=True
        with self.assertRaises(IntentConflict): self.review()

    def test_changed_file_metadata_requires_new_review(self):
        plan=self.review()
        self.db.rows['pdf_blobs.files'][self.i.document_id]['filename']='changed.pdf'
        with self.assertRaises(IntentConflict): self.apply(plan)

    def test_foreign_chunks_block_deletion_finalization(self):
        d=self.deletion()
        chunk=ObjectId()
        self.db.rows['document_chunks']={chunk:dict(_id=chunk,document_id=d.document_id,owner_id=ObjectId(),knowledge_base_id=d.knowledge_base_id)}
        with self.assertRaises(IntentConflict): recovery.review_deletion(self.db,d,maintenance=self.maintenance)
