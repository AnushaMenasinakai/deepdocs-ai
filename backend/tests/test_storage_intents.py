"""Transactional database double: snapshot rollback, serialization, lost commit ACK."""
import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from storage_intents import StorageIntents, IntentIdentity, QuotaPolicy, IntentConflict, QuotaExceeded
from storage_contract import StorageUncertain


class Collection:
    def __init__(self, db, name): self.db, self.name = db, name
    def find_one(self, query, **kwargs):
        return next((copy.deepcopy(v) for v in self.db.rows.setdefault(self.name, {}).values()
                     if all(v.get(k) == x for k,x in query.items())), None)
    def insert_one(self, value, **kwargs):
        assert value['_id'] not in self.db.rows.setdefault(self.name,{})
        self.db.rows[self.name][value['_id']] = copy.deepcopy(value)
    def replace_one(self, query, value, **kwargs):
        old = self.find_one(query)
        assert old is not None or kwargs.get('upsert')
        self.db.rows.setdefault(self.name,{})[value['_id']] = copy.deepcopy(value)


class Session:
    def __init__(self, db): self.db = db
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def start_transaction(self, **kwargs):
        db = self.db
        class Transaction:
            def __enter__(self):
                db.lock.acquire()
                self.before = copy.deepcopy(db.rows)
            def __exit__(self, typ, value, tb):
                failure, db.failure = db.failure, None
                if typ or failure == 'before_commit': db.rows = self.before
                db.lock.release()
                if not typ and failure: raise ConnectionFailure('private-provider-data')
        return Transaction()


