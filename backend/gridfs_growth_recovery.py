"""Operator-only reviewed reservation release and metadata finalization.

Use an owned synchronous worker or an offline operator process. No routes import
this module. It never mutates GridFS payloads or resumes interrupted remote IO.
"""
import hashlib
import re
from bson import BSON
from storage_intents import StorageIntents, IntentConflict


def _inspect(store, identity, session):
    record = store._record(identity, session)
    scope = {'_id': identity.document_id}
    docs = store.db.get_collection('documents').find_one(scope, projection={'_id': 1}, session=session)
    file = store.db.get_collection('pdf_blobs.files').find_one(scope, projection={'_id': 1}, session=session)
    chunk = store.db.get_collection('pdf_blobs.chunks').find_one({'files_id': identity.document_id}, projection={'_id': 1}, session=session)
    processed = store.db.get_collection('document_chunks').find_one({'document_id': identity.document_id}, projection={'_id': 1}, session=session)
    base = store.db.get_collection('knowledge_bases').find_one(
        {'_id': identity.knowledge_base_id, 'owner_id': identity.owner_id}, session=session)
    ledger = store._ledger(session)
    growth = store.db.get_collection('resource_usage').find_one({'_id': identity.owner_id}, session=session)
    entry = [e for e in ledger['entries'] if e['operation_id'] == identity.operation_id or e['document_id'] == identity.document_id]
    if (identity.operation_type != 'upload' or record.get('growth_reserved') is not True
            or record['stage'] not in ('prepared', 'failed_before_dispatch')
            or record['quota_state'] not in ('reserved', 'released')
            or any(value is not None for value in (docs, file, chunk, processed)) or base is None):
        raise IntentConflict('Recovery requires review; payload/state is not safely releasable.')
    if record['quota_state'] == 'reserved':
        if (len(entry) != 1 or any(entry[0].get(k) != record[k] for k in
                ('document_id', 'owner_id', 'knowledge_base_id'))
                or entry[0]['operation_id'] != identity.operation_id
                or entry[0]['bytes'] != record['expected_size'] or entry[0]['state'] != 'reserved'
                or growth is None or identity.document_id not in growth.get('documents', [])
                or identity.document_id not in base.get('_document_ids', [])):
            raise IntentConflict('Recovery accounting identity conflict.')
    elif (entry or (growth and identity.document_id in growth.get('documents', []))
            or identity.document_id in base.get('_document_ids', [])):
        raise IntentConflict('Recovery accounting identity conflict.')
    # Exact reviewed state, including revisions/heartbeats: any change requires review again.
    token = hashlib.sha256(BSON.encode({'intent': record, 'ledger': ledger, 'growth': growth, 'base': base})).hexdigest()
    return record, token


def review(database, identity):
    store = StorageIntents(database)
    with store.transaction() as session:
        record, token = _inspect(store, identity, session)
        return {'operation_id': str(identity.operation_id), 'stage': record['stage'],
                'action': 'already_released' if record['quota_state'] == 'released' else 'release_before_dispatch',
                'review_token': token}


