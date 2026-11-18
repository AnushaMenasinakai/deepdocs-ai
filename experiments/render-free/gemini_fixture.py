"""Experiment-only service doubles, reusing repository transaction/storage fixtures."""
import asyncio
import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace
from pymongo import ReturnDocument
from test_gridfs_growth import Database as SyncDatabase
from test_documents import Collection as Documents
from test_growth_limits import Cursor
from test_usage_budgets import Collection as BudgetCollection
from test_gridfs_storage import Bucket
from storage_schema import INDEXES


class Collection(Documents):
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.queries = []
        self.failure = None
    @property
    def documents(self): return self.db.sync.rows.setdefault(self.name, {})
    def matching(self, query):
        def match(row):
            for key, value in query.items():
                actual = row
                for part in key.split('.'):
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if key == '_document_ids.0': actual = bool(row.get('_document_ids')) or None
                if isinstance(value, dict):
                    if '$in' in value and actual not in value['$in']: return False
                    if '$exists' in value and (actual is not None) != value['$exists']: return False
                    if '$ne' in value and actual == value['$ne']: return False
                    if not set(value) <= {'$in', '$exists', '$ne'}: raise AssertionError('Unsupported fixture predicate')
                elif actual != value: return False
            return True
        return [r for r in self.documents.values() if match(r)]
    def find(self, query, *args, **kwargs): return Cursor(self.matching(query))
    async def find_one(self, query, *args, **kwargs):
        rows = self.matching(query)
        return copy.deepcopy(rows[0]) if rows else None
    async def insert_one(self, document, **kwargs):
        await super().insert_one(document)
        return SimpleNamespace(inserted_id=document['_id'])
    async def update_one(self, query, update, **kwargs):
        if not self.matching(query) and kwargs.get('upsert'):
            self.documents[query['_id']] = {**query, **copy.deepcopy(update.get('$setOnInsert', {}))}
        return await super().update_one(query, update)
    async def find_one_and_update(self, query, update, **kwargs):
        old = await self.find_one(query)
        await self.update_one(query, update, **kwargs)
        return await self.find_one(query) if kwargs.get('return_document') == ReturnDocument.AFTER else old
    async def delete_many(self, query, **kwargs): return await super().delete_many(query)
    async def create_index(self, keys, **options):
        if isinstance(keys, str): keys = [(keys, 1)]
        self.db.indexes.setdefault(self.name, {'_id_': {'key': [('_id', 1)]}})[options['name']] = {'key': keys, **options}
    async def index_information(self): return self.db.indexes.get(self.name, {'_id_': {'key': [('_id', 1)]}})


class Session:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.abort_transaction()
    async def start_transaction(self, **kwargs):
        self.before = None
        db = self.db
        @asynccontextmanager
        async def transaction():
            async with db.lock:
                self.before = copy.deepcopy(db.sync.rows)
                try: yield
                except BaseException:
                    db.sync.rows = self.before
                    raise
                finally: self.before = None
        return transaction()
    async def abort_transaction(self): pass  # Readiness only uses read-and-abort.


class Database:
    def __init__(self):
        self.sync = SyncDatabase()
        self.bucket = Bucket()
        self.budget = BudgetCollection()
        self.lock = asyncio.Lock()
        self.indexes = {}
        for index in INDEXES:
            self.indexes.setdefault(index.collection, {})[index.name] = {'key': list(index.keys), 'unique': index.unique}
        self.client = SimpleNamespace(start_session=lambda: Session(self))
    def get_collection(self, name, **kwargs):
        if name == 'gemini_daily_usage':
            collection = Collection(self, name)
            self.budget.create_index = collection.create_index
            self.budget.index_information = collection.index_information
            return self.budget
        return Collection(self, name)
    async def create_collection(self, name): self.sync.rows.setdefault(name, {})
    async def command(self, name, **kwargs):
        assert name in ('ping', 'hello')
        return {'ok': 1, 'setName': 'simulated', 'isWritablePrimary': True,
                'logicalSessionTimeoutMinutes': 30, 'maxWireVersion': 25}
