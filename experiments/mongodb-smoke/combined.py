"""Disposable real-transaction policy checks; no routes, providers or configuration IO."""
import asyncio
import hashlib
import io
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import AutoReconnect
from config import GrowthSettings
import growth_limits
import gridfs_growth_recovery as recovery
import policy_readiness
from gridfs_storage import GridFSPDFStorage
from storage_contract import DeletionOutcome
from storage_intents import StorageIntents, IntentIdentity, IntentConflict, QuotaPolicy, QuotaExceeded, StorageTransactionError


POLICY = QuotaPolicy(global_documents=2, user_documents=2)
GROWTH = GrowthSettings(enabled=True, documents=1, history=2)
WORKER = 'combined_smoke'


@contextmanager
def lost_commit(store):
    """Lose the acknowledgement AFTER a real transaction commits, never replay it."""
    original = store.transaction
    @contextmanager
    def transaction():
        with original() as session:
            yield session
        raise StorageTransactionError('unknown_commit_result', 'commit')
    with patch.object(store, 'transaction', transaction):
        yield


def rejected(call, error=IntentConflict):
    try:
        call()
    except error:
        return
    raise AssertionError('Expected fail-closed rejection')


def validate_sync(reconnect, pdfs):
    client, db = reconnect()
    try:
        store = StorageIntents(db, POLICY)
        baseline = db.storage_quota_ledger.find_one({'_id': 'pdf_payload_v1'})['entries']
        assert len(baseline) == 1  # Existing lifecycle's uncertain entry is preserved.
        def prepare(data, owner=None):
            owner, base = owner or ObjectId(), ObjectId()
            identity = IntentIdentity.new(ObjectId(), owner, base)
            db.knowledge_bases.insert_one({'_id': base, 'owner_id': owner,
                '_document_ids': [identity.document_id], '_document_revision': 0})
            store.create(identity, len(data), hashlib.sha256(data).hexdigest(), WORKER)
            return identity
        def held(identity):
            row = db.storage_quota_ledger.find_one({'_id': 'pdf_payload_v1'})
            growth = db.resource_usage.find_one({'_id': identity.owner_id})
            return (any(e['operation_id'] == identity.operation_id for e in row['entries']),
                    growth is not None and identity.document_id in growth['documents'])
        # Recovery uses the SAME test policy as the original lifecycle, not production defaults.
        with patch.object(recovery, 'StorageIntents', side_effect=lambda database: StorageIntents(database, POLICY)):
            identity = prepare(pdfs[0][0])
            with lost_commit(store):
                rejected(lambda: store.reserve(identity, growth_policy=GROWTH), StorageTransactionError)
            assert held(identity) == (True, True)
            # A cancelled pre-dispatch workflow retains capacity until operator review.
            token = recovery.review(db, identity)['review_token']
            original_release = StorageIntents._quota_transition
            def failed_release(self, *args, **kwargs):
                original_release(self, *args, **kwargs)
                raise AutoReconnect('synthetic acknowledgement loss inside transaction')
            with patch.object(StorageIntents, '_quota_transition', failed_release):
                rejected(lambda: recovery.apply(db, identity, review_token=token, operator=WORKER), StorageTransactionError)
            assert held(identity) == (True, True)
            # Metadata and actual payload changes invalidate a previously reviewed action.
            db.documents.insert_one({'_id': identity.document_id})
            rejected(lambda: recovery.apply(db, identity, review_token=token, operator=WORKER))
            db.documents.delete_one({'_id': identity.document_id})
            with tempfile.TemporaryDirectory() as root:
                storage = GridFSPDFStorage(db, owner_id=identity.owner_id,
                    knowledge_base_id=identity.knowledge_base_id, temp_root=root)
                data = pdfs[0][0]
                doc = storage.save(SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf',
                    file=io.BytesIO(data)), identity.document_id, 5 * 1024**2)
                doc.update(_id=identity.document_id, owner_id=identity.owner_id, knowledge_base_id=identity.knowledge_base_id)
                rejected(lambda: recovery.apply(db, identity, review_token=token, operator=WORKER))
                assert storage.delete(doc) == DeletionOutcome.DELETED
            db.knowledge_bases.update_one({'_id': identity.knowledge_base_id}, {'$inc': {'_document_revision': 1}})
            rejected(lambda: recovery.apply(db, identity, review_token=token, operator=WORKER))
            token = recovery.review(db, identity)['review_token']
            assert recovery.apply(db, identity, review_token=token, operator=WORKER) == 'released'
            assert held(identity) == (False, False)
            rejected(lambda: store.transition(identity, 'prepared', 'write_dispatched', WORKER))
            print('PASS: combined lost commit, retained cancellation state, release rollback and reviewed recovery fencing.', flush=True)

            # Separate authenticated clients compete for the one remaining global slot.
            contenders = [prepare(pdfs[1][0]) for _ in range(2)]
            barrier = threading.Barrier(2)
            def admit(identity):
                other, database = reconnect()
                try:
                    barrier.wait(timeout=5)
                    try:
                        StorageIntents(database, POLICY).reserve(identity, growth_policy=GROWTH)
                        return True
                    except QuotaExceeded:
                        return False
                    except StorageTransactionError as error:
                        assert error.category in ('transaction_write_conflict', 'duplicate_identity_conflict')
                        return False
                finally:
                    other.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(admit, contenders))
            assert sum(results) == 1
            for identity, admitted in zip(contenders, results):
                assert held(identity) == (admitted, admitted)
            winner = contenders[results.index(True)]
            # Global exhaustion must not leave a growth-only reservation.
            loser = contenders[results.index(False)]
            rejected(lambda: store.reserve(loser, growth_policy=GROWTH), QuotaExceeded)
            assert held(loser) == (False, False)
            token = recovery.review(db, winner)['review_token']
            recovery.apply(db, winner, review_token=token, operator=WORKER)
            # Growth exhaustion tested with available global capacity.
            blocked = prepare(pdfs[0][0])
            db.resource_usage.insert_one({'_id': blocked.owner_id, 'revision': 0,
                'documents': [ObjectId()], 'knowledge_bases': []})
            rejected(lambda: store.reserve(blocked, growth_policy=GROWTH), growth_limits.GrowthError)
            assert held(blocked) == (False, False)
            print('PASS: separate-client combined admission; global and growth exhaustion without partial accounting.', flush=True)

            # Actual upload followed by atomic metadata and BOTH accounting releases.
            identity = prepare(pdfs[1][0])
            data = pdfs[1][0]
            store.reserve(identity, growth_policy=GROWTH)
            store.transition(identity, 'prepared', 'write_dispatched', WORKER)
            with tempfile.TemporaryDirectory() as root:
                storage = GridFSPDFStorage(db, owner_id=identity.owner_id, knowledge_base_id=identity.knowledge_base_id, temp_root=root)
                doc = storage.save(SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf', file=io.BytesIO(data)), identity.document_id, 5 * 1024**2)
                doc.update(_id=identity.document_id, owner_id=identity.owner_id, knowledge_base_id=identity.knowledge_base_id,
                    storage_operation_id=identity.operation_id, storage_checksum=hashlib.sha256(data).hexdigest())
                store.transition(identity, 'write_dispatched', 'file_confirmed', WORKER)
                db.documents.insert_one(doc)
                store.transition(identity, 'file_confirmed', 'published', WORKER)
                store.quota_transition(identity, 'commit')
                deletion = IntentIdentity.new(identity.document_id, identity.owner_id, identity.knowledge_base_id, 'delete')
                store.create(deletion, len(data), doc['storage_checksum'], WORKER, identity.operation_id)
                db.documents.update_one({'_id': identity.document_id}, {'$set': {'_operation': deletion.operation_id}})
                store.transition(deletion, 'prepared', 'delete_dispatched', WORKER)
                store.transition(deletion, 'delete_dispatched', 'vectors_deleted', WORKER)  # No vectors in this harness.
                assert storage.delete(doc) == DeletionOutcome.DELETED
                store.transition(deletion, 'vectors_deleted', 'file_deleted', WORKER)
                store.finalize_deletion(deletion, WORKER)
            assert held(identity) == (False, False)
            assert db.documents.find_one({'_id': identity.document_id}) is None
            assert db.storage_quota_ledger.find_one({'_id': 'pdf_payload_v1'})['entries'] == baseline
            print('PASS: combined original deletion, metadata cleanup and atomic quota release.', flush=True)
            # Deterministic real write conflict, independently of race scheduling.
            other, other_db = reconnect()
            try:
                with client.start_session() as session:
                    session.start_transaction()
                    db.resource_usage.update_one({'_id': identity.owner_id}, {'$inc': {'revision': 1}}, session=session)
                    try:
                        with StorageIntents(other_db, POLICY).transaction() as competing:
                            other_db.resource_usage.update_one({'_id': identity.owner_id}, {'$inc': {'revision': 1}}, session=competing)
                    except StorageTransactionError as error:
                        assert error.category == 'transaction_write_conflict'
                    else:
                        raise AssertionError('Expected transaction write conflict')
                    finally:
                        session.abort_transaction()
            finally:
                other.close()
            interrupted = prepare(pdfs[0][0])
            store.reserve(interrupted, growth_policy=GROWTH)
            store.transition(interrupted, 'prepared', 'write_dispatched', WORKER)
            store.mark_uncertain(interrupted, 'write_dispatched')
            from storage_reconciliation import ReconciliationInspector
            result = ReconciliationInspector(db).inspect(interrupted)
            assert result.classification == 'ambiguous_manual_review' and not result.mutation_authorized
            rejected(lambda: recovery.review(db, interrupted))
            assert held(interrupted) == (True, True)
            print('PASS: real write conflict; staged post-dispatch interruption retains both quotas for manual review.', flush=True)
    finally:
        client.close()


async def validate_async(databases):
    db = databases[0]
    from storage_schema import StorageSchemaError
    with patch.object(policy_readiness.config, 'load_growth_settings', return_value=GROWTH), \
         patch.object(policy_readiness.config, 'load_usage_budget_settings', return_value=SimpleNamespace(enabled=True)), \
         patch.object(policy_readiness.config, 'load_gridfs_upload_enabled', return_value=True):
        try:
            ready = await policy_readiness.verify(db)
        except StorageSchemaError:
            ready = False
        assert ready is False  # Missing application history/ownership indexes fail closed.
    for collection, name, time in (
        ('ask_history', 'ask_history_owner_created', 'created_at'),
        ('documents', 'documents_owner_created', 'created_at'),
        ('knowledge_bases', 'knowledge_bases_owner_updated', 'updated_at')):
        await db[collection].create_index([('owner_id', 1), (time, -1), ('_id', -1)], name=name)
    with patch.object(growth_limits, 'load_growth_settings', return_value=GROWTH), \
         patch.object(policy_readiness.config, 'load_growth_settings', return_value=GROWTH), \
         patch.object(policy_readiness.config, 'load_usage_budget_settings', return_value=SimpleNamespace(enabled=True)), \
         patch.object(policy_readiness.config, 'load_gridfs_upload_enabled', return_value=True):
        assert await policy_readiness.verify(db)
        owner = ObjectId()
        await db.resource_usage.insert_one({'_id': owner, 'revision': 0, 'documents': [], 'knowledge_bases': []})
        records = [{'_id': ObjectId(), 'owner_id': owner, 'created_at': datetime.now(timezone.utc)} for _ in range(6)]
        async def insert(n):
            try:
                await growth_limits.retain_history(databases[n % 2], owner, records[n])
                return True
            except growth_limits.GrowthError as error:
                assert error.status == 503  # Transaction conflicts fail closed; no retries.
                return False
        results = await asyncio.gather(*(insert(n) for n in range(6)))
        assert any(results)
        expected = sorted([r for r, ok in zip(records, results) if ok], key=lambda r: (r['created_at'], r['_id']), reverse=True)[:2]
        actual = await db.ask_history.find({'owner_id': owner}).sort([('created_at', -1), ('_id', -1)]).to_list()
        assert [r['_id'] for r in actual] == [r['_id'] for r in expected]
        # Force failure after an actual insert: MongoDB must roll back the owner revision AND history.
        before = await db.resource_usage.find_one({'_id': owner})
        failed = {'_id': ObjectId(), 'owner_id': owner, 'created_at': datetime.now(timezone.utc)}
        original = growth_limits.owner_transaction
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def abort(database, identity):
            async with original(database, identity) as value:
                yield value
                raise growth_limits.GrowthError()
        with patch.object(growth_limits, 'owner_transaction', abort):
            try:
                await growth_limits.retain_history(db, owner, failed)
            except growth_limits.GrowthError:
                pass
            else:
                raise AssertionError('History rollback was not exercised')
        assert await db.resource_usage.find_one({'_id': owner}) == before
        assert await db.ask_history.find({'owner_id': owner}).sort([('created_at', -1), ('_id', -1)]).to_list() == actual
        print('PASS: enabled-policy readiness, separate-client history retention and actual transaction rollback.', flush=True)
