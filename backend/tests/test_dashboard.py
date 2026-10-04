"""Offline dashboard routes and aggregation contracts; no remote services."""
import copy
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from test_knowledge_bases import request, app, get_current_user, get_database

MISSING = object()
def field(value, path):
    for key in path.split('.'):
        if isinstance(value, list):
            return [field(item, key) for item in value]
        value = value.get(key, MISSING) if isinstance(value, dict) else MISSING
    return value

def evaluate(expr, row, variables=None):
    variables = variables or {}
    if isinstance(expr, str) and expr.startswith('$$'): return variables[expr[2:]]
    if isinstance(expr, str) and expr.startswith('$'): return field(row, expr[1:])
    if isinstance(expr, list): return [evaluate(x, row, variables) for x in expr]
    if not isinstance(expr, dict): return expr
    op, value = next(iter(expr.items()))
    if op == '$type':
        v = evaluate(value, row, variables)
        return 'missing' if v is MISSING else {ObjectId:'objectId', str:'string', int:'int', bool:'bool', datetime:'date'}.get(type(v), 'null')
    args = evaluate(value, row, variables)
    if op == '$eq': return args[0] == args[1]
    if op == '$ne': return args[0] != args[1]
    if op == '$gt': return False if args[0] is MISSING else args[0] > args[1]
    if op == '$in': return args[0] in args[1]
    if op == '$and': return all(args)
    if op == '$or': return any(args)
    if op == '$cond': return args[1] if args[0] else args[2]
    if op == '$arrayElemAt': return args[0][args[1]] if len(args[0]) > args[1] else None
    if op == '$ifNull': return args[1] if args[0] is None else args[0]
    raise AssertionError(op)

def pipeline(rows, stages, db, variables=None):
    rows = copy.deepcopy(rows)
    for stage in stages:
        op, value = next(iter(stage.items()))
        if op == '$match': rows = [r for r in rows if all(evaluate(v, r, variables) if k == '$expr' else r.get(k) == v for k,v in value.items())]
        elif op == '$sort':
            for k,d in reversed(list(value.items())): rows.sort(key=lambda r:r[k], reverse=d == -1)
        elif op == '$limit': rows = rows[:value]
        elif op == '$count': rows = [{value:len(rows)}] if rows else []
        elif op == '$group': rows = [{k:None if k == '_id' else sum(evaluate(v['$sum'],r) for r in rows) for k,v in value.items()}] if rows else []
        elif op == '$lookup':
            for row in rows: row[value['as']] = pipeline(db[value['from']].rows, value['pipeline'], db, {k:evaluate(v,row) for k,v in value['let'].items()})
        elif op == '$project': rows = [{'_id':r['_id'], **{k:r[k] if v == 1 else evaluate(v,r) for k,v in value.items()}} for r in rows]
        else: raise AssertionError(op)
    return rows

class Cursor:
    def __init__(self, rows): self.rows=rows
    async def to_list(self, length): return self.rows[:length]
    def __aiter__(self): self.iterator=iter(self.rows); return self
    async def __anext__(self):
        try: return next(self.iterator)
        except StopIteration: raise StopAsyncIteration

class Collection:
    def __init__(self, db): self.db=db; self.rows=[]; self.calls=[]; self.failure=False
    async def count_documents(self, scope):
        self.calls.append(scope)
        if self.failure: raise ConnectionFailure('PRIVATE DATABASE DETAILS')
        return sum(all(r.get(k)==v for k,v in scope.items()) for r in self.rows)
    async def aggregate(self, stages, **kwargs):
        self.calls.append(stages)
        assert kwargs['maxTimeMS'] == 5000
        if self.failure: raise ConnectionFailure('PRIVATE DATABASE DETAILS')
        return Cursor(pipeline(self.rows, stages, self.db))

class DashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=ObjectId(); self.other=ObjectId(); self.now=datetime.now(timezone.utc)
        self.db={}
        for key in ('knowledge_bases','documents','ask_history'): self.db[key]=Collection(self.db)
        app.dependency_overrides[get_current_user]=lambda: SimpleNamespace(id=str(self.owner))
        app.dependency_overrides[get_database]=lambda: SimpleNamespace(get_collection=self.db.__getitem__)
    async def asyncTearDown(self): app.dependency_overrides.clear()
    def kb(self, owner=None):
        row={'_id':ObjectId(),'owner_id':owner or self.owner,'name':'Safe <name>', 'updated_at':self.now}
        self.db['knowledge_bases'].rows.append(row); return row
    def document(self, base, owner=None):
        generation=ObjectId()
        row={'_id':ObjectId(),'owner_id':owner or self.owner,'knowledge_base_id':base['_id'],'status':'processed','chunk_generation':generation,'chunk_count':2,
             'embedding':{'status':'generated','chunk_generation':generation,'chunk_count':2,'model':'model','dimension':384},
             'vector_index':{'status':'indexed','chunk_generation':generation,'chunk_count':2,'embedding_model':'model','embedding_dimension':384,'collection_name':'collection','target':'hash','indexed_at':self.now}}
        self.db['documents'].rows.append(row); return row
    async def get(self): return await request('GET','/api/dashboard/summary')
    async def test_authentication_required(self):
        del app.dependency_overrides[get_current_user]
        self.assertEqual((await self.get())[0],401)
        self.assertTrue(all(not collection.calls for collection in self.db.values()))
    async def test_zero_state(self):
        status,data,_=await self.get(); self.assertEqual(status,200)
        self.assertEqual(data.pop('recent_knowledge_bases'),[]); self.assertTrue(all(v==0 for v in data.values()))
    async def test_owner_counts_recent_order_limit_safe_fields_and_fixed_queries(self):
        bases=[self.kb() for _ in range(7)]; foreign=self.kb(self.other)
        self.document(bases[-1]); self.document(bases[-1]); self.document(foreign,self.other)
        self.document(bases[-1], self.other) # even a foreign owner's mismatched reference must not count
        self.db['ask_history'].rows=[{'owner_id':self.owner},{'owner_id':self.other}]
        status,data,_=await self.get(); self.assertEqual(status,200)
        self.assertEqual([data[k] for k in ('knowledge_base_count','document_count','indexed_document_count','ask_history_count')],[7,2,2,1])
        recent=data['recent_knowledge_bases']; self.assertEqual([r['id'] for r in recent],[str(b['_id']) for b in bases[::-1][:5]])
        self.assertEqual(recent[0]['document_count'],2)
        self.assertEqual(set(recent[0]),{'id','name','updated_at','document_count'})
        self.assertEqual(sum(len(c.calls) for c in self.db.values()),4)
    async def test_indexing_requires_matching_active_successful_metadata(self):
        base=self.kb(); self.document(base)
        variants=[('vector_index','status','stale'),('vector_index','status','failed'),('vector_index','chunk_generation',ObjectId()),('vector_index','chunk_count',1),('vector_index','embedding_dimension',0),('vector_index','indexed_at',None),('vector_index','target',''),('embedding','status','generated_without_vectors'),('embedding','dimension',768),('embedding','model','other')]
        for section,key,value in variants: self.document(base)[section][key]=value
        self.document(base).pop('vector_index')
        self.document(base)['_operation']={'kind':'indexing'}
        self.document(base)['status']='processing'
        self.document(base)['status']='failed'
        _,data,_=await self.get()
        self.assertEqual(data['indexed_document_count'],1)
        self.assertEqual(data['processing_document_count'],1)
        self.assertEqual(data['failed_document_count'],2)
    async def test_secondary_failures_count_once(self):
        row=self.document(self.kb()); row['status']='failed'; row['embedding']['status']='failed'; row['vector_index']['status']='failed'
        self.assertEqual((await self.get())[1]['failed_document_count'],1)
    async def test_database_errors_sanitized_at_every_query(self):
        for key in self.db:
            with self.subTest(collection=key):
                self.db[key].failure=True
                status,data,_=await self.get(); self.assertEqual(status,503); self.assertNotIn('PRIVATE',str(data))
                self.db[key].failure=False

    async def test_recent_dates_take_priority_over_objectid_tiebreaker(self):
        newer=self.kb(); older=self.kb()
        newer['updated_at']=self.now+timedelta(days=1)
        self.assertEqual([r['id'] for r in (await self.get())[1]['recent_knowledge_bases']], [str(newer['_id']),str(older['_id'])])

    async def test_client_owner_cannot_change_scope_and_foreign_secondary_counts_excluded(self):
        own=self.kb(); foreign=self.kb(self.other)
        self.document(own)
        self.document(foreign,self.other)['status']='processing'
        self.document(foreign,self.other)['status']='failed'
        status,data,_=await request('GET','/api/dashboard/summary?owner_id='+str(self.other))
        self.assertEqual(status,200)
        self.assertEqual(data['knowledge_base_count'],1)
        self.assertEqual(data['document_count'],1)
        self.assertEqual(data['processing_document_count'],0)
        self.assertEqual(data['failed_document_count'],0)
