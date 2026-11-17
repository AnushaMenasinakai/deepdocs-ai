"""Synchronous growth coordination within the existing GridFS metadata transaction."""
from bson import ObjectId
from pymongo import ReturnDocument
from growth_limits import GrowthError


def reserve(database, session, identity, settings):
    ledger = database.get_collection('resource_usage')
    row = ledger.find_one_and_update({'_id': identity.owner_id},
        {'$inc': {'revision': 1}, '$setOnInsert': {'documents': [], 'knowledge_bases': []}},
        upsert=True, session=session, return_document=ReturnDocument.AFTER)
    held = row.get('documents') if row else None
    if not isinstance(held, list) or any(not isinstance(i, ObjectId) for i in held) or identity.document_id in held:
        raise GrowthError()
    docs = database.get_collection('documents')
    if docs.find_one({'_id': identity.document_id}, session=session) is not None:
        raise GrowthError()
    used = set(held)
    used.update(r['_id'] for r in docs.find({'owner_id': identity.owner_id}, {'_id': 1}, session=session).limit(settings.documents + 1))
    for base in database.get_collection('knowledge_bases').find(
            {'owner_id': identity.owner_id, '_document_ids.0': {'$exists': True}},
            {'_document_ids': 1}, session=session).limit(settings.documents + 1):
        pending = base.get('_document_ids')
        if not isinstance(pending, list) or any(not isinstance(i, ObjectId) for i in pending):
            raise GrowthError()
        used.update(pending)
    # This upload already owns a KB reservation; it is the candidate, not a second PDF.
    used.discard(identity.document_id)
    if len(used) >= settings.documents:
        raise GrowthError(True)
    ledger.update_one({'_id': identity.owner_id}, {'$addToSet': {'documents': identity.document_id}}, session=session)


def release(database, session, record):
    if record.get('growth_reserved') is not True:
        return
    ledger = database.get_collection('resource_usage')
    row = ledger.find_one({'_id': record['owner_id']}, session=session)
    if row is None or record['document_id'] not in row.get('documents', []):
        raise GrowthError()  # Do not invent evidence that accounting was released.
    ledger.update_one({'_id': record['owner_id']},
        {'$pull': {'documents': record['document_id']}, '$inc': {'revision': 1}}, session=session)
