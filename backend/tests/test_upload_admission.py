"""UPLOAD route admission with controlled local work and mocked databases."""
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from cpu_workers import CPUWorkers, get_cpu_workers
from expensive_admission import Resource
from main import app
from document_routes import router, parse_upload
from upload_lifecycle import OwnedParser
from multipart_cleanup import ParserCleanup
import test_documents as fixtures
from test_documents import request, PDF


class UploadAdmissionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown

    async def test_two_admitted_third_rejected_before_parser_and_mutation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def paused(*args):
            nonlocal count
            count += 1
            if count == 2:
                entered.set()
            await release.wait()
            return await parse_upload(*args)
        with patch('document_routes.parse_upload', side_effect=paused), \
                patch('document_routes.ParserCleanup', wraps=ParserCleanup) as parser:
            active = [asyncio.create_task(request('POST', self.path, PDF)) for _ in range(2)]
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 2)
                status, body, receives = await request('POST', self.path, PDF)
                self.assertEqual(status, 503)
                self.assertEqual(body, {'detail': 'Service is busy. Please try again later.'})
                self.assertEqual(receives, 0)
                self.assertEqual(count, 2)
                parser.assert_not_called()  # No spools allocated yet, including by rejection.
                self.assertFalse(self.docs.documents)
                self.assertNotIn('_document_ids', self.bases.documents[self.base_id])
                self.assertEqual(list(self.root.glob('*')), [])
            finally:
                release.set()
                results = await asyncio.gather(*active)
        self.assertEqual([r[0] for r in results], [201, 201])
        self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 0)

    async def test_success_and_validation_errors_release_capacity(self):
        for content, expected in ((PDF, 201), (b'%PDF-' + b'x' * 101, 413)):
            with self.subTest(expected=expected):
                self.assertEqual((await request('POST', self.path, content))[0], expected)
                self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 0)
        self.assertEqual((await request('POST', self.path, raw=b'broken multipart'))[0], 422)
        self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 0)

    async def test_native_finalization_retains_upload_after_request_cancellation(self):
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        lifetimes = []
        original = self.storage.save
        def save(upload, *args):
            value = original(upload, *args)
            lifetimes.append(upload._deepdocs_save_lifetime)
            close = upload.file.close
            def blocked_close():
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(5):
                    raise OSError('test timeout')
                close()
            upload.file.close = blocked_close
            return value
        with patch.object(self.storage, 'save', side_effect=save):
            task = asyncio.create_task(request('POST', self.path, PDF))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.admission.begin_shutdown()
            try:
                self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 1)
                self.assertFalse(self.admission._drained.done())
                self.assertFalse(lifetimes[0].finalized.is_set())
            finally:
                release.set()
            await asyncio.wait_for(self.admission.aclose(), 5)
            self.assertTrue(lifetimes[0].finalization_succeeded)
            self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 0)

    async def pending(self, application, entered):
        async def receive():
            entered.set()
            await asyncio.Future()
        async def send(message):
            pass
        await application({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                           'method': 'POST', 'scheme': 'http', 'path': self.path,
                           'raw_path': self.path.encode(), 'query_string': b'', 'root_path': '',
                           'headers': [(b'content-type', b'multipart/form-data; boundary=b')],
                           'server': ('test', 80), 'client': ('test', 123)}, receive, send)

    async def test_cooperative_abort_releases_after_cleanup_and_no_reservation(self):
        entered = asyncio.Event()
        task = asyncio.create_task(self.pending(app, entered))
        await entered.wait()
        self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(self.admission.aclose(), 5)
        self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 0)
        self.assertNotIn('_document_ids', self.bases.documents[self.base_id])

    async def test_uncertain_parser_retains_reserved_capacity(self):
        # Isolated service deliberately cannot drain; fixture service stays healthy.
        service = AdmissionService(ExpensiveAdmissionSettings())
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides.update(app.dependency_overrides)
        application.dependency_overrides[get_cpu_workers] = lambda: SimpleNamespace(workflow=service.workflow)
        parsers = []
        class ObservedParser(OwnedParser):
            def __init__(self, *args):
                super().__init__(*args)
                parsers.append(self)
        entered = asyncio.Event()
        with patch('document_routes.OwnedParser', ObservedParser):
            task = asyncio.create_task(self.pending(application, entered))
            await entered.wait()
            with self.assertLogs('upload_lifecycle', level='WARNING'):
                parsers[0].task.cancel()
                await asyncio.wait_for(task, 5)
        service.begin_shutdown()
        self.assertTrue(parsers[0].uncertain)
        self.assertEqual(service._admission.outstanding[Resource.UPLOAD], 1)
        self.assertFalse(service._drained.done())
        self.assertNotIn('_document_ids', self.bases.documents[self.base_id])

    async def test_independent_app_and_configured_capacity(self):
        service = AdmissionService(ExpensiveAdmissionSettings(upload_max_inflight=1))
        workers = CPUWorkers(service)
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides.update(app.dependency_overrides)
        application.dependency_overrides[get_cpu_workers] = lambda: workers
        held = [self.admission.workflow(Resource.UPLOAD) for _ in range(2)]
        entered = asyncio.Event()
        task = asyncio.create_task(self.pending(application, entered))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual(service._admission.outstanding[Resource.UPLOAD], 1)
            self.assertEqual(self.admission._admission.outstanding[Resource.UPLOAD], 2)
            self.assertEqual((await request('POST', self.path, PDF))[0], 503)
            from expensive_admission import CapacityExceeded
            with self.assertRaises(CapacityExceeded):
                service.workflow(Resource.UPLOAD)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            for workflow in held:
                workflow.finish()
            await service.aclose()
            await workers.aclose()
