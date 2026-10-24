"""Offline readiness tests; dependency fakes expose read-only methods only."""
import asyncio
import json
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import readiness
from config import ConfigurationError, Settings
from database import database_lifespan
from fastapi import FastAPI
from main import app


async def response(path):
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
               "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
               "query_string": b"", "root_path": "", "headers": [],
               "server": ("test", 80), "client": ("test", 1)}, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    body = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
    return start["status"], body, dict(start["headers"])


class ConfigurationTests(unittest.TestCase):
    def test_missing_configuration(self):
        with patch("config.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            self.assertFalse(readiness.configuration_ready())

    def test_each_loader_failure_and_success(self):
        for failed in [None, *readiness.CONFIG_LOADERS]:
            with self.subTest(loader=failed), ExitStack() as stack:
                for name in readiness.CONFIG_LOADERS:
                    stack.enter_context(patch.object(readiness.config, name,
                        side_effect=ConfigurationError("private-url-secret") if name == failed else None))
                self.assertEqual(readiness.configuration_ready(), failed is None)


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = patch("readiness.configuration_ready", return_value=True)
        self.valid = self.config.start()
        self.state = readiness.Readiness()
        self.state.initialized = True
        self.state.indexes = dict.fromkeys(readiness.INDEX_GROUPS, True)
        self.db = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
        self.vector = SimpleNamespace(health=AsyncMock())
        self.store = patch("readiness.get_vector_store", return_value=self.vector)
        self.factory = self.store.start()

    async def asyncTearDown(self):
        await self.state.close()
        self.store.stop()
        self.config.stop()

    async def test_healthy_cache_and_expiration(self):
        with patch("readiness.time.monotonic", return_value=100):
            self.assertTrue(await self.state.check(self.db))
            self.assertTrue(await self.state.check(self.db))
        self.db.command.assert_awaited_once_with("ping")
        self.vector.health.assert_awaited_once_with()
        with patch("readiness.time.monotonic", return_value=103):
            self.assertTrue(await self.state.check(self.db))
        self.assertEqual(self.db.command.await_count, 2)
        self.valid.assert_called_once()

    async def test_prerequisites_fail_without_probes(self):
        for field in ["initialized", "configuration_valid"]:
            setattr(self.state, field, False)
            self.assertFalse(await self.state.check(self.db))
            setattr(self.state, field, True)
        self.assertFalse(await self.state.check(None))
        self.state.shutting_down = True
        self.assertFalse(await self.state.check(self.db))
        self.state.shutting_down = False
        for name in readiness.INDEX_GROUPS:
            self.state.indexes[name] = False
            self.assertFalse(await self.state.check(self.db))
            self.state.indexes[name] = True
        self.db.command.assert_not_awaited()
        self.factory.assert_not_called()

    async def test_probes_never_invoke_mutating_or_expensive_operations(self):
        forbidden = [
            "database.ensure_user_indexes", "database.ensure_knowledge_base_indexes",
            "database.ensure_document_indexes", "database.ensure_chunk_indexes",
            "database.ensure_history_indexes", "document_operations.claim_document",
            "document_operations.release_document", "document_operations.record_operation_stage",
            "embeddings.load_model", "embeddings.embed_text", "gemini_provider.get_gemini_provider",
            "vector_store.VectorStore.ensure_collection",
        ]
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(name, side_effect=AssertionError("forbidden"))) for name in forbidden]
            self.assertTrue(await self.state.check(self.db))
            for mock in mocks:
                mock.assert_not_called()
        self.db.command.assert_awaited_once_with("ping")

    async def test_failures_and_temporary_recovery(self):
        for target in [self.db.command, self.vector.health]:
            for error in [OSError("private-host"), TimeoutError(), RuntimeError("private-token")]:
                with self.subTest(target=target, error=type(error)):
                    self.state._expires = 0
                    target.side_effect = error
                    self.assertFalse(await self.state.check(self.db))
                    target.side_effect = None
                    # Cached failure cannot cause immediate repeated probes.
                    self.assertFalse(await self.state.check(self.db))
                    self.state._expires = 0
                    self.assertTrue(await self.state.check(self.db))

    async def test_dependency_deadline_cancels_hanging_check(self):
        for target in [self.db.command, self.vector.health]:
            entered, cancelled = asyncio.Event(), asyncio.Event()
            async def hang(*args):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            target.side_effect = hang
            self.state._expires = 0
            with patch("readiness.DEPENDENCY_TIMEOUT", 0):
                self.assertFalse(await self.state.check(self.db))
            self.assertTrue(entered.is_set())
            self.assertTrue(cancelled.is_set())
            target.side_effect = None

    async def test_concurrent_callers_and_cancellation(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def ping(*args):
            entered.set()
            await finish.wait()
            return {"ok": 1}
        self.db.command.side_effect = ping
        first = asyncio.create_task(self.state.check(self.db))
        await entered.wait()
        probe = self.state._task
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(probe.cancelled())
        callers = [asyncio.create_task(self.state.check(self.db)) for _ in range(20)]
        finish.set()
        self.assertEqual(await asyncio.gather(*callers), [True] * 20)
        self.db.command.assert_awaited_once()
        self.vector.health.assert_awaited_once()

    async def test_shutdown_cleans_pending_cycle_and_prevents_stale_success(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def ping(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.db.command.side_effect = ping
        caller = asyncio.create_task(self.state.check(self.db))
        await entered.wait()
        await self.state.close()
        self.assertFalse(await caller)
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.state._task)
        self.assertFalse(await self.state.check(self.db))
        self.assertEqual(self.state._expires, 0)

    async def test_overall_deadline(self):
        # Timeout fires at the next suspension, without wall-clock sleeps.
        with patch("readiness.PROBE_TIMEOUT", 0):
            self.assertFalse(await self.state.check(self.db))
            await self.state._task
        self.assertFalse(self.state._result)

    async def test_endpoint_generic_and_liveness_unchanged(self):
        original = app.state._state.copy()
        try:
            app.state.readiness = self.state
            app.state.database = self.db
            for healthy in [True, False]:
                self.state._expires = 0
                self.db.command.side_effect = None if healthy else RuntimeError("private-url-secret")
                status, body, headers = await response("/api/ready")
                self.assertEqual(status, 200 if healthy else 503)
                self.assertEqual(body, {"status": "ready" if healthy else "not_ready"})
                self.assertEqual(headers[b"cache-control"], b"no-store")
                status, body, _ = await response("/api/health")
                self.assertEqual((status, body), (200, {"status": "ok", "message": "DeepDocs AI API is running"}))
            del app.state.readiness
            self.assertEqual((await response("/api/ready"))[0], 503)
        finally:
            app.state._state.clear()
            app.state._state.update(original)


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_index_outcomes_order_reset_and_no_recovery(self):
        functions = ["ensure_user_indexes", "ensure_knowledge_base_indexes", "ensure_document_indexes",
                     "ensure_chunk_indexes", "ensure_history_indexes"]
        for failure in [None, *range(5), "ping"]:
            with self.subTest(failure=failure), ExitStack() as stack:
                stack.enter_context(patch("readiness.configuration_ready", return_value=True))
                stack.enter_context(patch("database.load_settings", return_value=Settings("mock-only")))
                db = SimpleNamespace(command=AsyncMock(return_value={"ok": 1}))
                client = MagicMock()
                client.get_database.return_value = db
                client.close = AsyncMock()
                stack.enter_context(patch("database.AsyncMongoClient", return_value=client))
                stack.enter_context(patch("database.ping_database", new=AsyncMock(return_value=failure != "ping")))
                calls = []
                for i, name in enumerate(functions):
                    async def initialize(database, i=i):
                        calls.append(i)
                        if failure == i:
                            raise TimeoutError("private")
                    stack.enter_context(patch("database." + name, side_effect=initialize))
                probe = stack.enter_context(patch("readiness.get_vector_store", return_value=SimpleNamespace(health=AsyncMock())))
                local = FastAPI()
                previous = None
                for _ in range(2):
                    async with database_lifespan(local):
                        state = local.state.readiness
                        self.assertIsNot(state, previous)
                        self.assertTrue(state.initialized)
                        for i, group in enumerate(readiness.INDEX_GROUPS):
                            self.assertEqual(state.indexes[group], failure != i and failure != "ping")
                        before = calls.copy()
                        self.assertEqual(await state.check(db), failure is None)
                        self.assertEqual(calls, before)
                    self.assertTrue(state.shutting_down)
                    self.assertFalse(state.initialized)
                    previous = state
                self.assertEqual(calls, [] if failure == "ping" else list(range(5)) * 2)
                if failure is not None:
                    probe.assert_not_called()
                self.assertEqual(client.close.await_count, 2)
