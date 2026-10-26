"""Isolated boundary/configuration tests; no application lifespan or services."""
import asyncio
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ConfigurationError, JSONBodySettings, load_json_body_settings
from json_body_boundary import BodyLimitExceeded, DETAIL, JSONBodyBoundary
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.routing import compile_path


class ConfigurationTests(unittest.TestCase):
    def test_defaults_dotenv_precedence_and_boundaries(self):
        with patch('config.dotenv_values', return_value={}) as read, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_json_body_settings(), JSONBodySettings())
            self.assertFalse(read.call_args.kwargs['interpolate'])
        for value in ('16384', '1048576', ' 65536 '):
            with self.subTest(value=value), patch('config.dotenv_values', return_value={'JSON_MAX_BODY_BYTES': value}), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_json_body_settings().max_body_bytes, int(value))
        with patch('config.dotenv_values', return_value={'JSON_MAX_BODY_BYTES': '32768'}), patch.dict(os.environ, {'JSON_MAX_BODY_BYTES': '65536'}, clear=True):
            self.assertEqual(load_json_body_settings().max_body_bytes, 65536)

    def test_invalid_values_are_sanitized_including_blank_override(self):
        for value in ('', ' ', 'private-secret', '1.5', '-1', '0', '16383', '1048577', '+65536', '1e5', '６５５３６'):
            with self.subTest(value=value), patch('config.dotenv_values', return_value={'JSON_MAX_BODY_BYTES': '65536'}), patch.dict(os.environ, {'JSON_MAX_BODY_BYTES': value}, clear=True):
                with self.assertRaisesRegex(ConfigurationError, '^Invalid JSON request body configuration.$'):
                    load_json_body_settings()
        for value in (True, 65536.0, None, 0, 1048577):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                JSONBodySettings(value)

    def test_read_errors_and_valueless_dotenv(self):
        for error in (OSError('secret'), UnicodeError('secret')):
            with self.subTest(error=type(error)), patch('config.dotenv_values', side_effect=error):
                with self.assertRaisesRegex(ConfigurationError, '^Invalid JSON request body configuration.$'):
                    load_json_body_settings()
        with patch('config.dotenv_values', return_value={'JSON_MAX_BODY_BYTES': None}), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_json_body_settings()


def scope(headers=(), method='POST', path='/items/123'):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'method': method, 'path': path, 'raw_path': path.encode(), 'root_path': '',
            'scheme': 'http', 'query_string': b'', 'headers': list(headers),
            'client': ('127.0.0.1', 1), 'server': ('test', 80)}


