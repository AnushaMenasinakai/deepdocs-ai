"""Loopback Uvicorn and production upload route, with in-memory providers."""
import asyncio
import json
import socket
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import uvicorn
from fastapi import Header, HTTPException
from auth import get_current_user
from main import app
from upload_lifecycle import OwnedParser
import test_documents as fixtures
from test_documents import PDF, PUBLIC
from test_multipart_cleanup import HEADER


class UploadHTTPTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown

    async def test_real_http_contract_disconnect_abort_and_shutdown(self):
        original_identity = app.dependency_overrides[get_current_user]
        async def identity(authorization: str | None = Header(None)):
            if authorization != 'Bearer offline-test':
                raise HTTPException(401, 'Not authenticated')
            return original_identity()
        app.dependency_overrides[get_current_user] = identity
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
        running = asyncio.create_task(server.serve(sockets=[sock]))
        parsers, owners = [], []
        entered = asyncio.Queue()
        class ObservedParser(OwnedParser):
            async def result(self):
                parsers.append(self)
                owners.append(asyncio.current_task())
                entered.put_nowait(self)
                return await super().result()
        def http(path, body=None, authenticated=True):
            headers = {'Content-Type': 'multipart/form-data; boundary=b'}
            if authenticated:
                headers['Authorization'] = 'Bearer offline-test'
            req = Request(f'http://127.0.0.1:{port}' + path, data=body, headers=headers)
            try:
                response = urlopen(req, timeout=5)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response)
        async def slow():
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write((f'POST {self.path} HTTP/1.1\r\nHost: localhost\r\n'
                          'Authorization: Bearer offline-test\r\n'
                          'Content-Type: multipart/form-data; boundary=b\r\n'
                          'Content-Length: 1000\r\n\r\n').encode() + HEADER + b'%PDF-partial')
            await writer.drain()
            parser = await asyncio.wait_for(entered.get(), 5)
            return reader, writer, parser
        try:
            await asyncio.wait_for(ready.wait(), 5)
            complete = HEADER + PDF + b'\r\n--b--\r\n'
            status, body = await asyncio.to_thread(http, self.path, complete)
            self.assertEqual(status, 201)
            self.assertEqual(set(body), PUBLIC)
            self.assertEqual((await asyncio.to_thread(http, self.path, complete, False))[0], 401)
            self.current_owner = self.other
            self.assertEqual((await asyncio.to_thread(http, self.path, complete))[0], 404)
            self.current_owner = self.owner
            self.assertEqual((await asyncio.to_thread(http, self.path, HEADER + b'%PDF-' + b'x' * 101 + b'\r\n--b--\r\n'))[0], 413)
            self.assertEqual((await asyncio.to_thread(http, self.path, HEADER + b'%PDF-truncated'))[0], 422)
            self.assertEqual((await asyncio.to_thread(http, self.path, b'not multipart'))[0], 422)
            before = len(self.docs.documents)
            reservations = list(self.bases.documents[self.base_id]['_document_ids'])
            revision = self.bases.documents[self.base_id]['_document_revision']
            with patch('document_routes.OwnedParser', ObservedParser):
                # A disconnected client aborts only the body receiver.
                reader, writer, parser = await slow()
                writer.close()
                await writer.wait_closed()
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(parser._terminal)), 5)
                self.assertFalse(parser.uncertain)
                # Explicit HTTP request cancellation must not cancel its parser.
                reader, writer, parser = await slow()
                owners[-1].cancel()
                owners[-1].cancel()
                await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(parser._terminal)), 5)
                writer.close()
                await writer.wait_closed()
                self.assertFalse(parser.uncertain)
                self.assertFalse(parser.task.cancelled())
                # Graceful drain waits for an active parser; liveness still serves.
                reader, writer, parser = await slow()
                self.admission.begin_shutdown()
                self.workers.begin_drain()
                shutdown = asyncio.create_task(self.admission.aclose())
                self.assertEqual((await asyncio.to_thread(http, '/api/health'))[0], 200)
                self.assertFalse(shutdown.done())
                writer.close()
                await writer.wait_closed()
                await asyncio.wait_for(shutdown, 5)
            self.assertEqual(len(self.docs.documents), before)
            self.assertEqual(self.bases.documents[self.base_id]['_document_ids'], reservations)
            self.assertEqual(self.bases.documents[self.base_id]['_document_revision'], revision)
            self.assertFalse(self.admission._owners)
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, 10)
            sock.close()
