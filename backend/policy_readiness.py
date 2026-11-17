"""Bounded read-only MongoDB safeguard verification; no provider calls or scans."""
import asyncio
from pymongo.errors import CollectionInvalid
from pymongo.read_concern import ReadConcern
import config
from storage_schema import INDEXES, Index, _check


def runtime_ready(state):
    try:
        return not config.load_gridfs_upload_enabled() or all(
            getattr(state, name, None) is not None for name in
            ('gridfs_uploads', 'gridfs_processing', 'gridfs_deletion'))
    except Exception:
        return False


async def setup_growth_identity(database):
    if not config.load_growth_settings().enabled:
        return
    async with asyncio.timeout(5):
        try:
            await database.create_collection('resource_usage')
        except CollectionInvalid:
            pass  # Verification, not existence, decides readiness.


async def verify(database):
    budget = config.load_usage_budget_settings().enabled
    growth = config.load_growth_settings().enabled
    gridfs = config.load_gridfs_upload_enabled()
    if not (budget or growth or gridfs):
        return True
    async with asyncio.timeout(2):
        manifest = []
        if budget:
            indexes = await database.get_collection('gemini_daily_usage').index_information()
            expiry = indexes.get('gemini_usage_expiry', {})
            if (expiry.get('key') != [('expires_at', 1)] or expiry.get('expireAfterSeconds') != 0
                    or expiry.get('unique', False) is not False
                    or any(k in expiry for k in ('partialFilterExpression', 'collation'))
                    or expiry.get('sparse', False) is not False or expiry.get('hidden', False) is not False):
                return False
            _check(Index('gemini_daily_usage', '_id_', (('_id', 1),), True),
                   {**indexes.get('_id_', {}), 'key': dict(indexes.get('_id_', {}).get('key', []))})
        if growth:
            manifest += [Index('resource_usage', '_id_', (('_id', 1),), True),
                Index('ask_history', 'ask_history_owner_created', (('owner_id', 1), ('created_at', -1), ('_id', -1))),
                Index('documents', 'documents_owner_created', (('owner_id', 1), ('created_at', -1), ('_id', -1))),
                Index('knowledge_bases', 'knowledge_bases_owner_updated', (('owner_id', 1), ('updated_at', -1), ('_id', -1)))]
        if gridfs:
            manifest += list(INDEXES)
        cached = {}
        for index in manifest:
            if index.collection not in cached:
                cached[index.collection] = await database.get_collection(index.collection).index_information()
            value = cached[index.collection].get(index.name, {})
            _check(index, {**value, 'key': dict(value.get('key', []))})
        if growth or gridfs:
            hello = await database.command('hello')
            if (not hello.get('setName') or hello.get('isWritablePrimary') is not True
                    or type(hello.get('logicalSessionTimeoutMinutes')) is not int
                    or hello['logicalSessionTimeoutMinutes'] <= 0 or hello.get('maxWireVersion', 0) < 7):
                return False
            # Actual bounded snapshot transaction read, always aborted, never a write probe.
            async with database.client.start_session() as session:
                await session.start_transaction(read_concern=ReadConcern('snapshot'))
                try:
                    await database.get_collection('resource_usage' if growth else 'storage_quota_ledger').find_one(
                        {'_id': '__readiness_missing__'}, {'_id': 1}, session=session, max_time_ms=1000)
                finally:
                    await session.abort_transaction()
        return True
