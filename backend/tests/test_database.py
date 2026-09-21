"""Run from the repository root with unittest discovery; no Atlas access."""
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from pymongo.errors import ConnectionFailure
from config import ConfigurationError, Settings, load_settings
from database import database_lifespan
from main import app


async def get(path):
    """Exercise the ASGI app without an HTTP client dependency."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"", "root_path": "", "headers": [],
            "server": ("test", 80), "client": ("test", 123),
        },
        receive, send,
    )
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(body)


class ConfigurationTests(unittest.TestCase):
    def test_missing_and_blank_uri(self):
        for value in [None, "", "   "]:
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                with patch("config.dotenv_values", return_value={"MONGODB_URI": value}):
                    with self.assertRaisesRegex(ConfigurationError, "MONGODB_URI is required"):
                        load_settings()

    def test_environment_precedence_and_safe_repr(self):
        # Mock-only strings are never used to construct a real MongoDB client.
        with patch("config.dotenv_values", return_value={"MONGODB_URI": "file-value"}):
            with patch.dict(os.environ, {"MONGODB_URI": "mongodb://mock-only"}, clear=True):
                settings = load_settings()
        self.assertEqual(settings.mongodb_db_name, "deepdocs_ai")
        self.assertEqual(settings.mongodb_uri, "mongodb://mock-only")
        self.assertNotIn("mock-only", repr(settings))

    def test_invalid_config_does_not_echo_values(self):
        for values in [
            {"MONGODB_URI": "private-invalid-value"},
            {"MONGODB_URI": "mongodb://mock-only", "MONGODB_DB_NAME": "bad/name"},
        ]:
            with patch.dict(os.environ, values, clear=True):
                with patch("config.dotenv_values", return_value={}):
                    with self.assertRaises(ConfigurationError) as error:
                        load_settings()
            self.assertNotIn("private-invalid-value", str(error.exception))
            self.assertNotIn("bad/name", str(error.exception))


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    def mock_client(self):
        client = MagicMock()
        client.close = AsyncMock()
        database = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
        client.get_database.return_value = database
        return client, database

    async def test_missing_config_preserves_health_and_returns_503(self):
        with patch("database.load_settings", side_effect=ConfigurationError("MONGODB_URI is required.")):
            with patch("database.AsyncMongoClient") as factory:
                async with database_lifespan(app):
                    self.assertEqual(await get("/api/health"), (
                        200, {"status": "ok", "message": "DeepDocs AI API is running"}
                    ))
                    status, body = await get("/api/health/database")
                    self.assertEqual(status, 503)
                    self.assertIn("MONGODB_URI is required", body["detail"])
                factory.assert_not_called()

    async def test_shared_client_ping_and_clean_shutdown(self):
        client, database = self.mock_client()
        with patch("database.load_settings", return_value=Settings("mock-only")):
            with patch("database.AsyncMongoClient", return_value=client) as factory:
                async with database_lifespan(app):
                    for _ in range(2):
                        self.assertEqual((await get("/api/health/database"))[0], 200)
                    factory.assert_called_once()
                    client.get_database.assert_called_once_with("deepdocs_ai")
                    self.assertEqual(database.command.await_count, 3)
                    client.close.assert_not_awaited()
                client.close.assert_awaited_once()
                self.assertIsNone(app.state.database)

    async def test_failure_is_sanitized_and_same_client_recovers(self):
        client, database = self.mock_client()
        database.command.side_effect = [
            ConnectionFailure("private-driver-detail"),
            ConnectionFailure("private-driver-detail"),
            {"ok": 1},
        ]
        with patch("database.load_settings", return_value=Settings("mock-only")):
            with patch("database.AsyncMongoClient", return_value=client) as factory:
                async with database_lifespan(app):
                    status, body = await get("/api/health/database")
                    self.assertEqual(status, 503)
                    self.assertNotIn("private-driver-detail", json.dumps(body))
                    self.assertEqual((await get("/api/health"))[0], 200)
                    self.assertEqual((await get("/api/health/database"))[0], 200)
                    factory.assert_called_once()
                client.close.assert_awaited_once()

    async def test_invalid_driver_config_is_sanitized(self):
        with patch("database.load_settings", return_value=Settings("mock-only")):
            with patch("database.AsyncMongoClient", side_effect=ValueError("private-driver-detail")):
                async with database_lifespan(app):
                    status, body = await get("/api/health/database")
                    self.assertEqual(status, 503)
                    self.assertNotIn("private-driver-detail", json.dumps(body))

    async def test_client_closes_when_application_body_raises(self):
        client, _ = self.mock_client()
        with patch("database.load_settings", return_value=Settings("mock-only")):
            with patch("database.AsyncMongoClient", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "application failure"):
                    async with database_lifespan(FastAPI()):
                        raise RuntimeError("application failure")
        client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