def event(body, more=False):
    return {'type': 'http.request', 'body': body, 'more_body': more}


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, events, *, limit=8, headers=(), app=None, request_scope=None):
        seen, sent, calls = [], [], []
        async def receive():
            calls.append(True)
            self.assertLessEqual(len(calls), len(events), 'unexpected additional read')
            return events[len(calls)-1]
        async def send(message):
            sent.append(message)
        async def consume(s, r, w):
            while True:
                message = await r()
                seen.append(message)
                if message['type'] == 'http.disconnect':
                    return
                if not message.get('more_body', False):
                    break
            await w({'type': 'http.response.start', 'status': 204, 'headers': []})
            await w({'type': 'http.response.body', 'body': b''})
        await JSONBodyBoundary(app or consume, max_bytes=limit, select=lambda s: True)(
            request_scope or scope(headers), receive, send)
        return seen, sent, len(calls)

    def assert_rejected(self, sent):
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0]['status'], 413)
        self.assertIn((b'cache-control', b'no-store'), sent[0]['headers'])
        self.assertEqual(json.loads(sent[1]['body']), {'detail': DETAIL})

    async def test_below_exact_and_over_boundary(self):
        for size in (7, 8, 9, 10000):
            with self.subTest(size=size):
                message = event(b'x'*size)
                seen, sent, calls = await self.exercise([message])
                self.assertEqual(calls, 1)
                if size <= 8:
                    self.assertIs(seen[0], message)
                    self.assertEqual(sent[0]['status'], 204)
                else:
                    self.assertEqual(seen, [])
                    self.assert_rejected(sent)

    async def test_chunks_preserved_and_crossing_stops_reads(self):
        messages = [event(b'ab', True), event(b'', True), event(b'cdef', True), event(b'gh')]
        seen, sent, calls = await self.exercise(messages)
        self.assertEqual(calls, 4)
        for a, b in zip(seen, messages):
            self.assertIs(a, b)
        seen, sent, calls = await self.exercise([event(b'1234', True), event(b'56789', True), event(b'unread')])
        self.assertEqual(calls, 2)
        self.assertEqual(len(seen), 1)
        self.assert_rejected(sent)

    async def test_content_length_never_bypasses_actual_count(self):
        for values in ((), (b'0',), (b'1',), (b'8',), (b'-9',), (b'bad',), (b'9,9',), (b'1', b'1'), (b'99', b'1')):
            with self.subTest(values=values):
                _, sent, calls = await self.exercise([event(b'123456789')], headers=[(b'content-length', v) for v in values])
                self.assertEqual(calls, 1)
                self.assert_rejected(sent)

    async def test_declared_oversize_rejects_before_application_or_read(self):
        async def forbidden(*args):
            self.fail('downstream was invoked')
        for value in (b'9', b' 0009\t', b'9'*5000):
            with self.subTest(value_length=len(value)):
                _, sent, calls = await self.exercise([], headers=[(b'content-length', value)], app=forbidden)
                self.assertEqual(calls, 0)
                self.assert_rejected(sent)

    async def test_utf8_counts_bytes(self):
        _, sent, _ = await self.exercise([event('€€€'.encode())])
        self.assert_rejected(sent)

    async def test_unselected_scopes_and_safe_methods_are_unchanged(self):
        for s in (scope(method='GET'), scope(method='HEAD'), scope(method='OPTIONS'), {'type': 'lifespan'}, {'type': 'websocket'}, scope(path='/upload')):
            with self.subTest(scope=s):
                async def receive():
                    return event(b'x'*100)
                async def send(message):
                    pass
                async def app(given, r, w):
                    self.assertIs(given, s)
                    self.assertIs(r, receive)
                    self.assertIs(w, send)
                await JSONBodyBoundary(app, max_bytes=8, select=lambda s: s['path'] != '/upload')(s, receive, send)

    async def test_explicit_dynamic_matcher_without_scope_route(self):
        regex, _, _ = compile_path('/items/{item_id}')
        def select(s):
            return s['method'] == 'PATCH' and regex.fullmatch(s['path']) is not None
        for method, path, expected in [('PATCH', '/items/123', 413), ('POST', '/items/123', 204), ('PATCH', '/upload', 204)]:
            with self.subTest(method=method, path=path):
                async def app(s, r, w):
                    await r()
                    await w({'type': 'http.response.start', 'status': 204, 'headers': []})
                wrapped = JSONBodyBoundary(app, max_bytes=8, select=select)
                _, sent, _ = await self.exercise([event(b'x'*9)], limit=100, app=wrapped, request_scope=scope(method=method, path=path))
                self.assertEqual(sent[0]['status'], expected)

    async def test_disconnect_passed_through(self):
        disconnected = {'type': 'http.disconnect'}
        seen, sent, calls = await self.exercise([event(b'a', True), disconnected])
        self.assertIs(seen[-1], disconnected)
        self.assertEqual(sent, [])
        self.assertEqual(calls, 2)

    async def test_cancellation_propagates_without_response(self):
        entered = asyncio.Event()
        sent = []
        async def receive():
            entered.set()
            await asyncio.Future()
        async def send(m):
            sent.append(m)
        async def app(s, r, w):
            await r()
        task = asyncio.create_task(JSONBodyBoundary(app, max_bytes=8, select=lambda s: True)(scope(), receive, send))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sent, [])

    async def test_swallowed_rejection_cannot_resume_reads_or_send_success(self):
        async def app(s, r, w):
            for _ in range(3):
                with self.assertRaises(BodyLimitExceeded):
                    await r()
            await w({'type': 'http.response.start', 'status': 200, 'headers': []})
        _, sent, calls = await self.exercise([event(b'x'*9), event(b'unread')], app=app)
        self.assertEqual(calls, 1)
        self.assert_rejected(sent)

    async def test_swallowed_rejection_without_response_still_returns_413(self):
        async def app(s, r, w):
            with self.assertRaises(BodyLimitExceeded):
                await r()
        _, sent, _ = await self.exercise([event(b'x'*9)], app=app)
        self.assert_rejected(sent)

    async def test_started_response_is_not_replaced(self):
        sent = []
        async def receive():
            return event(b'x'*9)
        async def send(m):
            sent.append(m)
        async def app(s, r, w):
            await w({'type': 'http.response.start', 'status': 200, 'headers': []})
            await r()
        with self.assertRaises(BodyLimitExceeded):
            await JSONBodyBoundary(app, max_bytes=8, select=lambda s: True)(scope(), receive, send)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['status'], 200)

    async def test_concurrent_request_counters_are_independent(self):
        barrier = asyncio.Barrier(2)
        async def app(s, r, w):
            await r()
            await barrier.wait()
            await r()
            await w({'type': 'http.response.start', 'status': 204, 'headers': []})
        boundary = JSONBodyBoundary(app, max_bytes=8, select=lambda s: True)
        results = await asyncio.gather(self.exercise([event(b'1234', True), event(b'5678')], limit=100, app=boundary),
                                       self.exercise([event(b'1234', True), event(b'56789')], limit=100, app=boundary))
        self.assertEqual(results[0][1][0]['status'], 204)
        self.assert_rejected(results[1][1])

    async def test_installed_fastapi_stack_cannot_convert_rejection_to_400(self):
        class Payload(BaseModel):
            value: str
        app = FastAPI()
        invoked = []
        @app.post('/items/123')
        async def endpoint(payload: Payload):
            invoked.append(True)
            return {'ok': True}
        _, sent, _ = await self.exercise([event(b'{"value":"oversized"}')], headers=[(b'content-type', b'application/json')], app=app)
        self.assert_rejected(sent)
        self.assertEqual(invoked, [])

    async def test_no_accumulation_or_read_ahead(self):
        # Each chunk must reach downstream before the next receive is requested.
        acknowledged = True
        current = None
        count = 0
        async def receive():
            nonlocal acknowledged, current, count
            self.assertTrue(acknowledged)
            count += 1
            current = event(b'x', count < 100)
            acknowledged = False
            return current
        async def send(m):
            pass
        async def app(s, r, w):
            nonlocal acknowledged
            for _ in range(100):
                self.assertIs(await r(), current)
                acknowledged = True
        await JSONBodyBoundary(app, max_bytes=100, select=lambda s: True)(scope(), receive, send)
        self.assertEqual(count, 100)
