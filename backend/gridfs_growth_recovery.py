"""Operator-only reviewed release of terminal/pre-dispatch GridFS reservations.

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
