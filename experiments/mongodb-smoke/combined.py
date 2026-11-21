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
    validate_recovery(reconnect, pdfs)
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


def validate_recovery(reconnect, pdfs):
    """D3B primitives only. Faults are application injection, never network faults."""
    import json
    import time
    from pathlib import Path
    from storage_contract import StorageUncertain
    client, db = reconnect()
    store = StorageIntents(db, POLICY)  # Same persisted test policy as lifecycle.py.
    policy_patch = patch.object(recovery, 'StorageIntents', side_effect=lambda database: StorageIntents(database, POLICY))
    policy_patch.start()
    maintenance = dict(operator=WORKER, evidence_id='disposable_drained',
                       writers_stopped=True, inflight_resolved=True)
    results = []
    report = Path('/evidence/recovery.json')
    version = db.command('buildInfo')['version']
    hello = client.admin.command('hello')
    assert version == '8.0.38' and hello['isWritablePrimary'] and len(hello['hosts']) == 1

    @contextmanager
    def scenario(name):
        start = time.monotonic()
        row = dict(name=name, outcome='failed')
        results.append(row)
        try:
            yield
            row['outcome'] = 'passed'
        finally:
            row['seconds'] = round(time.monotonic() - start, 3)
            report.write_text(json.dumps(dict(scenarios=results,
                passed=sum(r['outcome'] == 'passed' for r in results),
                failed=sum(r['outcome'] == 'failed' for r in results), skipped=0,
                planned=20, not_run=20-len(results),
                scope='real_transactions_gridfs_primitives',
                mongodb_version=version, replica_members=1, tls_scoped_identity_verified=True,
                acknowledgement_loss='simulated_after_real_commit',
                concurrency='separate_authenticated_clients_server_write_conflict')))

    def descriptor(identity, data):
        key = str(identity.document_id) + '.pdf'
        return dict(original_filename='synthetic.pdf', stored_filename=key,
                    storage_path=key, file_size=len(data), content_type='application/pdf')

    def new(data):
        identity = IntentIdentity.new(ObjectId(), ObjectId(), ObjectId())
        db.knowledge_bases.insert_one(dict(_id=identity.knowledge_base_id, owner_id=identity.owner_id))
        return identity, descriptor(identity, data)

    def prepare(identity, desc, data):
        return store.prepare_upload(identity, desc, hashlib.sha256(data).hexdigest(), WORKER)

    def capacity(identity, state, growth):
        entries = db.storage_quota_ledger.find_one({'_id': 'pdf_payload_v1'})['entries']
        entries = [e for e in entries if e['operation_id'] == identity.operation_id]
        assert ([e['state'] for e in entries] == ([state] if state else []))
        usage = db.resource_usage.find_one({'_id': identity.owner_id}) or {}
        assert (identity.document_id in usage.get('documents', [])) == (growth and state is not None)

    def review(identity, kind='publication', evidence=None):
        return getattr(recovery, 'review_' + kind)(db, identity,
            maintenance=maintenance if evidence is None else evidence)

    def apply(identity, plan, kind='publication'):
        # The actual finalizer has no authority to call either adapter mutation.
        with patch.object(GridFSPDFStorage, 'delete', side_effect=AssertionError('Unexpected PDF deletion')), \
             patch.object(GridFSPDFStorage, 'save', side_effect=AssertionError('Unexpected PDF upload')):
            return getattr(recovery, 'apply_' + kind)(db, identity,
                review_token=plan['review_token'], maintenance=maintenance)

    @contextmanager
    def commit_fault(after):
        original = StorageIntents.transaction
        @contextmanager
        def transaction(self):
            with original(self) as session:
                yield session
                if not after:
                    raise AutoReconnect('synthetic rollback injection')
            if after:
                raise StorageTransactionError('unknown_commit_result', 'commit')
        with patch.object(StorageIntents, 'transaction', transaction):
            yield

    def payload(identity):
        from gridfs import GridFSBucket
        with GridFSBucket(db, bucket_name='pdf_blobs').open_download_stream(identity.document_id) as stream:
            return stream.read()

    def snapshot(identity, deletion=None):
        """Exact persisted participants; bounded synthetic records, never logged."""
        ids = [identity.operation_id] + ([deletion.operation_id] if deletion else [])
        return dict(
            intents=list(db.storage_operation_intents.find({'_id': {'$in': ids}}).sort('_id', 1)),
            base=db.knowledge_bases.find_one({'_id': identity.knowledge_base_id}),
            document=db.documents.find_one({'_id': identity.document_id}),
            quota=db.storage_quota_ledger.find_one({'_id': 'pdf_payload_v1'}),
            growth=db.resource_usage.find_one({'_id': identity.owner_id}),
            chunks=list(db.document_chunks.find({'document_id': identity.document_id}).sort('_id', 1)),
            original=db['pdf_blobs.files'].find_one({'_id': identity.document_id}),
            original_chunks=list(db['pdf_blobs.chunks'].find({'files_id': identity.document_id}).sort('n', 1)))

    try:
        data = pdfs[0][0]
        with scenario('preparation_atomic_rollback'):
            identity, desc = new(data)
            with commit_fault(False):
                rejected(lambda: prepare(identity, desc, data), StorageUncertain)
            assert db.storage_operation_intents.find_one({'_id': identity.operation_id}) is None
            assert not db.knowledge_bases.find_one({'_id': identity.knowledge_base_id}).get('_document_ids')
        with scenario('preparation_missing_foreign_kb'):
            missing = IntentIdentity.new(ObjectId(), ObjectId(), ObjectId())
            assert prepare(missing, descriptor(missing, data), data) is None
            foreign = IntentIdentity.new(ObjectId(), ObjectId(), identity.knowledge_base_id)
            assert prepare(foreign, descriptor(foreign, data), data) is None
            assert db.storage_operation_intents.count_documents({'_id': {'$in': [missing.operation_id, foreign.operation_id]}}) == 0
        with scenario('preparation_real_concurrent_kb_delete_conflict'):
            other, other_db = reconnect()
            try:
                with client.start_session() as session:
                    session.start_transaction()
                    db.knowledge_bases.delete_one({'_id': identity.knowledge_base_id}, session=session)
                    # Separate connection reaches the same KB while deletion owns its write lock.
                    try:
                        StorageIntents(other_db, POLICY).prepare_upload(identity, desc, hashlib.sha256(data).hexdigest(), WORKER)
                    except StorageTransactionError as error:
                        assert error.category == 'transaction_write_conflict'
                    else:
                        raise AssertionError('Expected real server write conflict')
                    session.commit_transaction()
                assert db.storage_operation_intents.find_one({'_id': identity.operation_id}) is None
                assert db.knowledge_bases.find_one({'_id': identity.knowledge_base_id}) is None
            finally:
                other.close()
        with scenario('preparation_simulated_lost_ack_retains_reservation'):
            identity, desc = new(data)
            with commit_fault(True):
                rejected(lambda: prepare(identity, desc, data), StorageUncertain)
            assert identity.document_id in db.knowledge_bases.find_one({'_id': identity.knowledge_base_id})['_document_ids']
            assert db.storage_operation_intents.find_one({'_id': identity.operation_id})['stage'] == 'prepared'
            rejected(lambda: prepare(identity, desc, data))

        for growth in (False, True):
            suffix = 'growth_on' if growth else 'growth_off'
            data = pdfs[int(growth)][0]
            with scenario('preparation_commit_duplicate_identity_' + suffix):
                identity, desc = new(data)
                assert prepare(identity, desc, data)['stage'] == 'prepared'
                store.reserve(identity, growth_policy=GROWTH if growth else None)
                capacity(identity, 'reserved', growth)
                rejected(lambda: prepare(identity, desc, data))
                duplicate = IntentIdentity.new(identity.document_id, identity.owner_id, identity.knowledge_base_id)
                # Duplicate document rejected at quota admission, before remote dispatch.
                prepare(duplicate, desc, data)
                rejected(lambda: store.reserve(duplicate))
                assert db.storage_operation_intents.find_one({'_id': duplicate.operation_id})['quota_state'] == 'none'
                # Fixture-only removal of the proved never-dispatched competing intent.
                db.storage_operation_intents.delete_one({'_id': duplicate.operation_id, 'stage': 'prepared', 'quota_state': 'none'})
                store.transition(identity, 'prepared', 'write_dispatched', WORKER)
                with tempfile.TemporaryDirectory() as root:
                    storage = GridFSPDFStorage(db, owner_id=identity.owner_id,
                        knowledge_base_id=identity.knowledge_base_id, temp_root=root)
                    storage.save(SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf',
                        file=io.BytesIO(data)), identity.document_id, 5 * 1024**2)
            with scenario('publication_corrupt_incomplete_identity_metadata_rejection_' + suffix):
                chunk = db['pdf_blobs.chunks'].find_one({'files_id': identity.document_id})
                for replacement in (None, b'x' * len(chunk['data'])):
                    if replacement is None:
                        db['pdf_blobs.chunks'].delete_one({'_id': chunk['_id']})
                    else:
                        db['pdf_blobs.chunks'].update_one({'_id': chunk['_id']}, {'$set': {'data': replacement}})
                    rejected(lambda: review(identity))
                    db['pdf_blobs.chunks'].replace_one({'_id': chunk['_id']}, chunk, upsert=True)
                db['pdf_blobs.files'].update_one({'_id': identity.document_id}, {'$set': {'metadata.owner_id': ObjectId()}})
                rejected(lambda: review(identity))
                db['pdf_blobs.files'].update_one({'_id': identity.document_id}, {'$set': {'metadata.owner_id': identity.owner_id}})
                db.documents.insert_one({'_id': identity.document_id, 'owner_id': ObjectId()})
                rejected(lambda: review(identity))
                db.documents.delete_one({'_id': identity.document_id})
            with scenario('publication_maintenance_stale_heartbeat_changed_review_' + suffix):
                db.storage_operation_intents.update_one({'_id': identity.operation_id}, {'$set': {'heartbeat_at': datetime(2000, 1, 1, tzinfo=timezone.utc)}})
                rejected(lambda: review(identity, evidence={}))
                rejected(lambda: review(identity, evidence={**maintenance, 'writers_stopped': False}))
                plan = review(identity)
                db.knowledge_bases.update_one({'_id': identity.knowledge_base_id}, {'$inc': {'_document_revision': 1}})
                rejected(lambda: apply(identity, plan))
            with scenario('publication_real_rollback_retains_capacity_' + suffix):
                plan = review(identity)
                before = snapshot(identity)
                with commit_fault(False):
                    rejected(lambda: apply(identity, plan), StorageUncertain)
                assert db.documents.find_one({'_id': identity.document_id}) is None
                capacity(identity, 'reserved', growth)
                assert payload(identity) == data
                assert snapshot(identity) == before
            with scenario('publication_matching_metadata_simulated_ack_idempotency_' + suffix):
                if growth:
                    row = db.storage_operation_intents.find_one({'_id': identity.operation_id})
                    db.documents.insert_one({**desc, '_id': identity.document_id, 'owner_id': identity.owner_id,
                        'knowledge_base_id': identity.knowledge_base_id, 'storage_backend': 'gridfs',
                        'storage_file_id': identity.document_id, 'storage_operation_id': identity.operation_id,
                        'storage_checksum': row['checksum'], 'status': 'uploaded',
                        'created_at': row['created_at'], 'updated_at': row['created_at']})
                plan = review(identity)
                before = snapshot(identity)
                with commit_fault(True):
                    rejected(lambda: apply(identity, plan), StorageUncertain)
                capacity(identity, 'committed', growth)
                # Matching metadata is deliberately left untouched by production recovery.
                if growth:
                    assert db.documents.find_one({'_id': identity.document_id}) == before['document']
                first = snapshot(identity)
                assert first['original'] == before['original']
                assert first['original_chunks'] == before['original_chunks']
                assert first['growth'] == before['growth']
                assert apply(identity, review(identity)) == 'already_finalized'
                assert snapshot(identity) == first
                capacity(identity, 'committed', growth)
                assert db.documents.count_documents({'_id': identity.document_id}) == 1
                assert payload(identity) == data
            with scenario('deletion_orphan_vector_claim_maintenance_rejection_' + suffix):
                deletion = IntentIdentity.new(identity.document_id, identity.owner_id, identity.knowledge_base_id, 'delete')
                store.create(deletion, len(data), hashlib.sha256(data).hexdigest(), WORKER, identity.operation_id)
                db.documents.update_one({'_id': identity.document_id}, {'$set': {'_operation': deletion.operation_id}})
                store.transition(deletion, 'prepared', 'delete_dispatched', WORKER)
                # Fixture has never created vectors; affirmative stage records that fact.
                store.transition(deletion, 'delete_dispatched', 'vectors_deleted', WORKER)
                doc = db.documents.find_one({'_id': identity.document_id})
                assert storage.delete(doc) == DeletionOutcome.DELETED
                store.mark_uncertain(deletion, 'vectors_deleted')
                orphan = db['pdf_blobs.chunks'].insert_one({'files_id': identity.document_id, 'n': 0, 'data': b'x'}).inserted_id
                rejected(lambda: review(deletion, 'deletion'))
                db['pdf_blobs.chunks'].delete_one({'_id': orphan})
                row = db.storage_operation_intents.find_one({'_id': deletion.operation_id})
                db.storage_operation_intents.update_one({'_id': deletion.operation_id}, {'$set': {'stage': 'delete_dispatched', 'last_acknowledged_stage': 'delete_dispatched'}})
                rejected(lambda: review(deletion, 'deletion'))
                db.storage_operation_intents.replace_one({'_id': deletion.operation_id}, row)
                rejected(lambda: review(deletion, 'deletion', {}))
                plan = review(deletion, 'deletion')
                db.documents.update_one({'_id': identity.document_id}, {'$set': {'_operation': ObjectId()}})
                rejected(lambda: apply(deletion, plan, 'deletion'))
                db.documents.update_one({'_id': identity.document_id}, {'$set': {'_operation': deletion.operation_id}})
            with scenario('deletion_real_rollback_' + suffix):
                db.document_chunks.insert_one(dict(_id=ObjectId(), document_id=identity.document_id,
                    owner_id=identity.owner_id, knowledge_base_id=identity.knowledge_base_id,
                    generation='synthetic_recovery', chunk_index=0, text='Synthetic recovery fixture.'))
                plan = review(deletion, 'deletion')
                before = snapshot(identity, deletion)
                with commit_fault(False):
                    rejected(lambda: apply(deletion, plan, 'deletion'), StorageUncertain)
                assert snapshot(identity, deletion) == before
                capacity(identity, 'committed', growth)
            with scenario('deletion_simulated_ack_exactly_once_release_' + suffix):
                plan = review(deletion, 'deletion')
                with commit_fault(True):
                    rejected(lambda: apply(deletion, plan, 'deletion'), StorageUncertain)
                first = snapshot(identity, deletion)
                assert apply(deletion, review(deletion, 'deletion'), 'deletion') == 'already_finalized'
                assert snapshot(identity, deletion) == first
                capacity(identity, None, growth)
                assert db.document_chunks.count_documents({'document_id': identity.document_id}) == 0
                assert identity.document_id not in db.knowledge_bases.find_one({'_id': identity.knowledge_base_id})['_document_ids']
                assert db.documents.find_one({'_id': identity.document_id}) is None
                assert db['pdf_blobs.files'].count_documents({'_id': identity.document_id}) == 0
                assert db['pdf_blobs.chunks'].count_documents({'files_id': identity.document_id}) == 0
    finally:
        policy_patch.stop()
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
