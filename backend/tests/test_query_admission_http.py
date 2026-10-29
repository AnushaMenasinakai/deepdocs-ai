"""Real loopback HTTP boundary with isolated app and mocked external services."""
import asyncio
import json
import socket
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import AsyncMock, patch

from bson import ObjectId
from fastapi import FastAPI, Header, HTTPException
import uvicorn
from admission_lifecycle import AdmissionService
from auth import get_current_user
from config import ExpensiveAdmissionSettings, EmbeddingSettings, RAGSettings
from cpu_workers import CPUWorkers, get_cpu_workers
from database import get_database
from expensive_admission import Resource
from main import health
from search_routes import router as search_router, search_settings
from rag_routes import router as ask_router, rag_settings


class QueryHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_saturation_precedence_and_liveness(self):
        application = FastAPI()
        application.include_router(search_router)
        application.include_router(ask_router)
        application.get('/api/health')(health)
        owner, base = ObjectId(), ObjectId()
        admission = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(admission)
        async def identity(authorization: str | None = Header(None)):
            if authorization != 'Bearer test-only':
                raise HTTPException(401, 'Not authenticated')
            return SimpleNamespace(id=str(owner))
        application.dependency_overrides.update({
            get_current_user: identity, get_database: lambda: object(),
            get_cpu_workers: lambda: workers, search_settings: lambda: EmbeddingSettings(),
            rag_settings: lambda: RAGSettings()})
        async def owned(database, user, identifier):
            return {'_id': base} if identifier == base else None
        ready = asyncio.Event()
        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()
        server = Server(uvicorn.Config(application, host='127.0.0.1', port=0,
                                       proxy_headers=False, lifespan='off', log_level='error'))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        def http(path, payload=None, authenticated=True):
            headers = {'Content-Type': 'application/json'}
            if authenticated:
                headers['Authorization'] = 'Bearer test-only'
            req = Request(url + path, data=None if payload is None else json.dumps(payload).encode(),
                          headers=headers)
            try:
                response = urlopen(req, timeout=10)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response), dict(response.headers)
        async def call(kind, identifier=base, authenticated=True):
            return await asyncio.to_thread(http, f'/api/knowledge-bases/{identifier}/{kind}',
                                           {'query' if kind == 'search' else 'question': 'question'}, authenticated)
        running = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            await asyncio.wait_for(ready.wait(), 5)
            with patch('knowledge_bases.get_knowledge_base', side_effect=owned), \
                 patch('ask_history.save_history', AsyncMock(return_value=True)) as save, \
                 patch('rag.get_gemini_provider') as provider, patch('retrieval.query_vector') as encode:
                for kind in ('search', 'ask'):
                    entered, release = asyncio.Event(), asyncio.Event()
                    count = 0
                    async def blocked(*args, **kwargs):
                        nonlocal count
                        count += 1
                        if count == 2:
                            entered.set()
                        await release.wait()
                        return []
                    target = 'search_routes.search_chunks' if kind == 'search' else 'retrieval.search_chunks'
                    with patch(target, side_effect=blocked):
                        active = [asyncio.create_task(call(kind)) for _ in range(2)]
                        try:
                            await asyncio.wait_for(entered.wait(), 5)
                            before = save.await_count
                            for rejected in ('search', 'ask'):
                                status, body, headers = await call(rejected)
                                self.assertEqual(status, 503)
                                self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
                                self.assertEqual(headers['retry-after'], '1')
                                self.assertEqual(headers['cache-control'], 'no-store')
                                self.assertEqual((await call(rejected, authenticated=False))[0], 401)
                                self.assertEqual((await call(rejected, identifier=ObjectId()))[0], 404)
                            self.assertEqual(count, 2)
                            self.assertEqual(save.await_count, before)
                            self.assertEqual((await asyncio.to_thread(http, '/api/health'))[0], 200)
                            provider.assert_not_called()
                            encode.assert_not_called()
                        finally:
                            release.set()
                            results = await asyncio.gather(*active)
                        self.assertTrue(all(result[0] == 200 for result in results))
                    self.assertFalse(any(admission._admission.outstanding.values()))
                # Gemini-only saturation is independent of available QUERY slots.
                held = [admission.workflow(Resource.GEMINI) for _ in range(2)]
                try:
                    self.assertEqual((await call('ask'))[0], 503)
                    self.assertEqual(admission._admission.outstanding[Resource.QUERY], 0)
                finally:
                    for workflow in held:
                        workflow.finish()
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, 10)
            sock.close()
            await admission.aclose()
            await workers.aclose()