def apply(database, identity, *, review_token, operator):
    if not isinstance(operator, str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', operator):
        raise ValueError('Operator audit identity required.')
    if not isinstance(review_token, str) or not re.fullmatch('[0-9a-f]{64}', review_token):
        raise ValueError('Exact review token required.')
    store = StorageIntents(database)
    with store.transaction() as session:
        record, token = _inspect(store, identity, session)
        if token != review_token:
            raise IntentConflict('Reviewed state changed; inspect again.')
        if record['quota_state'] == 'released':
            return 'already_released'
        # Fence the existing worker in the same transaction as both quota releases.
        # Its transition from prepared cannot succeed after this terminal stage.
        record.update(stage='failed_before_dispatch', previous_stage=record['stage'],
            last_acknowledged_stage='failed_before_dispatch', reconciliation_status='none',
            growth_recovery={'operator': operator, 'review_token': token})
        store.intents.replace_one({'_id': identity.operation_id}, record, session=session)
        result = database.get_collection('knowledge_bases').update_one(
            {'_id': identity.knowledge_base_id, 'owner_id': identity.owner_id},
            {'$pull': {'_document_ids': identity.document_id}, '$inc': {'_document_revision': 1}}, session=session)
        if result.matched_count != 1:
            raise IntentConflict('Recovery knowledge-base identity conflict.')
        store._quota_transition(identity, 'release', session)
        return 'released'

# Post-dispatch recovery is maintenance-only. These attestations are an operator
# prerequisite, not a claim that Python can fence an already dispatched GridFS IO.
def _maintenance(value):
    if (not isinstance(value, dict) or set(value) != {'operator', 'evidence_id', 'writers_stopped', 'inflight_resolved'}
            or value['writers_stopped'] is not True or value['inflight_resolved'] is not True
            or any(not isinstance(value[k], str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', value[k])
                   for k in ('operator', 'evidence_id'))):
        raise IntentConflict('Verified maintenance evidence and operator identity required.')
    return dict(value)


def _finalization_state(store, identity, action, session, maintenance):
    from datetime import datetime, timezone
    from storage_reconciliation import ReconciliationInspector
    from storage_intents import publication_descriptor
    inspector = ReconciliationInspector(store.db)
    report = inspector._inspect(identity, datetime.now(timezone.utc), session)
    allowed = {'published_upload_at_snapshot', 'completed_deletion_at_snapshot',
               'reconciliation_or_worker_evidence_required', 'retain_quota',
               'metadata_publication_uncertain', 'worker_termination_unproven', 'incomplete_deletion'}
    if report.classification == 'identity_conflict' or not set(report.reasons) <= allowed:
        raise IntentConflict('Recovery evidence is incomplete or conflicting.')
    row = store._record(identity, session)
    doc = store.db.get_collection('documents').find_one({'_id': identity.document_id}, session=session)
    base = store.db.get_collection('knowledge_bases').find_one({'_id': identity.knowledge_base_id}, session=session)
    if base is None or base.get('owner_id') != identity.owner_id:
        raise IntentConflict('Owned knowledge base required.')
    ledger = store._ledger(session)
    growth = store.db.get_collection('resource_usage').find_one({'_id': identity.owner_id}, session=session)
    source = row if action == 'publication' else store.intents.find_one({'_id': row['reservation_id']}, session=session)
    # Inspector already verifies immutable linkage, checksum, ledger entry and claim.
    related = inspector._many('storage_operation_intents',
        {'document_id': identity.document_id},
        ('_id', 'stage', 'reconciliation_status'), 'storage_intent_owner_document', 21, session, [('created_at', 1)])
    expected_ids = {identity.operation_id, source['_id']}
    if len(related) != len(expected_ids) or {r['_id'] for r in related} != expected_ids:
        raise IntentConflict('Related operations require quarantine.')
    complete = False
    if action == 'publication':
        if (identity.operation_type != 'upload' or row['stage'] not in ('write_dispatched', 'file_confirmed', 'published')
                or report.file_state != 'checksum_verified_at_snapshot'
                or row['quota_state'] not in ('reserved', 'committed')
                or identity.document_id not in base.get('_document_ids', [])):
            raise IntentConflict('Publication evidence unavailable.')
        descriptor = publication_descriptor(identity, row.get('publication_descriptor'))
        if descriptor['file_size'] != row['expected_size']:
            raise IntentConflict('Publication size conflict.')
        expected = {**descriptor, '_id': identity.document_id, 'owner_id': identity.owner_id,
            'knowledge_base_id': identity.knowledge_base_id, 'storage_backend': 'gridfs',
            'storage_file_id': identity.document_id, 'storage_operation_id': identity.operation_id,
            'storage_checksum': row['checksum']}
        if doc is not None and (any(doc.get(k) != v for k, v in expected.items())
                or '_operation' in doc):
            raise IntentConflict('Publication metadata conflict.')
        complete = row['stage'] == 'published' and row['quota_state'] == 'committed' and row['reconciliation_status'] == 'none' and doc is not None
        if not complete:
            # No reconstruction over processing/indexing work or unpublished generations.
            if doc is not None and (doc.get('status') != 'uploaded' or any(k in doc for k in ('chunk_generation', 'vector_index', 'embedding'))):
                raise IntentConflict('Publication already has downstream work.')
            if store.db.get_collection('document_chunks').find_one({'document_id': identity.document_id}, session=session) is not None:
                raise IntentConflict('Publication has application chunks.')
    else:
        if identity.operation_type != 'delete' or report.file_state != 'absent_at_snapshot':
            raise IntentConflict('Deletion absence is unconfirmed.')
        complete = report.classification == 'confirmed_consistent' and row['stage'] == 'delete_confirmed'
        if not complete and (row['stage'] not in ('vectors_deleted', 'file_deleted')
                or row['last_acknowledged_stage'] != row['stage'] or doc is None
                or source['stage'] != 'published' or source['quota_state'] != 'committed'
                or source['reconciliation_status'] != 'none'
                or identity.document_id not in base.get('_document_ids', [])):
            raise IntentConflict('Deletion/vector acknowledgement requires quarantine.')
    held = growth.get('documents', []) if growth else []
    should_hold = not (action == 'deletion' and complete)
    if source.get('growth_reserved') is True and ((identity.document_id in held) != should_hold):
        raise IntentConflict('Growth accounting conflicts with recovery.')
    if source.get('growth_reserved') is not True and identity.document_id in held:
        raise IntentConflict('Unexpected growth reservation.')
    # Record bounded chunk identity/generation evidence, never PDF text in tokens/logs.
    # Large/unknown chunk sets remain quarantined rather than using unbounded reads.
    chunks = inspector._many('document_chunks', {'document_id': identity.document_id},
        ('_id', 'owner_id', 'knowledge_base_id', 'document_id', 'generation', 'chunk_index'),
        'chunks_owner_document_generation_order', 501, session, [('generation', 1), ('chunk_index', 1)])
    if len(chunks) > 500 or any(c.get('knowledge_base_id') != identity.knowledge_base_id
                              or c.get('owner_id') != identity.owner_id for c in chunks):
        raise IntentConflict('Application chunk bound or identity conflict.')
    file_metadata = inspector._one('pdf_blobs.files', {'_id': identity.document_id},
        ('_id', 'metadata', 'length', 'chunkSize', 'uploadDate', 'filename'), session)
    state = {'intent': row, 'source': source, 'document': doc, 'base': base, 'ledger': ledger,
             'growth': growth, 'related': related, 'chunks': chunks, 'file_metadata': file_metadata,
             'action': action, 'maintenance': maintenance}
    token = hashlib.sha256(BSON.encode(state)).hexdigest()
    return row, doc, complete, token


def _review_finalization(database, identity, action, maintenance):
    maintenance = _maintenance(maintenance)
    store = StorageIntents(database)
    with store.transaction() as session:
        _, _, complete, token = _finalization_state(store, identity, action, session, maintenance)
        return {'operation_id': str(identity.operation_id), 'action': 'already_finalized' if complete else action,
                'review_token': token}


def _apply_finalization(database, identity, action, review_token, maintenance):
    maintenance = _maintenance(maintenance)
    if not isinstance(review_token, str) or not re.fullmatch('[0-9a-f]{64}', review_token):
        raise ValueError('Exact review token required.')
    store = StorageIntents(database)
    with store.transaction() as session:
        row, doc, complete, token = _finalization_state(store, identity, action, session, maintenance)
        if token != review_token:
            raise IntentConflict('Reviewed state changed; inspect again.')
        if complete:
            return 'already_finalized'
        from datetime import datetime, timezone
        row['recovery_review'] = {**maintenance, 'review_token': token, 'action': action}
        row['reconciliation_status'] = 'none'
        row['error_category'] = None
        if action == 'publication':
            if doc is None:
                descriptor = row['publication_descriptor']
                doc = {**descriptor, '_id': identity.document_id, 'owner_id': identity.owner_id,
                    'knowledge_base_id': identity.knowledge_base_id, 'storage_backend': 'gridfs',
                    'storage_file_id': identity.document_id, 'storage_operation_id': identity.operation_id,
                    'storage_checksum': row['checksum'], 'status': 'uploaded',
                    'created_at': row['created_at'], 'updated_at': datetime.now(timezone.utc)}
                store.db.get_collection('documents').insert_one(doc, session=session)
            row.update(previous_stage=row['stage'], stage='published', last_acknowledged_stage='published')
            store.intents.replace_one({'_id': identity.operation_id}, row, session=session)
            store._quota_transition(identity, 'commit', session)
        else:
            row.update(previous_stage=row['stage'], stage='file_deleted', last_acknowledged_stage='file_deleted')
            store.intents.replace_one({'_id': identity.operation_id}, row, session=session)
            store._finalize_deletion(identity, row['worker_id'], session)
        return 'finalized'


def review_publication(database, identity, *, maintenance):
    return _review_finalization(database, identity, 'publication', maintenance)


def apply_publication(database, identity, *, review_token, maintenance):
    return _apply_finalization(database, identity, 'publication', review_token, maintenance)


def review_deletion(database, identity, *, maintenance):
    return _review_finalization(database, identity, 'deletion', maintenance)


def apply_deletion(database, identity, *, review_token, maintenance):
    return _apply_finalization(database, identity, 'deletion', review_token, maintenance)
