"""Offline route/query tests with real ownership and public response mapping."""
import copy
import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlencode
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from test_knowledge_bases import request, app, get_current_user, get_database, MemoryCursor
from test_dashboard import evaluate
from dashboard import indexed_expression
from management import failed_expression


def matches(row, scope):
    for key, value in scope.items():
        if key == "$expr":
            if not evaluate(value,row):return False
        elif isinstance(value,dict) and "$regex" in value:
            if not re.search(value["$regex"],row.get(key,""),re.I):return False
        elif row.get(key)!=value:return False
    return True


def aggregate(rows, stages):
    rows=copy.deepcopy(rows)
    for stage in stages:
        op,value=next(iter(stage.items()))
        if op=="$match":rows=[r for r in rows if matches(r,value)]
        elif op=="$sort":
            for key,direction in reversed(list(value.items())):rows.sort(key=lambda r:r[key],reverse=direction==-1)
        elif op=="$skip":rows=rows[value:]
        elif op=="$limit":rows=rows[:value]
        elif op=="$count":rows=[{value:len(rows)}] if rows else []
        elif op=="$addFields":
            for row in rows:row.update({key:evaluate(expr,row) for key,expr in value.items()})
        elif op=="$facet":rows=[{key:aggregate(rows,pipeline) for key,pipeline in value.items()}]
        else:raise AssertionError(op)
    return rows


class Collection:
    def __init__(self):self.rows=[];self.calls=[];self.failure=False
    async def aggregate(self,stages,**kwargs):
        self.calls.append((stages,kwargs))
        if self.failure:raise ConnectionFailure("PRIVATE connection details")
        rows=aggregate(self.rows,stages)
        async def to_list(length):return rows[:length]
        return SimpleNamespace(to_list=to_list)
    async def find_one(self,scope):return next((r for r in self.rows if matches(r,scope)),None)
    def find(self,scope):return MemoryCursor([r for r in self.rows if matches(r,scope)])


class ManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner=ObjectId();self.other=ObjectId();self.now=datetime.now(timezone.utc)
        self.db={key:Collection() for key in ("knowledge_bases","documents")}
        app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=str(self.owner))
        app.dependency_overrides[get_database]=lambda:SimpleNamespace(get_collection=self.db.__getitem__)
        self.base=self.kb("Research")
    async def asyncTearDown(self):app.dependency_overrides.clear()
    def kb(self,name,owner=None):
        row=dict(_id=ObjectId(),owner_id=owner or self.owner,name=name,description=None,created_at=self.now,updated_at=self.now)
        self.db["knowledge_bases"].rows.append(row);return row
    def doc(self,name="notes.pdf",status="uploaded",owner=None,base=None,indexed=False):
        row=dict(_id=ObjectId(),owner_id=owner or self.owner,knowledge_base_id=(base or self.base)["_id"],original_filename=name,status=status,
                 created_at=self.now,updated_at=self.now,content_type="application/pdf",file_size=20,storage_path="PRIVATE",stored_filename="PRIVATE")
        if indexed:
            generation=ObjectId();row.update(status="processed",chunk_generation=generation,chunk_count=2,
                embedding=dict(status="generated",chunk_generation=generation,model="model",dimension=384,chunk_count=2),
                vector_index=dict(status="indexed",chunk_generation=generation,embedding_model="model",embedding_dimension=384,chunk_count=2,indexed_at=self.now,collection_name="collection",target="target"))
        self.db["documents"].rows.append(row);return row
    async def get(self,documents=False,base=None,**params):
        path=f"/api/knowledge-bases/{(base or self.base)['_id']}/documents/browse" if documents else "/api/knowledge-bases/browse"
        return await request("GET",path+"?"+urlencode(params))

    async def test_authentication_required_for_both(self):
        del app.dependency_overrides[get_current_user]
        for docs in (False,True):self.assertEqual((await self.get(docs))[0],401)
        self.assertTrue(all(not c.calls for c in self.db.values()))

    async def test_kb_defaults_metadata_and_legacy_array(self):
        for i in range(24):self.kb(f"Base {i}")
        status,data,_=await self.get()
        self.assertEqual(status,200);self.assertEqual((data["page"],data["limit"],data["total"],data["total_pages"]),(1,20,25,2))
        self.assertEqual(len(data["items"]),20)
        self.assertEqual(data["items"][0]["id"],str(self.db["knowledge_bases"].rows[-1]["_id"]))
        legacy=await request("GET","/api/knowledge-bases")
        self.assertEqual(len(legacy[1]),25)
        self.assertIsInstance(legacy[1],list)

    async def test_owner_and_kb_isolation(self):
        foreign=self.kb("Foreign",self.other);second=self.kb("Other owned")
        self.doc();self.doc(owner=self.other);self.doc(base=foreign,owner=self.other);self.doc(base=second)
        self.assertEqual((await self.get())[1]["total"],2)
        self.assertEqual((await self.get(True))[1]["total"],1)
        self.assertEqual((await self.get(True,foreign))[0],404)
        self.assertEqual((await self.get(True,{"_id":ObjectId()}))[0],404)
        self.assertEqual(len(self.db["documents"].calls),1)

    async def test_search_case_trim_empty_and_literal_metacharacters(self):
        for docs in (False,True):
            self.doc("AUTH.guide.pdf") if docs else self.kb("AUTH.guide")
            for search in ("auth", "  aUtH  "):
                with self.subTest(docs=docs,search=search):self.assertEqual((await self.get(docs,search=search))[1]["total"],1)
            for char in ".*+?[]()\\^$":
                name="literal"+char+"text"
                self.doc(name) if docs else self.kb(name)
                data=(await self.get(docs,search=char))[1]
                self.assertTrue(all(char in row["filename" if docs else "name"] for row in data["items"]))
                scope=self.db["documents" if docs else "knowledge_bases"].calls[-1][0][0]["$match"]
                self.assertEqual(scope["original_filename" if docs else "name"]["$regex"],re.escape(char))
            self.assertEqual((await self.get(docs,search=" "))[1]["total"],(await self.get(docs))[1]["total"])
            self.assertEqual((await self.get(docs,search="missing"))[1]["total_pages"],0)

    async def test_pagination_boundaries_max_and_stable_ties(self):
        for i in range(22):self.kb("Same");self.doc("same.pdf")
        for docs in (False,True):
            first=(await self.get(docs,limit=10))[1];second=(await self.get(docs,page=2,limit=10))[1]
            self.assertEqual(len(second["items"]),10)
            self.assertFalse({i["id"] for i in first["items"]}&{i["id"] for i in second["items"]})
            self.assertEqual(first,(await self.get(docs,limit=10))[1])
            self.assertEqual((await self.get(docs,page=100))[1]["items"],[])
            self.assertEqual((await self.get(docs,limit=100))[0],200)

    async def test_allowlisted_sorts_and_secondary_id(self):
        self.kb("Zebra");self.kb("Alpha");self.doc("Z.pdf");self.doc("A.pdf")
        for docs in (False,True):
            name="filename" if docs else "name"
            for field in (name,"created_at","updated_at"):
                for order in ("asc","desc"):
                    data=(await self.get(docs,sort=field,order=order))[1]
                    key=field
                    self.assertEqual([r[key] for r in data["items"]],sorted([r[key] for r in data["items"]],reverse=order=="desc"))
                    stages=self.db["documents" if docs else "knowledge_bases"].calls[-1][0]
                    self.assertEqual(stages[1]["$sort"]["_id"],1 if order=="asc" else -1)

    async def test_invalid_query_parameters_sanitized(self):
        for docs in (False,True):
            for params in (dict(page=0),dict(page=-1),dict(page="1.0"),dict(page="wat"),dict(page=1000001),dict(limit=0),dict(limit=101),dict(limit="bad"),dict(sort="owner_id"),dict(order="other"),dict(search="x"*201),dict(owner_id=str(self.other))):
                with self.subTest(docs=docs,params=params):
                    status,data,_=await self.get(docs,**params);self.assertEqual(status,422)
                    self.assertTrue(all("input" not in detail for detail in data["detail"]))
        self.assertEqual((await self.get(True,status="embedded"))[0],422)
        self.assertEqual((await request("GET","/api/knowledge-bases/not-an-id/documents/browse"))[0],422)

    async def test_status_filters_share_authoritative_indexing_and_failures(self):
        self.doc();self.doc(status="processing");self.doc(status="failed")
        good=self.doc(indexed=True)
        for mutate in (lambda r:r["embedding"].update(status="failed"),lambda r:r["vector_index"].update(status="failed"),lambda r:r["vector_index"].update(chunk_generation=ObjectId()),lambda r:r.update(_operation={}),lambda r:r["vector_index"].update(chunk_count=99),lambda r:r["vector_index"].pop("indexed_at"),lambda r:r["embedding"].update(status="not_generated")):
            row=self.doc(indexed=True);mutate(row)
        self.doc(status="processed")["embedding"]={"status":"generated"}
        data=(await self.get(True,status="indexed"))[1]
        self.assertEqual([r["id"] for r in data["items"]],[str(good["_id"])])
        self.assertTrue(data["items"][0]["indexed"])
        self.assertEqual(self.db["documents"].calls[-1][0][0]["$match"]["$expr"],indexed_expression())
        self.assertEqual((await self.get(True,status="processing"))[1]["total"],1)
        failed=(await self.get(True,status="failed"))[1];self.assertEqual(failed["total"],3)
        self.assertTrue(all(r["failed"] for r in failed["items"]))
        self.assertEqual((await self.get(True,status="all"))[1]["total"],12)

    async def test_database_work_bounded_single_facet_no_n_plus_one(self):
        self.doc()
        for docs in (False,True):
            await self.get(docs,page=3,limit=7)
            calls=self.db["documents" if docs else "knowledge_bases"].calls
            self.assertEqual(len(calls),1)
            stages,kwargs=calls[0];self.assertEqual(kwargs,{"maxTimeMS":5000})
            self.assertEqual(stages[0]["$match"]["owner_id"],self.owner)
            self.assertEqual(stages[-1]["$facet"]["items"][:2],[{"$skip":14},{"$limit":7}])
            self.assertEqual(stages[-1]["$facet"]["count"],[{"$count":"total"}])

    async def test_document_legacy_public_fields_and_page_after_removal_addition(self):
        first=self.doc();last=self.doc()
        self.assertIsInstance((await request("GET",f"/api/knowledge-bases/{self.base['_id']}/documents"))[1],list)
        self.assertEqual((await self.get(True,page=2,limit=1))[1]["total_pages"],2)
        self.db["documents"].rows.remove(last)
        data=(await self.get(True,page=2,limit=1))[1]
        self.assertEqual(data["items"],[]);self.assertEqual(data["total_pages"],1)
        self.doc("fresh.pdf")
        data=(await self.get(True,limit=1))[1]
        self.assertEqual(data["items"][0]["filename"],"fresh.pdf")
        for secret in ("owner_id","storage_path","stored_filename","vector_index","PRIVATE"):
            self.assertNotIn(secret,str(data))

    async def test_service_errors_are_sanitized(self):
        for docs in (False,True):
            self.db["documents" if docs else "knowledge_bases"].failure=True
            status,data,_=await self.get(docs)
            self.assertEqual(status,503);self.assertNotIn("PRIVATE",str(data))
