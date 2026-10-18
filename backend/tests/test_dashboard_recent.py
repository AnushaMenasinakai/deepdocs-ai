"""Offline routes, real aggregation expressions and bounded query contracts."""
import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_dashboard import evaluate, field, MISSING, pipeline as existing_pipeline
from test_knowledge_bases import app, request, get_current_user, get_database
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from documents import ensure_document_indexes

PATH = "/api/dashboard/recent-documents"


def pipeline(rows, stages, db, variables=None):
    rows = copy.deepcopy(rows)
    for stage in stages:
        op, value = next(iter(stage.items()))
        if op == "$lookup":
            for row in rows:
                scope = {k: evaluate(v, row) for k, v in value["let"].items()}
                row[value["as"]] = pipeline(db[value["from"]].rows, value["pipeline"], db, scope)
        elif op == "$unwind":
            rows = [{**row, value[1:]: item} for row in rows for item in row.get(value[1:], [])]
        elif op == "$project":
            projected = []
            for row in rows:
                result = {"_id": row["_id"]}
                for key, expression in value.items():
                    item = field(row, key) if expression == 1 else evaluate(expression, row, variables)
                    if item is not MISSING:
                        result[key] = item
                projected.append(result)
            rows = projected
        else:
            rows = existing_pipeline(rows, [stage], db, variables)
    return rows


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.lengths = []

    async def to_list(self, length):
        self.lengths.append(length)
        return self.rows[:length]


class Collection:
    # Deliberately has no mutation, find, or per-KB query methods.
    def __init__(self, db):
        self.db, self.rows, self.calls = db, [], []
        self.failure = None

    async def aggregate(self, stages, **options):
        self.calls.append((stages, options))
        if self.failure:
            raise self.failure
        self.cursor = Cursor(pipeline(self.rows, stages, self.db))
        return self.cursor


class RecentDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner, self.other = ObjectId(), ObjectId()
        self.now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
        self.db = {}
        for name in ("documents", "knowledge_bases"):
            self.db[name] = Collection(self.db)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=str(self.owner))
        app.dependency_overrides[get_database] = lambda: SimpleNamespace(get_collection=self.db.__getitem__)

    async def asyncTearDown(self):
        app.dependency_overrides.clear()

    def kb(self, owner=None):
        row = {"_id": ObjectId(), "owner_id": owner or self.owner, "name": "Research",
               "updated_at": self.now, "private": "PRIVATE"}
        self.db["knowledge_bases"].rows.append(row)
        return row

    def document(self, base, owner=None, days=0):
        row = {"_id": ObjectId(), "owner_id": owner or self.owner, "knowledge_base_id": base["_id"],
               "original_filename": "Reference.pdf", "created_at": self.now + timedelta(days=days),
               "status": "uploaded", "storage_path": "PRIVATE", "content": "PRIVATE"}
        self.db["documents"].rows.append(row)
        return row

    async def get(self, suffix=""):
        return await request("GET", PATH + suffix)

    async def test_authentication_required_without_query(self):
        del app.dependency_overrides[get_current_user]
        self.assertEqual((await self.get())[0], 401)
        self.assertFalse(self.db["documents"].calls)

    async def test_empty_workspace(self):
        status, data, _ = await self.get()
        self.assertEqual((status, data), (200, {"documents": []}))

    async def test_one_to_three_exact_public_shape_and_utc(self):
        base = self.kb()
        for count in range(1, 4):
            row = self.document(base)
            status, data, _ = await self.get()
            self.assertEqual(status, 200)
            self.assertEqual(len(data["documents"]), count)
            item = data["documents"][0]
            self.assertEqual(item, {"id": str(row["_id"]), "knowledge_base_id": str(base["_id"]),
                                   "knowledge_base_name": "Research", "filename": "Reference.pdf",
                                   "created_at": "2026-10-18T12:00:00Z", "status": "uploaded",
                                   "indexed": False, "failed": False})
            self.assertNotIn("PRIVATE", str(data))

    async def test_global_dates_before_id_and_maximum_three(self):
        rows = [self.document(self.kb(), days=days) for days in [9, 1, 8, 2, 7, 3]]
        expected = sorted(rows, key=lambda r: (r["created_at"], r["_id"]), reverse=True)[:3]
        self.assertEqual([r["id"] for r in (await self.get())[1]["documents"]],
                         [str(r["_id"]) for r in expected])

    async def test_equal_timestamps_use_descending_id(self):
        rows = [self.document(self.kb()) for _ in range(6)]
        self.db["documents"].rows.reverse()
        self.assertEqual([r["id"] for r in (await self.get())[1]["documents"]],
                         [str(r["_id"]) for r in rows[::-1][:3]])

    async def test_both_document_and_base_ownership_required(self):
        own, foreign = self.kb(), self.kb(self.other)
        foreign["name"] = "PRIVATE FOREIGN NAME"
        good = self.document(own)
        self.document(foreign, self.other, 4)
        self.document(own, self.other, 5)
        self.document(foreign, self.owner, 6)
        status, data, _ = await self.get("?owner_id=" + str(self.other) + "&limit=100")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in data["documents"]], [str(good["_id"])])
        self.assertNotIn("PRIVATE", str(data))

    async def test_orphans_filtered_before_limit(self):
        base = self.kb()
        good = [self.document(base) for _ in range(3)]
        removed = self.kb()
        for _ in range(5):
            self.document(removed, days=2)
        self.db["knowledge_bases"].rows.remove(removed)
        self.assertEqual([r["id"] for r in (await self.get())[1]["documents"]],
                         [str(r["_id"]) for r in good[::-1]])

    async def test_more_than_100_bases_no_recent_base_truncation(self):
        bases = [self.kb() for _ in range(205)]
        rows = [self.document(base, days=i) for i, base in enumerate(bases)]
        bases[-1]["updated_at"] = self.now - timedelta(days=1000)
        self.assertEqual([r["id"] for r in (await self.get())[1]["documents"]],
                         [str(r["_id"]) for r in rows[::-1][:3]])
        self.assertEqual(len(self.db["documents"].calls), 1)
        self.assertFalse(self.db["knowledge_bases"].calls)

    async def test_bounded_pipeline_and_cursor_contract(self):
        await self.get()
        stages, options = self.db["documents"].calls[0]
        self.assertEqual(stages[0], {"$match": {"owner_id": self.owner}})
        self.assertEqual(stages[1], {"$sort": {"created_at": -1, "_id": -1}})
        self.assertEqual(stages[2]["$lookup"]["pipeline"][0]["$match"]["owner_id"], self.owner)
        self.assertEqual(stages[3:5], [{"$unwind": "$base"}, {"$limit": 3}])
        self.assertEqual(options, {"maxTimeMS": 5000})
        self.assertEqual(self.db["documents"].cursor.lengths, [3])
        self.assertFalse(any("$out" in s or "$merge" in s for s in stages))

    async def test_existing_processing_and_failure_statuses(self):
        row = self.document(self.kb())
        for state in ("uploaded", "processing", "processed", "failed"):
            row["status"] = state
            item = (await self.get())[1]["documents"][0]
            self.assertEqual(item["status"], state)
            self.assertEqual(item["failed"], state == "failed")
            self.assertFalse(item["indexed"])
        row["status"] = "processed"
        for key in ("embedding", "vector_index"):
            row[key] = {"status": "failed"}
            self.assertTrue((await self.get())[1]["documents"][0]["failed"])
            row.pop(key)

    async def test_indexed_requires_authoritative_metadata_and_no_claim(self):
        row = self.document(self.kb())
        generation = ObjectId()
        row.update(status="processed", chunk_generation=generation, chunk_count=2,
                   embedding=dict(status="generated", chunk_generation=generation, chunk_count=2, model="model", dimension=384),
                   vector_index=dict(status="indexed", chunk_generation=generation, chunk_count=2,
                                     embedding_model="model", embedding_dimension=384, collection_name="PRIVATE",
                                     target="PRIVATE", indexed_at=self.now))
        self.assertTrue((await self.get())[1]["documents"][0]["indexed"])
        row["_operation"] = ObjectId()
        row["_operation_meta"] = {"token": row["_operation"], "stage": "PRIVATE"}
        before = copy.deepcopy(row)
        data = (await self.get())[1]
        self.assertFalse(data["documents"][0]["indexed"])
        self.assertNotIn("PRIVATE", str(data))
        self.assertEqual(row, before)
        row.pop("_operation")
        row["vector_index"]["chunk_generation"] = ObjectId()
        self.assertFalse((await self.get())[1]["documents"][0]["indexed"])

    async def test_optional_missing_malformed_name_and_metadata(self):
        base = self.kb()
        row = self.document(base)
        row.update(embedding="malformed", vector_index=None, _operation_meta={"secret": "PRIVATE"})
        for name in (None, {}, 123, "", " " * 3, "x" * 101):
            base["name"] = name
            status, data, _ = await self.get()
            self.assertEqual(status, 200)
            self.assertIsNone(data["documents"][0]["knowledge_base_name"])
            self.assertFalse(data["documents"][0]["indexed"])
        del base["name"]
        self.assertIsNone((await self.get())[1]["documents"][0]["knowledge_base_name"])

    async def test_naive_bson_dates_are_utc(self):
        self.document(self.kb())["created_at"] = self.now.replace(tzinfo=None)
        self.assertEqual((await self.get())[1]["documents"][0]["created_at"], "2026-10-18T12:00:00Z")

    async def test_database_errors_and_timeouts_sanitized(self):
        for failure in (ConnectionFailure("PRIVATE"), TimeoutError("PRIVATE")):
            self.db["documents"].failure = failure
            status, data, _ = await self.get()
            self.assertEqual(status, 503)
            self.assertEqual(data, {"detail": "Recent documents are temporarily unavailable."})

    async def test_malformed_required_field_fails_safely(self):
        self.document(self.kb())["status"] = {"PRIVATE": True}
        status, data, _ = await self.get()
        self.assertEqual(status, 503)
        self.assertNotIn("PRIVATE", str(data))

    async def test_cancellation_propagates(self):
        self.db["documents"].failure = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.get()

    async def test_no_document_or_kb_mutations(self):
        base = self.kb()
        row = self.document(base)
        row["_operation"] = ObjectId()
        row["_operation_meta"] = {"token": row["_operation"], "stage_sequence": 7}
        before = {name: copy.deepcopy(c.rows) for name, c in self.db.items()}
        await self.get()
        self.assertEqual(before, {name: c.rows for name, c in self.db.items()})

    async def test_index_initialization_is_repeatable_without_changing_existing_index(self):
        collection = SimpleNamespace(create_index=AsyncMock())
        database = SimpleNamespace(get_collection=lambda name: collection)
        await ensure_document_indexes(database)
        first = collection.create_index.await_args_list[:]
        await ensure_document_indexes(database)
        self.assertEqual(collection.create_index.await_args_list, first + first)
        self.assertEqual(first[0].kwargs, {"name": "documents_owner_base_created"})
        self.assertEqual(first[1].args[0], [("owner_id", 1), ("created_at", -1), ("_id", -1)])
        self.assertEqual(first[1].kwargs, {"name": "documents_owner_created"})
