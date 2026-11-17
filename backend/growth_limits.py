"""Optional bounded owner-scoped admission; uncertain reservations are never expired."""
import asyncio
from contextlib import asynccontextmanager
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from config import load_growth_settings as _load_settings, ConfigurationError


class GrowthError(Exception):
    def __init__(self, exhausted=False):
        self.status = 409 if exhausted else 503
        super().__init__('Account storage limit reached. Delete an existing resource before creating another.'
                         if exhausted else 'Resource accounting is temporarily unavailable.')


def load_growth_settings():
    try:
        return _load_settings()
    except ConfigurationError:
        raise GrowthError() from None


@asynccontextmanager
async def owner_transaction(database, owner):
    if not isinstance(owner, ObjectId):
        raise GrowthError()
    try:
        async with asyncio.timeout(5):
            collection = database.get_collection('resource_usage', write_concern=WriteConcern('majority', wtimeout=5000))
            # Intrinsic unique _id serializes first creation; conflicts fail closed.
            await collection.update_one({'_id': owner}, {'$setOnInsert': {'revision': 0, 'documents': [], 'knowledge_bases': []}}, upsert=True)
            async with database.client.start_session() as session:
                async with await session.start_transaction(read_concern=ReadConcern('snapshot'),
                        write_concern=WriteConcern('majority', wtimeout=5000), max_commit_time_ms=5000):
                    row = await collection.find_one_and_update({'_id': owner}, {'$inc': {'revision': 1}},
                        session=session, return_document=ReturnDocument.AFTER)
                    if row is None:
                        raise GrowthError()
                    yield collection, row, session
    except GrowthError:
        raise
    except Exception:
        raise GrowthError() from None


async def reserve(database, owner, kind, identity):
    settings = load_growth_settings()
    if not settings.enabled:
        return
    if kind not in ('documents', 'knowledge_bases') or not isinstance(identity, ObjectId):
        raise GrowthError()
    limit = getattr(settings, kind)
    async with owner_transaction(database, owner) as (ledger, row, session):
        held = row.get(kind)
        if not isinstance(held, list) or any(not isinstance(i, ObjectId) for i in held):
            raise GrowthError()
        if identity in held:
            raise GrowthError()  # No blind replay of an uncertain creation.
        used = set(held)
        # Existing records count too; enabling the policy never deletes them.
        cursor = database.get_collection(kind).find({'owner_id': owner}, {'_id': 1}, session=session).limit(limit + 1)
        used.update([r['_id'] async for r in cursor])
        if kind == 'documents':
            # Include legacy local upload reservations across all owned KBs.
            cursor = database.get_collection('knowledge_bases').find(
                {'owner_id': owner, '_document_ids.0': {'$exists': True}}, {'_document_ids': 1}, session=session).limit(limit + 1)
            async for base in cursor:
                used.update(base['_document_ids'])
        if len(used) >= limit:
            raise GrowthError(True)
        await ledger.update_one({'_id': owner}, {'$addToSet': {kind: identity}}, session=session)


async def release_confirmed(database, owner, kind, identity):
    if not load_growth_settings().enabled:
        return
    if kind not in ('documents', 'knowledge_bases') or not isinstance(identity, ObjectId):
        raise GrowthError()
    async with owner_transaction(database, owner) as (ledger, _, session):
        await ledger.update_one({'_id': owner}, {'$pull': {kind: identity}}, session=session)


async def retain_history(database, owner, record):
    settings = load_growth_settings()
    if record.get('owner_id') != owner or not isinstance(record.get('_id'), ObjectId):
        raise GrowthError()
    async with owner_transaction(database, owner) as (_, __, session):
        history = database.get_collection('ask_history')
        await history.insert_one(record, session=session)
        # Keep newest entries by creation time and ID, including this insertion.
        cursor = history.find({'owner_id': owner}, {'_id': 1}, session=session).sort(
            [('created_at', -1), ('_id', -1)]).skip(settings.history).limit(501)
        obsolete = [r['_id'] async for r in cursor]
        if len(obsolete) > 500:
            raise GrowthError()  # Explicit operator migration needed for large legacy histories.
        if obsolete:
            await history.delete_many({'owner_id': owner, '_id': {'$in': obsolete}}, session=session)


def check_indexing_limits(document, chunks):
    settings = load_growth_settings()
    if settings.enabled and (not isinstance(document.get('page_count'), int)
            or document['page_count'] > settings.pages or len(chunks) > settings.chunks):
        raise GrowthError(True)
