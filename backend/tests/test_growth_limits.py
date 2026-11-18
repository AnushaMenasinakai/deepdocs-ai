"""Offline transaction double; real MongoDB conflict behavior needs isolated validation."""
import asyncio
import copy
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from bson import ObjectId
from config import GrowthSettings
import growth_limits as growth


def matches(row, query):
    for key, value in query.items():
        if key == '_document_ids.0':
            if not row.get('_document_ids'): return False
        elif isinstance(value, dict) and '$in' in value:
            if row.get(key) not in value['$in']: return False
        elif row.get(key) != value: return False
    return True


class Cursor:
    def __init__(self, rows): self.rows = copy.deepcopy(rows)
    def limit(self, n): self.rows = self.rows[:n]; return self
    def skip(self, n): self.rows = self.rows[n:]; return self
    def sort(self, keys):
        for key, direction in reversed(keys): self.rows.sort(key=lambda row: row[key], reverse=direction < 0)
        return self
    def __aiter__(self):
        async def iterate():
            for row in self.rows: yield row
        return iterate()


class Collection:
    def __init__(self, db, name): self.db, self.name = db, name
    @property
    def rows(self): return self.db.rows.setdefault(self.name, [])
    def find(self, query, *args, **kwargs): return Cursor([r for r in self.rows if matches(r, query)])
    async def update_one(self, query, update, **kwargs):
        row = next((r for r in self.rows if matches(r, query)), None)
        if row is None:
            assert kwargs.get('upsert')
            row = {**query, **copy.deepcopy(update['$setOnInsert'])}; self.rows.append(row)
        for k, v in update.get('$inc', {}).items(): row[k] = row.get(k, 0) + v
        for k, v in update.get('$addToSet', {}).items():
            if v not in row[k]: row[k].append(v)
        for k, v in update.get('$pull', {}).items(): row[k] = [i for i in row[k] if i != v]
    async def find_one_and_update(self, query, update, **kwargs):
        await self.update_one(query, update, **kwargs)
        return copy.deepcopy(next(r for r in self.rows if matches(r, query)))
    async def delete_many(self, query, **kwargs): self.db.rows[self.name] = [r for r in self.rows if not matches(r, query)]
    async def insert_one(self, row, **kwargs):
        if self.db.insert_failure: raise OSError('private')
        self.rows.append(copy.deepcopy(row))


class Session:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def start_transaction(self, **kwargs):
        db = self.db
        class Transaction:
            async def __aenter__(self):
                await db.lock.acquire(); self.before = copy.deepcopy(db.rows)
                await asyncio.sleep(0)
            async def __aexit__(self, typ, *args):
                if typ: db.rows = self.before
                db.lock.release()
                if not typ and db.lost: raise OSError('private acknowledgement')
        return Transaction()


class Database:
    def __init__(self):
        self.rows = {}; self.lock = asyncio.Lock(); self.lost = False; self.insert_failure = False
        self.client = SimpleNamespace(start_session=lambda: Session(self))
    def get_collection(self, name, **kwargs): return Collection(self, name)


class GrowthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = Database(); self.owner = ObjectId()
        self.settings = GrowthSettings(True, 2, 2, 2, 3, 2)
        p = patch.object(growth, '_load_settings', return_value=self.settings)
        p.start(); self.addCleanup(p.stop)

    async def test_concurrent_resource_admission_and_restart(self):
        for kind in ('documents', 'knowledge_bases'):
            async def create():
                try: await growth.reserve(self.db, self.owner, kind, ObjectId()); return True
                except growth.GrowthError: return False
            self.assertEqual(sum(await asyncio.gather(*(create() for _ in range(8)))), 2)
            with self.assertRaises(growth.GrowthError): await growth.reserve(self.db, self.owner, kind, ObjectId())

    async def test_existing_resources_and_upload_reservations_count(self):
        self.db.rows['documents'] = [{'_id': ObjectId(), 'owner_id': self.owner}]
        self.db.rows['knowledge_bases'] = [{'_id': ObjectId(), 'owner_id': self.owner, '_document_ids': [ObjectId()]}]
        with self.assertRaises(growth.GrowthError) as error:
            await growth.reserve(self.db, self.owner, 'documents', ObjectId())
        self.assertEqual(error.exception.status, 409)
        await growth.reserve(self.db, ObjectId(), 'documents', ObjectId())

    async def test_lost_commit_retains_and_confirmed_release_is_scoped(self):
        identity = ObjectId(); self.db.lost = True
        with self.assertRaises(growth.GrowthError): await growth.reserve(self.db, self.owner, 'documents', identity)
        self.db.lost = False
        with self.assertRaises(growth.GrowthError): await growth.reserve(self.db, self.owner, 'documents', identity)
        await growth.release_confirmed(self.db, ObjectId(), 'documents', identity)
        self.assertIn(identity, self.db.rows['resource_usage'][0]['documents'])
        await growth.release_confirmed(self.db, self.owner, 'documents', identity)
        await growth.release_confirmed(self.db, self.owner, 'documents', identity)
        self.assertEqual(self.db.rows['resource_usage'][0]['documents'], [])

    async def test_history_concurrency_ownership_and_rollback(self):
        other = ObjectId(); now = datetime.now(timezone.utc)
        self.db.rows['ask_history'] = [{'_id': ObjectId(), 'owner_id': other, 'created_at': now}]
        async def save(i):
            await growth.retain_history(self.db, self.owner, {'_id': ObjectId(), 'owner_id': self.owner, 'created_at': now + timedelta(seconds=i)})
        await asyncio.gather(*(save(i) for i in range(8)))
        rows = self.db.rows['ask_history']
        self.assertEqual(sum(r['owner_id'] == self.owner for r in rows), 2)
        self.assertEqual(sum(r['owner_id'] == other for r in rows), 1)
        before = copy.deepcopy(rows); self.db.insert_failure = True
        with self.assertRaises(growth.GrowthError): await save(9)
        self.assertEqual(self.db.rows['ask_history'], before)

    async def test_legacy_history_cleanup_bound(self):
        self.db.rows['ask_history'] = [{'_id': ObjectId(), 'owner_id': self.owner, 'created_at': datetime.now(timezone.utc)} for _ in range(503)]
        with self.assertRaises(growth.GrowthError):
            await growth.retain_history(self.db, self.owner, {'_id': ObjectId(), 'owner_id': self.owner, 'created_at': datetime.now(timezone.utc)})
        self.assertEqual(len(self.db.rows['ask_history']), 503)

    async def test_disabled_no_database_access_and_indexing_limits(self):
        with patch.object(growth, '_load_settings', return_value=GrowthSettings()):
            await growth.reserve(None, None, 'documents', None)
            await growth.release_confirmed(None, None, 'documents', None)
        growth.check_indexing_limits({'page_count': 2}, [1, 2, 3])
        for doc, chunks in (({'page_count': 3}, []), ({'page_count': 1}, [1]*4), ({}, [])):
            with self.assertRaises(growth.GrowthError): growth.check_indexing_limits(doc, chunks)

    async def test_config_parser_caps_and_gridfs_guard(self):
        import os
        from config import load_growth_settings, load_processing_settings, ConfigurationError
        with patch.dict(os.environ, {}, clear=True), patch('config.dotenv_values', return_value={}):
            self.assertFalse(load_growth_settings().enabled)
        with patch.dict(os.environ, {}, clear=True), patch('config.dotenv_values', return_value={'GROWTH_LIMITS_ENABLED': 'true'}):
            p = load_processing_settings(); self.assertEqual((p.max_pages, p.max_chunks), (100, 500))
        with patch.dict(os.environ, {}, clear=True), patch('config.dotenv_values', return_value={
                'GROWTH_LIMITS_ENABLED': 'true', 'GRIDFS_UPLOAD_ENABLED': 'true'}):
            self.assertTrue(load_growth_settings().enabled)
        for values in ({'GROWTH_LIMITS_ENABLED': 'bad'}, {'GROWTH_MAX_DOCUMENTS': '0'}):
            with patch.dict(os.environ, {}, clear=True), patch('config.dotenv_values', return_value=values):
                with self.assertRaises(ConfigurationError): load_growth_settings()

    async def test_cancellation_rolls_back_owned_transaction(self):
        with self.assertRaises(asyncio.CancelledError):
            async with growth.owner_transaction(self.db, self.owner) as (ledger, row, session):
                await ledger.update_one({'_id': self.owner}, {'$addToSet': {'documents': ObjectId()}}, session=session)
                raise asyncio.CancelledError()
        self.assertEqual(self.db.rows['resource_usage'][0]['documents'], [])
        self.assertFalse(self.db.lock.locked())

    async def test_failed_release_does_not_silently_free_capacity(self):
        identity = ObjectId()
        await growth.reserve(self.db, self.owner, 'documents', identity)
        with patch.object(self.db.client, 'start_session', side_effect=OSError('private')):
            with self.assertRaises(growth.GrowthError) as error:
                await growth.release_confirmed(self.db, self.owner, 'documents', identity)
        self.assertNotIn('private', str(error.exception))
        self.assertIn(identity, self.db.rows['resource_usage'][0]['documents'])

    async def test_kb_admission_before_publication(self):
        from knowledge_bases import create_knowledge_base
        with patch('knowledge_bases.reserve', side_effect=growth.GrowthError(True)) as reserve:
            with self.assertRaises(growth.GrowthError):
                await create_knowledge_base(self.db, self.owner, SimpleNamespace(name='Synthetic', description=None))
            reserve.assert_awaited_once()
            self.assertNotIn('knowledge_bases', self.db.rows)

    async def test_history_wrong_owner_rejected(self):
        with self.assertRaises(growth.GrowthError):
            await growth.retain_history(self.db, self.owner, {'_id': ObjectId(), 'owner_id': ObjectId()})
        self.assertFalse(self.db.rows)