class Database:
    def __init__(self):
        self.rows, self.lock, self.failure = {}, threading.Lock(), None
        self.client = SimpleNamespace(start_session=lambda:Session(self))
    def get_collection(self, name): return Collection(self,name)


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.policy = QuotaPolicy(20,10,5,4,2)
        self.store = StorageIntents(self.db,self.policy)
        self.owner, self.base = ObjectId(), ObjectId()
    def create(self, owner=None, size=5):
        identity = IntentIdentity.new(ObjectId(), owner or self.owner, self.base)
        self.store.create(identity,size,'a'*64,'worker_1')
        return identity
    def row(self, identity):
        return self.db.get_collection('storage_operation_intents').find_one({'_id':identity.operation_id})
    def publish(self, identity):
        for a,b in [('prepared','write_dispatched'),('write_dispatched','file_confirmed'),('file_confirmed','published')]:
            self.store.transition(identity,a,b,'worker_1')

    def test_defaults_and_fields(self):
        self.assertEqual(QuotaPolicy().global_bytes,50*1024**2)
        row=self.row(self.create())
        for key in ('file_id','schema_version','stage','last_acknowledged_stage','checksum','reservation_id',
                    'worker_id','created_at','heartbeat_at','error_category','reconciliation_status'):
            self.assertIn(key,row)
    def test_duplicate_id_and_conflicting_identity(self):
        i=self.create()
        self.store.create(i,5,'a'*64,'worker_1')
        with self.assertRaises(IntentConflict): self.store.create(i,4,'a'*64,'worker_1')
        forged=IntentIdentity(i.operation_id,i.document_id,ObjectId(),self.base)
        with self.assertRaises(IntentConflict): self.store.reserve(forged)
    def test_per_user_quota(self):
        for _ in range(2): self.store.reserve(self.create())
        with self.assertRaises(QuotaExceeded): self.store.reserve(self.create())
    def test_global_concurrent_admission(self):
        ids=[self.create(ObjectId()) for _ in range(16)]
        def reserve(i):
            try: self.store.reserve(i); return True
            except QuotaExceeded: return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve,ids)),4)
        entries=self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries']
        self.assertEqual(sum(e['bytes'] for e in entries),20)
    def test_idempotent_reserve_commit_restart(self):
        i=self.create()
        self.store.reserve(i); self.store.reserve(i)
        self.publish(i)
        self.store.quota_transition(i,'commit')
        fresh=StorageIntents(self.db,self.policy)
        self.assertEqual(fresh.quota_transition(i,'commit'),'committed')
        self.assertEqual(len(self.db.rows['storage_quota_ledger']['pdf_payload_v1']['entries']),1)
    def test_stage_conflict_and_repeat(self):
        i=self.create()
        with self.assertRaises(IntentConflict): self.store.transition(i,'prepared','write_dispatched','worker_1')
        self.store.reserve(i)
        self.store.transition(i,'prepared','write_dispatched','worker_1')
        self.store.transition(i,'prepared','write_dispatched','worker_1')
        with self.assertRaises(IntentConflict): self.store.transition(i,'file_confirmed','published','worker_1')
    def test_lost_reservation_ack_retained(self):
        i=self.create(); self.db.failure='after_commit'
        with self.assertRaises(StorageUncertain) as error: self.store.reserve(i)
        self.assertNotIn('private',str(error.exception))
        self.assertEqual(self.row(i)['quota_state'],'reserved')
        self.assertEqual(self.store.reserve(i),'reserved')
    def test_failed_commit_retains_reserved(self):
        i=self.create(); self.store.reserve(i); self.publish(i)
        self.db.failure='before_commit'
        with self.assertRaises(StorageUncertain): self.store.quota_transition(i,'commit')
        self.assertEqual(self.row(i)['quota_state'],'reserved')
    def test_release_failure_and_retry(self):
        i=self.create(); self.store.reserve(i)
        self.store.transition(i,'prepared','failed_before_dispatch','worker_1')
        self.db.failure='after_commit'
        with self.assertRaises(StorageUncertain): self.store.quota_transition(i,'release')
        self.assertEqual(self.store.quota_transition(i,'release'),'released')
        with self.assertRaises(IntentConflict): self.store.reserve(i)
    def test_uncertain_and_heartbeat_never_release(self):
        i=self.create(); self.store.reserve(i)
        self.store.mark_uncertain(i,'prepared','cancelled')
        self.store.heartbeat(i,'worker_1')
        with self.assertRaises(IntentConflict): self.store.quota_transition(i,'release')
        self.assertEqual(self.row(i)['quota_state'],'reserved')
    def test_delete_requires_confirmed_deletion(self):
        i=self.create(); self.store.reserve(i); self.publish(i); self.store.quota_transition(i,'commit')
        d=IntentIdentity.new(i.document_id,i.owner_id,i.knowledge_base_id,'delete')
        self.store.create(d,5,'a'*64,'worker_1',i.operation_id)
        with self.assertRaises(IntentConflict): self.store.quota_transition(d,'release')
        self.store.transition(d,'prepared','delete_dispatched','worker_1')
        self.store.transition(d,'delete_dispatched','delete_confirmed','worker_1')
        self.assertEqual(self.store.quota_transition(d,'release'),'released')
        self.assertEqual(self.store.quota_transition(d,'release'),'released')
    def test_cancel_rolls_back_no_cleanup(self):
        import asyncio
        i=self.create(); self.store.reserve(i)
        with self.assertRaises(asyncio.CancelledError):
            with self.store.transaction() as session:
                raise asyncio.CancelledError()
        self.assertEqual(self.row(i)['quota_state'],'reserved')

    def test_byte_limits_independent_of_counts(self):
        for policy, distinct in [(QuotaPolicy(8,8,5,4,3),True), (QuotaPolicy(20,7,5,4,3),False)]:
            self.db=Database(); self.store=StorageIntents(self.db,policy)
            self.store.reserve(self.create())
            with self.assertRaises(QuotaExceeded):
                self.store.reserve(self.create(ObjectId() if distinct else self.owner))

    def test_failed_release_retains_capacity(self):
        i=self.create(); self.store.reserve(i)
        self.store.transition(i,'prepared','failed_before_dispatch','worker_1')
        self.db.failure='before_commit'
        with self.assertRaises(StorageUncertain): self.store.quota_transition(i,'release')
        self.assertEqual(self.row(i)['quota_state'],'reserved')

    def test_stale_worker_and_failed_delete_cannot_release(self):
        from datetime import datetime,timezone
        i=self.create(); self.store.reserve(i); self.publish(i); self.store.quota_transition(i,'commit')
        self.db.rows['storage_operation_intents'][i.operation_id]['heartbeat_at']=datetime(2000,1,1,tzinfo=timezone.utc)
        with self.assertRaises(IntentConflict): self.store.heartbeat(i,'other_worker')
        d=IntentIdentity.new(i.document_id,i.owner_id,i.knowledge_base_id,'delete')
        self.store.create(d,5,'a'*64,'worker_1',i.operation_id)
        self.store.transition(d,'prepared','failed_before_dispatch','worker_1')
        with self.assertRaises(IntentConflict): self.store.quota_transition(d,'release')
        self.assertEqual(self.row(i)['quota_state'],'committed')

    def test_invalid_size_and_checksum_no_intent(self):
        i=IntentIdentity.new(ObjectId(),self.owner,self.base)
        with self.assertRaises(QuotaExceeded): self.store.create(i,6,'a'*64,'worker_1')
        with self.assertRaises(ValueError): self.store.create(i,5,'secret','worker_1')
        self.assertEqual(self.db.rows,{})

if __name__=='__main__': unittest.main()
