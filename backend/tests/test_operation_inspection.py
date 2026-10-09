"""Offline inspection contract, read-only boundary, and lifecycle snapshots."""
import copy
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from bson import ObjectId
from pymongo.errors import ConnectionFailure
from test_knowledge_bases import request, app, get_current_user, get_database
from test_dashboard import evaluate, field, MISSING
from dashboard import indexed_expression


class ReadOnlyCollection:
    """Only aggregation is available: any attempted write/read elsewhere fails."""
    def __init__(self):
        self.rows = []
        self.calls = []
        self.failure = None
        self.before_read = None

    async def aggregate(self, stages, **kwargs):
        self.calls.append((stages, kwargs))
        if self.failure:
            raise self.failure
        if self.before_read:
            self.before_read()
        assert len(stages) == 3
        scope = stages[0]["$match"]
        assert set(scope) == {"_id", "owner_id"}
        assert stages[1] == {"$limit": 1}
        assert kwargs == {"maxTimeMS": 5000}
        rows = [copy.deepcopy(row) for row in self.rows if all(row.get(k) == v for k, v in scope.items())][:1]
        projected = []
        for row in rows:
            output = {}
            for key, expression in stages[2]["$project"].items():
                if expression == 0:
                    continue
                # Short-circuit the conjunction after its BSON type guards. The
                # shared fake otherwise compares malformed lists to Python ints;
                # MongoDB supports BSON comparisons and returns false overall.
                if key == "indexed":
                    value = all(evaluate(term, row) for term in expression["$and"])
                else:
                    value = field(row, key) if expression == 1 else evaluate(expression, row)
                # Existing shared evaluator omits BSON object/array types.
                if key in ("embedding_type", "vector_type"):
                    raw = field(row, expression["$type"][1:])
                    if isinstance(raw, dict): value = "object"
                    elif isinstance(raw, list): value = "array"
                if value is MISSING:
                    continue
                if "." in key:
                    parent, child = key.split(".")
                    output.setdefault(parent, {})[child] = value
                else:
                    output[key] = value
            projected.append(output)
        async def to_list(length):
            assert length == 1
            return projected
        return SimpleNamespace(to_list=to_list)


class OperationInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner, self.identifier = ObjectId(), ObjectId()
        self.collection = ReadOnlyCollection()
        self.row = dict(_id=self.identifier, owner_id=self.owner, status="uploaded",
                        storage_path="PRIVATE", processing_error="PRIVATE", secret="PRIVATE")
        self.collection.rows.append(self.row)
        def get_collection(name):
            self.assertEqual(name, "documents")
            return self.collection
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=str(self.owner))
        app.dependency_overrides[get_database] = lambda: SimpleNamespace(get_collection=get_collection)
        self.guards = ExitStack()
        for target in ("document_operations.claim_document", "document_operations.release_document",
                       "document_routes.claim_document", "document_routes.release_document",
                       "document_routes.get_vector_store", "vector_store.get_vector_store",
                       "document_storage.DocumentStorage.reference", "document_storage.DocumentStorage.save",
                       "document_storage.DocumentStorage.delete", "document_processing.process_document",
                       "document_embeddings.generate_embeddings", "documents.delete_document"):
            self.guards.enter_context(patch(target, side_effect=AssertionError("Forbidden inspection side effect")))

    async def asyncTearDown(self):
        self.guards.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    async def get(self, identifier=None):
        return await request("GET", f"/api/documents/{identifier or self.identifier}/operation-status")

    def processed(self):
        self.row.update(status="processed", chunk_generation=ObjectId(), chunk_count=2,
                        embedding={"status": "not_generated"}, vector_index={"status": "not_generated"})

    def indexed(self):
        self.processed()
        generation = self.row["chunk_generation"]
        self.row.update(embedding=dict(status="generated", model="model", dimension=384,
                                      chunk_count=2, chunk_generation=generation),
                        vector_index=dict(status="indexed", embedding_model="model", embedding_dimension=384,
                                          chunk_count=2, chunk_generation=generation,
                                          collection_name="PRIVATE", target="PRIVATE", indexed_at=datetime.now(timezone.utc)))

    async def expect(self, state, attention, action, operation="idle"):
        before = copy.deepcopy(self.collection.rows)
        status, body, _ = await self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body, dict(document_id=str(self.identifier), operation_state=operation,
                                  document_state=state, attention=attention, recommended_action=action))
        self.assertEqual(self.collection.rows, before)
        self.assertNotIn("PRIVATE", str(body))

    async def test_authentication_before_database_access(self):
        del app.dependency_overrides[get_current_user]
        self.assertEqual((await self.get())[0], 401)
        self.assertEqual(self.collection.calls, [])

    async def test_invalid_id_before_database_access(self):
        self.assertEqual((await self.get("invalid"))[0], 422)
        self.assertEqual(self.collection.calls, [])

    async def test_missing_and_foreign_indistinguishable(self):
        missing = await self.get(str(ObjectId()))
        self.row["owner_id"] = ObjectId()
        foreign = await self.get()
        self.assertEqual(missing[:2], foreign[:2])
        self.assertEqual(foreign[0], 404)

    async def test_uploaded(self):
        await self.expect("uploaded", "needs_processing", "process")

    async def test_processed(self):
        self.processed()
        await self.expect("processed", "needs_reindex", "reindex")

    async def test_indexed_uses_exact_management_definition(self):
        self.indexed()
        await self.expect("indexed", "none", "none")
        self.assertEqual(self.collection.calls[-1][0][2]["$project"]["indexed"], indexed_expression())

    async def test_processing_or_deletion_failure_requires_review(self):
        self.row["status"] = "failed"
        await self.expect("failed", "requires_review", "contact_support")

    async def test_embedding_and_vector_failures(self):
        for key in ("embedding", "vector_index"):
            with self.subTest(key=key):
                self.processed()
                self.row[key]["status"] = "failed"
                await self.expect("failed", "needs_reindex", "reindex")

    async def test_claim_presence_never_authorizes_retry(self):
        for token in (ObjectId("000000000000000000000001"), None, "PRIVATE", {}):
            for state in ("uploaded", "processing", "processed", "failed", "invalid"):
                with self.subTest(token=token, state=state):
                    self.row.update(_operation=token, status=state)
                    await self.expect(state if state != "invalid" else "unknown", "outcome_uncertain", "refresh", "claimed_unknown")

    async def test_claimed_indexed_is_not_indexed_or_actionable(self):
        self.indexed()
        self.row["_operation"] = ObjectId()
        await self.expect("processed", "outcome_uncertain", "refresh", "claimed_unknown")

    async def test_ambiguous_combinations(self):
        for changes in (dict(status="processing"), dict(status="mystery"), dict(chunk_generation=None),
                        dict(chunk_count=0), dict(chunk_count=True), dict(embedding=None),
                        dict(vector_index=[]), dict(embedding={"status": "mystery"}),
                        dict(embedding={"status": "generating"}), dict(vector_index={"status": "indexing"}),
                        dict(vector_index={"status": "indexed"})):
            with self.subTest(changes=changes):
                self.processed()
                self.row.update(changes)
                await self.expect("unknown", "requires_review", "contact_support")

    async def test_inconsistent_index_success_not_certified(self):
        for key, value in (("chunk_count", 3), ("chunk_generation", ObjectId()), ("embedding_dimension", 10)):
            self.indexed()
            self.row["vector_index"][key] = value
            await self.expect("unknown", "requires_review", "contact_support")

    async def test_database_errors_sanitized(self):
        for error in (ConnectionFailure("PRIVATE connection"), TimeoutError("PRIVATE timeout")):
            self.collection.failure = error
            status, body, _ = await self.get()
            self.assertEqual(status, 503)
            self.assertNotIn("PRIVATE", str(body))

    async def test_changes_between_inspections_and_disappearance(self):
        await self.expect("uploaded", "needs_processing", "process")
        self.row.update(status="processing", _operation=ObjectId())
        await self.expect("processing", "outcome_uncertain", "refresh", "claimed_unknown")
        self.collection.rows.clear()
        self.assertEqual((await self.get())[0], 404)

    async def test_owner_change_before_read_cannot_return_foreign_snapshot(self):
        self.collection.before_read = lambda: self.row.update(owner_id=ObjectId())
        self.assertEqual((await self.get())[0], 404)

    async def test_schema_is_bounded_and_requires_bearer(self):
        route = app.openapi()["paths"]["/api/documents/{document_id}/operation-status"]["get"]
        self.assertTrue(route["security"])
        schema = app.openapi()["components"]["schemas"]["OperationStatusResponse"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(len(schema["properties"]), 5)
