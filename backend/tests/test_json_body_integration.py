"""Actual app boundary integration with deterministic ASGI streams, no services."""
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import APIRouter, Body, FastAPI, HTTPException, UploadFile, File
from json_body_boundary import JSONRouteSelector, RegisteredJSONBodyBoundary, DETAIL
from config import ConfigurationError
from database import get_database
from auth import get_current_user, get_auth_settings

LIMIT = 16384
ID = '0123456789abcdef01234567'
INVENTORY = [
    ('POST', '/api/auth/register'), ('POST', '/api/auth/login'),
    ('POST', '/api/knowledge-bases'), ('PATCH', '/api/knowledge-bases/{knowledge_base_id}'),
    *[('POST', '/api/knowledge-bases/{knowledge_base_id}/' + suffix) for suffix in
      ('documents/bulk-delete', 'documents/bulk-reindex', 'search', 'ask')],
]


def make_app():
    with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, {}, clear=True):
        app = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'main.py'))['app']
        with patch('json_body_boundary.load_json_body_settings') as settings:
            settings.return_value.max_body_bytes = LIMIT
            app.middleware_stack = app.build_middleware_stack()
    return app


async def request(app, path, body=b'', method='POST', headers=(), chunks=None, root_path=''):
    messages = []
    chunks = list(chunks if chunks is not None else [body])
    calls = 0
    async def receive():
        nonlocal calls
        calls += 1
        if not chunks:
            raise AssertionError('read after request was exhausted')
        chunk = chunks.pop(0)
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(chunks)}
    async def send(message):
        messages.append(message)
    await app({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
               'method': method, 'scheme': 'http', 'path': path, 'raw_path': path.encode(),
               'root_path': root_path, 'query_string': b'', 'headers': list(headers),
               'server': ('test', 80), 'client': ('127.0.0.1', 1)}, receive, send)
    starts = [m for m in messages if m['type'] == 'http.response.start']
    assert len(starts) == 1, messages
    return starts[0]['status'], dict(starts[0]['headers']), b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'), calls


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = make_app()
        self.work = Mock()
        async def blocked_dependency():
            self.work()
            raise HTTPException(418, 'mock dependency reached')
        for dependency in (get_database, get_current_user, get_auth_settings):
            self.app.dependency_overrides[dependency] = blocked_dependency

    async def test_inventory_and_every_oversized_endpoint_prevents_side_effects(self):
        actual = {(method, context.path) for context, protected in JSONRouteSelector(self.app.router).entries()
                  if protected for method in context.methods}
        self.assertEqual(actual, set(INVENTORY))
        with patch('users.hash_password') as hashing, patch('auth_routes.verify_password') as verify, \
             patch('auth_routes.create_access_token') as jwt, \
             patch('document_bulk.delete_documents') as delete, patch('document_bulk.reindex_documents') as reindex:
            for method, template in INVENTORY:
                with self.subTest(endpoint=template):
                    result = await request(self.app, template.replace('{knowledge_base_id}', ID), method=method,
                                           chunks=[b' ' * LIMIT, b'x', b'unread'])
                    self.assertEqual(result[0], 413)
                    self.assertEqual(json.loads(result[2]), {'detail': DETAIL})
                    self.assertEqual(result[3], 2)
            self.work.assert_not_called()
            hashing.assert_not_called()
            verify.assert_not_called()
            jwt.assert_not_called()
            delete.assert_not_called()
            reindex.assert_not_called()

    async def test_content_type_and_length_cannot_bypass(self):
        for media in (None, b'application/json', b'application/problem+json', b'text/plain', b'multipart/form-data'):
            for length in (None, b'1', b'invalid'):
                with self.subTest(media=media, length=length):
                    headers = [] if media is None else [(b'content-type', media)]
                    if length is not None:
                        headers.append((b'content-length', length))
                    result = await request(self.app, '/api/auth/login', b'{' + b'x'*LIMIT, headers=headers)
                    self.assertEqual(result[0], 413)
        self.work.assert_not_called()

    async def test_early_declared_rejection_and_cors(self):
        result = await request(self.app, '/api/auth/login', headers=[
            (b'content-length', str(LIMIT+1).encode()), (b'origin', b'http://localhost:5173')])
        self.assertEqual(result[0], 413)
        self.assertEqual(result[3], 0)
        self.assertEqual(result[1][b'cache-control'], b'no-store')
        self.assertEqual(result[1][b'access-control-allow-origin'], b'http://localhost:5173')
        self.work.assert_not_called()

    async def test_under_and_exact_limit_reach_existing_dependencies(self):
        for method, template in INVENTORY:
            for size in (2, LIMIT):
                with self.subTest(endpoint=template, size=size):
                    result = await request(self.app, template.replace('{knowledge_base_id}', ID),
                                           b'{}' + b' '*(size-2), method=method,
                                           headers=[(b'content-type', b'application/json')])
                    self.assertEqual(result[0], 418)
        self.assertGreater(self.work.call_count, 0)

    async def test_malformed_under_limit_remains_sanitized_422(self):
        result = await request(self.app, '/api/auth/login', b'{private-password', headers=[(b'content-type', b'application/json')])
        self.assertEqual(result[0], 422)
        self.assertNotIn(b'private-password', result[2])
        self.work.assert_not_called()

    async def test_multipart_bodyless_and_safe_methods_are_not_limited(self):
        selector = JSONRouteSelector(self.app.router)
        examples = [('POST', f'/api/knowledge-bases/{ID}/documents'),
                    ('POST', f'/api/documents/{ID}/process'), ('POST', f'/api/documents/{ID}/embeddings'),
                    ('GET', '/api/health'), ('HEAD', '/api/health')]
        for method, path in examples:
            with self.subTest(path=path, method=method):
                self.assertFalse(selector({'type': 'http', 'method': method, 'path': path, 'root_path': ''}))
                result = await request(self.app, path, b'x'*(LIMIT+1), method=method,
                                       headers=[(b'content-length', str(LIMIT+1).encode())])
                self.assertIn(result[0], (200, 405, 418))
        result = await request(self.app, '/api/auth/login', method='OPTIONS', headers=[
            (b'origin', b'http://localhost:5173'), (b'access-control-request-method', b'POST'),
            (b'content-length', str(LIMIT+1).encode())])
        self.assertEqual(result[0], 200)

    async def test_concurrent_actual_app_requests_are_isolated(self):
        good, bad = await asyncio.gather(
            request(self.app, '/api/auth/login', b'{}', headers=[(b'content-type', b'application/json')]),
            request(self.app, '/api/auth/login', b'x'*(LIMIT+1)))
        self.assertEqual(good[0], 418)
        self.assertEqual(bad[0], 413)

    async def test_future_hidden_nested_route_and_late_registration_protected(self):
        nested = APIRouter(prefix='/nested')
        @nested.post('/{key}', include_in_schema=False)
        async def echo(key: str, body: dict = Body()):
            return body
        parent = APIRouter(prefix='/future')
        parent.include_router(nested)
        self.app.include_router(parent)  # Even after middleware was built.
        result = await request(self.app, '/future/nested/123', b'x'*(LIMIT+1))
        self.assertEqual(result[0], 413)
        result = await request(self.app, '/future/nested/123', b'{"ok":true}' + b' '*(LIMIT-11),
                               headers=[(b'content-type', b'application/json')])
        self.assertEqual(result[0], 200)
        self.assertEqual(json.loads(result[2]), {'ok': True})
        result = await request(self.app, '/prefix/future/nested/123', b'x'*(LIMIT+1), root_path='/prefix')
        self.assertEqual(result[0], 413)

    async def test_route_order_and_declared_forms(self):
        app = FastAPI()
        @app.post('/same')
        async def bodyless():
            return None
        @app.post('/same')
        async def shadow(body: dict):
            return body
        @app.post('/form')
        async def form(file: UploadFile = File()):
            return None
        selector = JSONRouteSelector(app.router)
        for path in ('/same', '/form'):
            self.assertFalse(selector({'type': 'http', 'path': path, 'method': 'POST', 'root_path': ''}))

    async def test_unsupported_metadata_and_invalid_config_fail_before_lifespan(self):
        app = FastAPI()
        app.mount('/unknown', FastAPI())
        with self.assertRaisesRegex(RuntimeError, 'route metadata is unsupported'):
            JSONRouteSelector(app.router)
        with patch('config.dotenv_values', return_value={'JSON_MAX_BODY_BYTES': 'private'}), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, '^Invalid JSON request body configuration.$'):
                RegisteredJSONBodyBoundary(self.app, router=self.app.router)
