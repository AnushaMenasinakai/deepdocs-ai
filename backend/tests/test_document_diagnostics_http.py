"""Real loopback HTTP with fake dependency probes and owner-scoped collections."""
import asyncio
import json
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import uvicorn
from fastapi import Header, HTTPException
from auth import get_current_user
from dependency_diagnostics import DependencyDiagnostics, get_diagnostics
from expensive_admission import Resource
from main import app
import test_documents as fixtures


class DocumentHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown
    upload = fixtures.DocumentTests.upload

    async def test_real_http_document_busy_and_coalesced_diagnostics(self):
        identifier = (await self.upload())['id']
        identity = app.dependency_overrides[get_current_user]
        async def authenticated(authorization: str | None = Header(None)):
            if authorization != 'Bearer test-only':
                raise HTTPException(401, 'Not authenticated')
            return identity()
        app.dependency_overrides[get_current_user] = authenticated
        joined = asyncio.Event()
        class ObservedDiagnostics(DependencyDiagnostics):
            async def run(self, name, probe):
                if name in self._tasks and not self._tasks[name].done():
                    joined.set()
                return await super().run(name, probe)
        diagnostics = ObservedDiagnostics()
        app.dependency_overrides[get_diagnostics] = lambda: diagnostics
        ready = asyncio.Event()
        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()
        server = Server(uvicorn.Config(app, host='127.0.0.1', port=0,
                                      lifespan='off', proxy_headers=False, log_level='critical'))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        def http(method, path, data=None, authorized=True):
            headers = {'Content-Type': 'application/json'}
            if authorized:
                headers['Authorization'] = 'Bearer test-only'
            req = Request(f'http://127.0.0.1:{port}' + path, method=method,
                          data=None if data is None else json.dumps(data).encode(), headers=headers)
            try:
                response = urlopen(req, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None, dict(response.headers)
        running = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            with self.admission.workflow(Resource.DOCUMENT):
                routes = [('DELETE', f'/api/documents/{identifier}', None),
                          ('POST', f'/api/documents/{identifier}/embeddings', None),
                          ('GET', f'/api/documents/{identifier}/vector-status', None),
                          ('POST', self.path + '/bulk-delete', {'document_ids': [identifier]}),
                          ('POST', self.path + '/bulk-reindex', {'document_ids': [identifier]})]
                for method, path, data in routes:
                    status, body, headers = await asyncio.to_thread(http, method, path, data)
                    self.assertEqual(status, 503)
                    self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
                    self.assertEqual(headers['retry-after'], '1')
                    self.assertEqual(headers['cache-control'], 'no-store')
                    self.assertEqual((await asyncio.to_thread(http, method, path, data, False))[0], 401)
                    self.current_owner = self.other
                    self.assertEqual((await asyncio.to_thread(http, method, path, data))[0], 404)
                    self.current_owner = self.owner
                self.assertEqual((await asyncio.to_thread(http, 'GET', '/api/health'))[0], 200)
            for name in ('database', 'qdrant'):
                joined.clear()
                entered, release = asyncio.Event(), asyncio.Event()
                calls = 0
                async def probe(*args):
                    nonlocal calls
                    calls += 1
                    entered.set()
                    await release.wait()
                    return True
                with patch('main.ping_database', side_effect=probe), \
                        patch('document_routes.get_vector_store', return_value=SimpleNamespace(health=probe)):
                    first = asyncio.create_task(asyncio.to_thread(http, 'GET', '/api/health/' + name))
                    await entered.wait()
                    second = asyncio.create_task(asyncio.to_thread(http, 'GET', '/api/health/' + name))
                    await asyncio.wait_for(joined.wait(), 5)
                    # Cancelling another waiter must not cancel either HTTP caller.
                    waiter = asyncio.create_task(diagnostics.run(name, probe))
                    await asyncio.sleep(0)
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    self.assertEqual(calls, 1)
                    self.assertEqual((await asyncio.to_thread(http, 'GET', '/api/health'))[0], 200)
                    release.set()
                    self.assertEqual((await first)[0], 200)
                    self.assertEqual((await second)[0], 200)
                    self.assertEqual((await asyncio.to_thread(http, 'GET', '/api/health/' + name))[0], 200)
                    self.assertEqual(calls, 2)
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, 10)
            sock.close()
            await diagnostics.close()
