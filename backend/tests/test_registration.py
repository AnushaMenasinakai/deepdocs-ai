"""Offline registration tests. Never read .env or connect to Atlas."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argon2 import PasswordHasher, extract_parameters
from argon2.low_level import Type
from bson import ObjectId
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure

from config import Settings
from database import database_lifespan, get_database
from main import app
from security import hash_password


async def post_registration(payload):
    messages = []
    body = json.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/api/auth/register",
            "raw_path": b"/api/auth/register", "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "server": ("test", 80), "client": ("test", 123),
        }, receive, send,
    )
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    response = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(response)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.collection = SimpleNamespace(insert_one=AsyncMock(), create_index=AsyncMock())
        self.database = SimpleNamespace(get_collection=MagicMock(return_value=self.collection))
        self.previous_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_database] = lambda: self.database
        self.previous_ready = getattr(app.state, "users_index_ready", False)
        app.state.users_index_ready = True
        self.hasher = patch("users.hash_password", new_callable=AsyncMock)
        self.hash_mock = self.hasher.start()
        self.hash_mock.return_value = "mock-encoded-hash"
        self.payload = {"name": " Example User ", "email": " USER@Example.com ", "password": "example-password"}

    async def asyncTearDown(self):
        self.hasher.stop()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)
        app.state.users_index_ready = self.previous_ready

    async def test_valid_registration_normalizes_and_only_stores_hash(self):
        status, response = await post_registration(self.payload)
        self.assertEqual(status, 201)
        self.assertEqual(set(response), {"id", "name", "email", "created_at"})
        self.assertEqual(response["name"], "Example User")
        self.assertEqual(response["email"], "user@example.com")
        self.assertTrue(ObjectId.is_valid(response["id"]))
        self.assertTrue(response["created_at"].endswith(("Z", "+00:00")))
        document = self.collection.insert_one.call_args.args[0]
        self.assertEqual(set(document), {"_id", "name", "email", "password_hash", "created_at"})
        self.assertIsInstance(document["_id"], ObjectId)
        self.assertEqual(document["created_at"].utcoffset().total_seconds(), 0)
        self.assertEqual(document["password_hash"], "mock-encoded-hash")
        self.assertNotIn(self.payload["password"], json.dumps(response))
        self.assertNotIn("mock-encoded-hash", json.dumps(response))
        self.hash_mock.assert_awaited_once_with("example-password")
        self.collection.create_index.assert_not_awaited()

    async def test_invalid_values_are_422_and_never_inserted_or_echoed(self):
        cases = [
            {"email": "invalid-email"}, {"password": "pw-123"},
            {"password": "a" * 129}, {"password": 12345678},
            {"name": "   "}, {"name": "a" * 101},
            {"password_hash": "untrusted-hash"}, {"password": None},
        ]
        for change in cases:
            with self.subTest(change=list(change)):
                status, response = await post_registration({**self.payload, **change})
                self.assertEqual(status, 422)
                for error in response["detail"]:
                    self.assertNotIn("input", error)
                response_text = json.dumps(response)
                submitted = {**self.payload, **change}
                for field in ("password", "password_hash"):
                    value = submitted.get(field)
                    if value is not None:
                        self.assertNotIn(str(value), response_text)
        for field in self.payload:
            payload = {k: v for k, v in self.payload.items() if k != field}
            self.assertEqual((await post_registration(payload))[0], 422)
        self.collection.insert_one.assert_not_awaited()
        self.hash_mock.assert_not_awaited()

    async def test_password_spaces_are_not_trimmed(self):
        payload = {**self.payload, "password": "  a long password  "}
        self.assertEqual((await post_registration(payload))[0], 201)
        self.hash_mock.assert_awaited_once_with(payload["password"])

    async def test_duplicate_key_maps_to_409_without_driver_details(self):
        self.collection.insert_one.side_effect = DuplicateKeyError("private-driver-detail")
        status, response = await post_registration(self.payload)
        self.assertEqual(status, 409)
        self.assertNotIn("private-driver-detail", json.dumps(response))
        self.assertNotIn("user@example.com", json.dumps(response))

    async def test_concurrent_normalized_duplicates(self):
        # Emulate atomic uniqueness. A real unique index is separately asserted.
        emails = set()

        async def insert(document):
            if document["email"] in emails:
                raise DuplicateKeyError("duplicate")
            emails.add(document["email"])

        self.collection.insert_one.side_effect = insert
        results = await asyncio.gather(
            post_registration(self.payload),
            post_registration({**self.payload, "email": "user@example.com"}),
        )
        self.assertEqual(sorted(status for status, _ in results), [201, 409])

    async def test_database_errors_are_safe_503(self):
        for error in [ConnectionFailure("private-detail"), OperationFailure("private-detail")]:
            self.collection.insert_one.side_effect = error
            status, response = await post_registration(self.payload)
            self.assertEqual(status, 503)
            self.assertNotIn("private-detail", json.dumps(response))

    async def test_no_registration_without_confirmed_index(self):
        app.state.users_index_ready = False
        self.assertEqual((await post_registration(self.payload))[0], 503)
        self.collection.insert_one.assert_not_awaited()
        self.hash_mock.assert_not_awaited()


class IndexLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_unique_index_once_at_startup_and_safe_failure(self):
        for fail in [False, True]:
            collection = SimpleNamespace(create_index=AsyncMock())
            if fail:
                collection.create_index.side_effect = OperationFailure("private-index-detail")
            database = SimpleNamespace(
                command=AsyncMock(return_value={"ok": 1}),
                get_collection=MagicMock(side_effect=lambda name: (
                    collection if name == "users"
                    else SimpleNamespace(create_index=AsyncMock())
                )),
            )
            client = MagicMock()
            client.get_database.return_value = database
            client.close = AsyncMock()
            with patch("database.load_settings", return_value=Settings("mock-only")):
                with patch("database.AsyncMongoClient", return_value=client):
                    async with database_lifespan(app):
                        self.assertEqual(app.state.users_index_ready, not fail)
                        collection.create_index.assert_awaited_once_with(
                            [("email", 1)], unique=True, name="users_email_unique",
                        )
                    self.assertFalse(app.state.users_index_ready)
                    client.close.assert_awaited_once()


class HashingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_argon2id_hashes_are_salted_and_verifiable(self):
        password = "offline-test-password"
        first = await hash_password(password)
        second = await hash_password(password)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, password)
        self.assertEqual(extract_parameters(first).type, Type.ID)
        self.assertTrue(PasswordHasher().verify(first, password))


if __name__ == "__main__":
    unittest.main()
