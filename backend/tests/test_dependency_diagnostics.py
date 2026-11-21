"""Coalescing, cancellation and fresh results without any actual providers."""
import asyncio
from types import SimpleNamespace
import unittest
from fastapi import FastAPI
from dependency_diagnostics import DependencyDiagnostics, get_diagnostics
from vector_store import VectorFailure


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesced_callers_cancellation_and_fresh_results(self):
        for name in ('database', 'qdrant'):
            with self.subTest(name=name):
                service = DependencyDiagnostics()
                entered, release = asyncio.Event(), asyncio.Event()
                calls = 0
                async def probe():
                    nonlocal calls
                    calls += 1
                    entered.set()
                    await release.wait()
                    return True
                tasks = [asyncio.create_task(service.run(name, probe)) for _ in range(10)]
                await entered.wait()
                tasks[0].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[0]
                self.assertEqual(calls, 1)
                self.assertFalse(service._tasks[name].cancelled())
                release.set()
                self.assertEqual(await asyncio.gather(*tasks[1:]), [True] * 9)
                self.assertTrue(await service.run(name, probe))
                self.assertEqual(calls, 2)
                await service.close()

    async def test_error_is_not_cached_and_per_app_isolation(self):
        first_request = SimpleNamespace(app=FastAPI())
        first = await get_diagnostics(first_request)
        second = await get_diagnostics(SimpleNamespace(app=FastAPI()))
        self.assertIs(await get_diagnostics(first_request), first)
        self.assertIsNot(first, second)
        calls = 0
        async def failed():
            nonlocal calls
            calls += 1
            raise VectorFailure('synthetic')
        for _ in range(2):
            with self.assertRaises(VectorFailure):
                await first.run('qdrant', failed)
        self.assertEqual(calls, 2)
        self.assertFalse(second._tasks)
        await first.close()
        await second.close()

    async def test_shutdown_drains_probe_and_prevents_new_dispatch(self):
        service = DependencyDiagnostics()
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe():
            entered.set()
            await release.wait()
            return True
        caller = asyncio.create_task(service.run('database', probe))
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        service.begin_shutdown()
        closing = asyncio.create_task(service.close())
        self.assertFalse(await service.run('database', probe))
        with self.assertRaises(VectorFailure):
            await service.run('qdrant', probe)
        self.assertFalse(closing.done())
        release.set()
        await closing


class DiagnosticAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_and_revoked_accounts_never_dispatch_probes(self):
        from contextlib import ExitStack
        from datetime import datetime, timezone
        from unittest.mock import AsyncMock, Mock, patch
        from bson import ObjectId
        from httpx import ASGITransport, AsyncClient
        from main import app
        from database import get_database
        from auth import create_access_token
        from config import JWTSettings
        settings = JWTSettings('offline-test-signing-material-long-enough')
        owner = ObjectId()
        user = {'_id': owner, 'name': 'Synthetic', 'email': 'demo@example.com',
                'created_at': datetime.now(timezone.utc), 'approved': True, 'enabled': True}
        collection = SimpleNamespace(find_one=AsyncMock(side_effect=lambda *a, **k: dict(user)))
        db = SimpleNamespace(get_collection=Mock(return_value=collection))
        previous = app.dependency_overrides.copy()
        database_access = Mock(return_value=db)
        app.dependency_overrides.clear()
        app.dependency_overrides[get_database] = lambda: database_access()
        try:
            with ExitStack() as stack:
                stack.enter_context(patch('auth.load_jwt_settings', return_value=settings))
                stack.enter_context(patch('auth.load_private_access_enabled', return_value=True))
                mongo = stack.enter_context(patch('main.ping_database', new_callable=AsyncMock, return_value=True))
                qdrant = SimpleNamespace(health=AsyncMock())
                factory = stack.enter_context(patch('document_routes.get_vector_store', return_value=qdrant))
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                    for _ in range(3):
                        for path in ('/api/health/database', '/api/health/qdrant'):
                            self.assertEqual((await client.get(path)).status_code, 401)
                    database_access.assert_not_called()
                    mongo.assert_not_awaited(); factory.assert_not_called()
                    self.assertEqual((await client.get('/api/health')).status_code, 200)
                    token = create_access_token(owner, settings)
                    client.headers['Authorization'] = 'Bearer ' + token
                    for path in ('/api/health/database', '/api/health/qdrant'):
                        self.assertEqual((await client.get(path)).status_code, 200)
                    mongo.assert_awaited_once(); qdrant.health.assert_awaited_once()
                    for flag in ('approved', 'enabled'):
                        user[flag] = False
                        for path in ('/api/health/database', '/api/health/qdrant'):
                            self.assertEqual((await client.get(path)).status_code, 403)
                        user[flag] = True
                    mongo.assert_awaited_once(); qdrant.health.assert_awaited_once()
        finally:
            app.dependency_overrides.clear(); app.dependency_overrides.update(previous)
