"""Offline route/lifecycle protection tests with controlled password workers."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
import secrets
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from bson import ObjectId
from pymongo.errors import DuplicateKeyError
from test_auth import request
from auth_routes import router
from auth import get_auth_settings
from auth_protection import AuthProtection, auth_protection_lifespan, PasswordCapacityExceeded
from config import AuthProtectionSettings, JWTSettings, ConfigurationError
from database import get_database
from main import application_lifespan


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 0
        self.app = FastAPI()
        self.app.include_router(router)
        self.service = AuthProtection(AuthProtectionSettings(), clock=lambda: self.now)
        self.app.state.auth_protection = self.service
        self.app.state.users_index_ready = True
        self.collection = SimpleNamespace(find_one=AsyncMock(), insert_one=AsyncMock())
        self.collection.find_one.return_value = {'_id': ObjectId(), 'password_hash': 'stored'}
        self.database = SimpleNamespace(get_collection=MagicMock(return_value=self.collection))
        self.app.dependency_overrides[get_database] = lambda: self.database
        self.app.dependency_overrides[get_auth_settings] = lambda: JWTSettings(secrets.token_urlsafe(32))
        self.payload = {'email': ' User@Example.com ', 'password': 'test-password'}
        self.hasher = MagicMock()
        self.hasher.hash.return_value = 'encoded'
        self.hasher.verify.return_value = True
        self.patch = patch('security._hasher', self.hasher)
        self.patch.start()

    async def asyncTearDown(self):
        await self.service.aclose()
        self.patch.stop()

    async def post(self, action='login', *, client=('192.0.2.1', 123), headers=(), **changes):
        data = {**self.payload, **({'name':'Example'} if action=='register' else {}), **changes}
        return await request('POST', '/api/auth/'+action, data, application=self.app,
                             client=client, extra_headers=headers)

    def assert_limited(self, response):
        status, body, headers = response
        self.assertEqual(status, 429)
        self.assertEqual(body, {'detail':'Too many authentication requests. Please try again later.'})
        self.assertGreater(int(headers[b'retry-after']), 0)
        self.assertEqual(headers[b'cache-control'], b'no-store')

    async def test_login_account_limit_before_database_and_password(self):
        for _ in range(5): self.assertEqual((await self.post())[0], 200)
        with patch('auth_routes.create_access_token') as issue:
            self.assert_limited(await self.post())
            issue.assert_not_called()
        self.assertEqual(self.collection.find_one.await_count, 5)
        self.assertEqual(self.hasher.verify.call_count, 5)
        self.now = 60
        self.assertEqual((await self.post())[0], 200)

    async def test_login_ip_limit_across_accounts(self):
        for n in range(10): self.assertEqual((await self.post(email=f'user{n}@example.com'))[0], 200)
        self.assert_limited(await self.post(email='new@example.com'))
        self.assertEqual(self.collection.find_one.await_count, 10)

    async def test_registration_limit_before_hash_and_insert(self):
        for _ in range(3): self.assertEqual((await self.post('register'))[0], 201)
        self.assert_limited(await self.post('register'))
        self.assertEqual(self.hasher.hash.call_count, 3)
        self.assertEqual(self.collection.insert_one.await_count, 3)
        self.collection.find_one.assert_not_awaited()

    async def test_dummy_verification_and_generic_401(self):
        self.collection.find_one.return_value = None
        with patch('security._dummy_hash', return_value='dummy') as dummy:
            response = await self.post()
        self.assertEqual(response[0], 401)
        self.assertEqual(response[1], {'detail':'Invalid email or password'})
        dummy.assert_called_once()
        self.hasher.verify.assert_called_once_with('dummy', 'test-password')

    async def test_duplicate_registration_still_conflicts_and_releases_capacity(self):
        self.collection.insert_one.side_effect = DuplicateKeyError('private')
        self.assertEqual((await self.post('register'))[0], 409)
        self.assertEqual(self.service.passwords.outstanding, 0)

    async def test_no_capacity_held_during_database_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def insert(record):
            self.assertEqual(self.service.passwords.outstanding, 0)
            entered.set(); await release.wait()
        self.collection.insert_one.side_effect = insert
        task = asyncio.create_task(self.post('register'))
        await asyncio.wait_for(entered.wait(), 5)
        self.assertEqual(self.service.passwords.outstanding, 0)
        release.set(); self.assertEqual((await task)[0], 201)
        async def lookup(*args):
            self.assertEqual(self.service.passwords.outstanding, 0)
            return {'_id':ObjectId(), 'password_hash':'stored'}
        self.collection.find_one.side_effect = lookup
        self.assertEqual((await self.post())[0], 200)

    async def test_forwarded_headers_do_not_change_asgi_identity(self):
        for _ in range(3): await self.post('register')
        self.assert_limited(await self.post('register', headers=[(b'x-forwarded-for',b'198.51.100.9'),
                                                               (b'forwarded',b'for=198.51.100.10')]))
        self.assertEqual((await self.post('register', client=('198.51.100.9',123)))[0], 201)

    async def test_concurrent_login_registration_share_capacity_and_cancel_safely(self):
        loop = asyncio.get_running_loop()
        started = asyncio.Queue()
        release = threading.Event()
        def work(*args):
            loop.call_soon_threadsafe(started.put_nowait, True)
            if not release.wait(5): raise RuntimeError('synchronization failed')
            return 'encoded'
        self.hasher.hash.side_effect = work
        self.hasher.verify.side_effect = work
        tasks = [asyncio.create_task(self.post('register')), asyncio.create_task(self.post())]
        try:
            await asyncio.wait_for(started.get(), 5); await asyncio.wait_for(started.get(), 5)
            self.assertEqual(self.service.passwords.outstanding, 2)
            self.assert_limited(await self.post('register', email='other@example.com'))
            self.collection.insert_one.assert_not_awaited()
            with patch('auth_routes.create_access_token') as issue:
                for task in tasks: task.cancel(); task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(all(isinstance(r, asyncio.CancelledError) for r in results))
                self.assertEqual(self.service.passwords.outstanding, 2)
                release.set(); await self.service.aclose()
                issue.assert_not_called()
            self.collection.insert_one.assert_not_awaited()
            self.assertEqual(self.hasher.hash.call_count, 1)
            self.assertEqual(self.hasher.verify.call_count, 1)
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_absent_service_fails_closed(self):
        self.app.state.auth_protection = None
        self.assertEqual((await self.post())[0], 503)
        self.collection.find_one.assert_not_awaited()
        self.hasher.verify.assert_not_called()


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_independent_apps_and_reset(self):
        a, b = FastAPI(), FastAPI()
        with patch('auth_protection.load_auth_protection_settings', return_value=AuthProtectionSettings()):
            async with auth_protection_lifespan(a), auth_protection_lifespan(b):
                first = a.state.auth_protection
                self.assertIsNot(first, b.state.auth_protection)
                for _ in range(3): first.limiter.check('register','127.0.0.1')
                self.assertFalse(first.limiter.check('register','127.0.0.1').allowed)
                self.assertTrue(b.state.auth_protection.limiter.check('register','127.0.0.1').allowed)
            self.assertIsNone(a.state.auth_protection)
            async with auth_protection_lifespan(a):
                self.assertIsNot(first, a.state.auth_protection)
                self.assertTrue(a.state.auth_protection.limiter.check('register','127.0.0.1').allowed)

    async def test_bad_configuration_is_sanitized(self):
        app = FastAPI()
        with patch('auth_protection.load_auth_protection_settings', side_effect=ConfigurationError('secret')):
            with self.assertRaisesRegex(RuntimeError, '^Authentication protection initialization failed.$'):
                async with auth_protection_lifespan(app): self.fail('must not start')
        self.assertIsNone(app.state.auth_protection)

    async def test_cancelled_lifespan_shutdown_drains_before_database_cleanup(self):
        app = FastAPI()
        entered, ending, worker_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release = threading.Event()
        cleaned = []
        loop = asyncio.get_running_loop()
        @asynccontextmanager
        async def database(app):
            try: yield
            finally: cleaned.append(True)
        def work():
            loop.call_soon_threadsafe(worker_started.set)
            if not release.wait(5): raise RuntimeError('synchronization failed')
        async def lifecycle():
            async with application_lifespan(app):
                entered.set(); await ending.wait()
        with patch('main.database_lifespan', database), patch('auth_protection.load_auth_protection_settings', return_value=AuthProtectionSettings()):
            task = asyncio.create_task(lifecycle())
            await asyncio.wait_for(entered.wait(), 5)
            service = app.state.auth_protection
            job = asyncio.create_task(service.passwords.run(work))
            try:
                await asyncio.wait_for(worker_started.wait(), 5)
                ending.set()
                # Checkpoints coordinate shutdown entry, not elapsed timing.
                while not service.closed: await asyncio.sleep(0)
                task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertFalse(cleaned)
                with self.assertRaises(PasswordCapacityExceeded): await service.passwords.run(work)
                release.set()
                with self.assertRaises(asyncio.CancelledError): await task
                await job
                self.assertEqual(cleaned, [True])
                self.assertIsNone(app.state.auth_protection)
            finally:
                release.set()
                await asyncio.gather(task, job, return_exceptions=True)
