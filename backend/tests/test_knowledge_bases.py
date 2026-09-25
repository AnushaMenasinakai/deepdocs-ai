"""Offline CRUD tests: real routes/data access, in-memory MongoDB substitute."""
import copy
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import ConnectionFailure, OperationFailure
from auth import get_current_user
from config import Settings
from database import database_lifespan, get_database
from knowledge_bases import public_knowledge_base
from main import app
from user_schemas import UserResponse

BASE = "/api/knowledge-bases"
PUBLIC_FIELDS = {"id", "name", "description", "created_at", "updated_at"}


async def request(method, path=BASE, payload=None):
    messages = []
    body = json.dumps(payload).encode() if payload is not None else b""

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("test", 80), "client": ("test", 123),
    }, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], json.loads(raw) if raw else None, dict(start["headers"])


class MemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, order):
        for key, direction in reversed(order):
            self.documents.sort(key=lambda document: document[key], reverse=direction == -1)
        return self

    def __aiter__(self):
        self.iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


class MemoryCollection:
    def __init__(self):
        self.documents = {}
        self.queries = []
        self.failure = None

    def matching(self, query):
        self.queries.append(copy.deepcopy(query))
        if self.failure:
            raise self.failure
        return [
            document for document in self.documents.values()
            if all(
                (bool(document.get("_document_ids")) == value["$exists"])
                if key == "_document_ids.0"
                else ((key in document) == value["$exists"])
                if isinstance(value, dict) and "$exists" in value
                else document.get(key) == value
                for key, value in query.items()
            )
        ]

    async def insert_one(self, document):
        if self.failure:
            raise self.failure
        self.documents[document["_id"]] = copy.deepcopy(document)

    def find(self, query):
        return MemoryCursor(copy.deepcopy(self.matching(query)))

    async def find_one(self, query):
        matches = self.matching(query)
        return copy.deepcopy(matches[0]) if matches else None

    async def find_one_and_update(self, query, update, return_document):
        assert return_document == ReturnDocument.AFTER
        assert set(update) == {"$set"}
        matches = self.matching(query)
        if not matches:
            return None
        matches[0].update(copy.deepcopy(update["$set"]))
        return copy.deepcopy(matches[0])

    async def delete_one(self, query):
        matches = self.matching(query)
        if matches:
            del self.documents[matches[0]["_id"]]
        return SimpleNamespace(deleted_count=int(bool(matches)))


class KnowledgeBaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collection = MemoryCollection()
        self.document_collection = MemoryCollection()
        self.database = SimpleNamespace(get_collection=MagicMock(side_effect=lambda name:
            self.document_collection if name == "documents" else self.collection))
        self.owner = ObjectId()
        self.other_owner = ObjectId()
        self.current_owner = self.owner
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_database] = lambda: self.database

        def current_user():
            return UserResponse(
                id=str(self.current_owner), name="Offline User",
                email="user@example.com", created_at=datetime.now(timezone.utc),
            )

        app.dependency_overrides[get_current_user] = current_user

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    async def create(self, **changes):
        status, body, _ = await request("POST", payload={"name": " Notes ", **changes})
        self.assertEqual(status, 201)
        return body

    async def test_all_endpoints_require_authentication(self):
        del app.dependency_overrides[get_current_user]
        for method, path, payload in [
            ("POST", BASE, {"name": "Notes"}), ("GET", BASE, None),
            ("GET", BASE + "/" + str(ObjectId()), None),
            ("PATCH", BASE + "/" + str(ObjectId()), {"name": "Changed"}),
            ("DELETE", BASE + "/" + str(ObjectId()), None),
        ]:
            with self.subTest(method=method, path=path):
                status, body, headers = await request(method, path, payload)
                self.assertEqual(status, 401)
                self.assertEqual(headers[b"www-authenticate"], b"Bearer")
        self.database.get_collection.assert_not_called()

    async def test_create_trims_derives_owner_and_returns_only_public_fields(self):
        body = await self.create(description="  Course notes  ")
        self.assertEqual(set(body), PUBLIC_FIELDS)
        self.assertEqual(body["name"], "Notes")
        self.assertEqual(body["description"], "Course notes")
        self.assertEqual(body["created_at"], body["updated_at"])
        document = self.collection.documents[ObjectId(body["id"])]
        self.assertEqual(set(document), {"_id", "owner_id", "name", "description", "created_at", "updated_at"})
        self.assertEqual(document["owner_id"], self.owner)
        self.assertIsInstance(document["owner_id"], ObjectId)
        self.assertEqual(document["created_at"].utcoffset(), timedelta(0))
        self.database.get_collection.assert_called_with("knowledge_bases")

    async def test_description_normalization_and_valid_boundaries(self):
        for description in [None, "", "   "]:
            with self.subTest(description=description):
                self.assertIsNone((await self.create(description=description))["description"])
        self.assertIsNone((await self.create())["description"])
        body = await self.create(name="n" * 100, description="d" * 500)
        self.assertEqual(len(body["name"]), 100)
        self.assertEqual(len(body["description"]), 500)

    async def test_create_invalid_and_injected_fields_never_persist(self):
        cases = [
            {}, {"name": ""}, {"name": "   "}, {"name": "n" * 101},
            {"name": None}, {"name": 42}, {"name": "Notes", "description": "d" * 501},
            {"name": "Notes", "description": 42},
        ]
        for field in ["owner_id", "_id", "created_at", "updated_at", "unexpected"]:
            cases.append({"name": "Notes", field: "untrusted-value"})
        for payload in cases:
            with self.subTest(payload_fields=list(payload)):
                status, body, _ = await request("POST", payload=payload)
                self.assertEqual(status, 422)
                self.assertNotIn("untrusted-value", json.dumps(body))
        self.assertEqual(self.collection.documents, {})

    async def test_list_empty_and_owner_filtered_deterministic_order(self):
        self.assertEqual((await request("GET"))[:2], (200, []))
        first = await self.create()
        second = await self.create()
        third = await self.create()
        earlier = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.collection.documents[ObjectId(first["id"])]["updated_at"] = earlier
        later = earlier + timedelta(days=1)
        for body in [second, third]:
            self.collection.documents[ObjectId(body["id"])]["updated_at"] = later
        self.current_owner = self.other_owner
        await self.create(name="Other user's private notes")
        self.current_owner = self.owner
        status, bodies, _ = await request("GET")
        self.assertEqual(status, 200)
        expected = sorted([second["id"], third["id"]], reverse=True) + [first["id"]]
        self.assertEqual([body["id"] for body in bodies], expected)
        self.assertEqual(self.collection.queries[-1], {"owner_id": self.owner})
        for body in bodies:
            self.assertEqual(set(body), PUBLIC_FIELDS)

    async def test_get_owned_and_missing(self):
        body = await self.create()
        status, result, _ = await request("GET", BASE + "/" + body["id"])
        self.assertEqual((status, result), (200, body))
        self.assertEqual(self.collection.queries[-1]["_id"], ObjectId(body["id"]))
        self.assertEqual(self.collection.queries[-1]["owner_id"], self.owner)
        for method, payload in [("GET", None), ("PATCH", {"name": "New"}), ("DELETE", None)]:
            with self.subTest(method=method):
                status, result, _ = await request(method, BASE + "/" + str(ObjectId()), payload)
                self.assertEqual((status, result), (404, {"detail": "Knowledge Base not found."}))

    async def test_malformed_ids_are_422(self):
        for method, payload in [("GET", None), ("PATCH", {"name": "New"}), ("DELETE", None)]:
            with self.subTest(method=method):
                self.assertEqual((await request(method, BASE + "/not-an-id", payload))[0], 422)
        self.assertEqual(self.collection.queries, [])

    async def test_patch_preserves_owner_and_created_at_and_sets_server_timestamp(self):
        body = await self.create(description="Old")
        key = ObjectId(body["id"])
        original = copy.deepcopy(self.collection.documents[key])
        # Control server time instead of relying on clock resolution.
        later = original["updated_at"] + timedelta(seconds=5)
        with patch("knowledge_bases.datetime") as clock:
            clock.now.return_value = later
            status, result, _ = await request("PATCH", BASE + "/" + body["id"], {"name": " New "})
        self.assertEqual(status, 200)
        self.assertEqual(set(result), PUBLIC_FIELDS)
        self.assertEqual(result["name"], "New")
        self.assertEqual(result["description"], "Old")
        self.assertEqual(result["created_at"], body["created_at"])
        document = self.collection.documents[key]
        self.assertEqual(document["updated_at"], later)
        self.assertEqual(document["created_at"], original["created_at"])
        self.assertEqual(document["owner_id"], self.owner)
        self.assertEqual(self.collection.queries[-1], {"_id": key, "owner_id": self.owner})
        for value in ["   ", None]:
            status, result, _ = await request("PATCH", BASE + "/" + body["id"], {"description": value})
            self.assertEqual(status, 200)
            self.assertIsNone(result["description"])
            self.assertEqual(result["name"], "New")

    async def test_invalid_empty_or_injected_updates_do_not_mutate(self):
        body = await self.create()
        before = copy.deepcopy(self.collection.documents)
        cases = [{}, {"name": None}, {"name": ""}, {"name": "  "}, {"name": "n" * 101},
                 {"description": "d" * 501}, {"description": 12}]
        cases += [{field: "untrusted-value"} for field in ["owner_id", "_id", "created_at", "updated_at", "unexpected"]]
        for payload in cases:
            with self.subTest(fields=list(payload)):
                self.assertEqual((await request("PATCH", BASE + "/" + body["id"], payload))[0], 422)
        self.assertEqual(self.collection.documents, before)

    async def test_delete_owned_returns_empty_204_then_404(self):
        body = await self.create()
        path = BASE + "/" + body["id"]
        status, response, _ = await request("DELETE", path)
        self.assertEqual((status, response), (204, None))
        self.assertEqual(self.collection.queries[-1]["_id"], ObjectId(body["id"]))
        self.assertEqual(self.collection.queries[-1]["owner_id"], self.owner)
        self.assertEqual(self.collection.documents, {})
        self.assertEqual((await request("DELETE", path))[0], 404)

    async def test_idor_user_b_cannot_read_update_or_delete_user_a_resource(self):
        body = await self.create()
        path = BASE + "/" + body["id"]
        before = copy.deepcopy(self.collection.documents)
        self.current_owner = self.other_owner
        for method, payload in [("GET", None), ("PATCH", {"name": "Stolen"}), ("DELETE", None)]:
            with self.subTest(method=method):
                status, response, _ = await request(method, path, payload)
                self.assertEqual((status, response), (404, {"detail": "Knowledge Base not found."}))
                missing = await request(method, BASE + "/" + str(ObjectId()), payload)
                self.assertEqual((status, response), missing[:2])
                self.assertEqual(self.collection.queries[-2], {
                    "_id": ObjectId(body["id"]), "owner_id": self.other_owner,
                })
        self.assertEqual(self.collection.documents, before)
        self.assertEqual((await request("GET"))[:2], (200, []))
        self.current_owner = self.owner
        self.assertEqual((await request("GET", path))[0], 200)

    async def test_all_database_failures_are_safe_503(self):
        body = await self.create()
        path = BASE + "/" + body["id"]
        for failure in [ConnectionFailure("private-driver-detail"), OperationFailure("private-driver-detail"), TimeoutError("private-driver-detail")]:
            self.collection.failure = failure
            for method, target, payload in [
                ("POST", BASE, {"name": "Notes"}), ("GET", BASE, None),
                ("GET", path, None), ("PATCH", path, {"name": "New"}), ("DELETE", path, None),
            ]:
                with self.subTest(error=type(failure).__name__, method=method, path=target):
                    status, response, _ = await request(method, target, payload)
                    self.assertEqual(status, 503)
                    self.assertEqual(response, {"detail": "Knowledge Base service is temporarily unavailable."})

    def test_public_serializer_excludes_private_fields_and_restores_utc(self):
        document = {
            "_id": ObjectId(), "owner_id": self.owner, "name": "Notes", "description": None,
            "created_at": datetime(2020, 1, 1), "updated_at": datetime(2020, 1, 1),
            "password": "private-value", "password_hash": "private-value",
            "jwt": "private-value", "mongodb_uri": "private-value",
        }
        result = public_knowledge_base(document).model_dump(mode="json")
        self.assertEqual(set(result), PUBLIC_FIELDS)
        self.assertNotIn("private-value", json.dumps(result))
        self.assertTrue(result["created_at"].endswith("Z"))

    def test_all_knowledge_base_operations_have_openapi_bearer_requirement(self):
        paths = app.openapi()["paths"]
        for path, methods in [(BASE, ["get", "post"]), (BASE + "/{knowledge_base_id}", ["get", "patch", "delete"])]:
            for method in methods:
                self.assertEqual(paths[path][method]["security"], [{"HTTPBearer": []}])


class KnowledgeBaseIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_index_created_once_and_failure_sanitized_without_disabling_registration(self):
        for fail in [False, True]:
            with self.subTest(fail=fail):
                users = SimpleNamespace(create_index=AsyncMock())
                bases = SimpleNamespace(create_index=AsyncMock())
                if fail:
                    bases.create_index.side_effect = OperationFailure("private-index-detail")
                database = SimpleNamespace(
                    command=AsyncMock(return_value={"ok": 1}),
                    get_collection=MagicMock(side_effect=lambda name: {"users": users, "knowledge_bases": bases, "documents": SimpleNamespace(create_index=AsyncMock())}[name]),
                )
                client = MagicMock()
                client.get_database.return_value = database
                client.close = AsyncMock()
                with patch("database.load_settings", return_value=Settings("mock-only")), patch("database.AsyncMongoClient", return_value=client), patch("database.logger") as logger:
                    async with database_lifespan(app):
                        self.assertTrue(app.state.users_index_ready)
                        bases.create_index.assert_awaited_once_with(
                            [("owner_id", 1), ("updated_at", -1), ("_id", -1)],
                            name="knowledge_bases_owner_updated",
                        )
                    self.assertNotIn("private-index-detail", str(logger.mock_calls))
                    self.assertEqual(logger.error.called, fail)
                    client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
