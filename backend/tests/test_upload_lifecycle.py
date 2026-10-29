"""Controlled native work and actual parser tests; no external services."""
import asyncio
import io
import threading
from types import SimpleNamespace
from contextlib import asynccontextmanager
import unittest
from unittest.mock import patch

from fastapi import HTTPException, FastAPI
from starlette.datastructures import Headers, FormData, UploadFile
from starlette.requests import ClientDisconnect
from starlette.concurrency import run_in_threadpool
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from expensive_admission import CapacityExceeded, Resource
from document_routes import parse_upload
from multipart_cleanup import ParserCleanup
from upload_lifecycle import OwnedParser
from upload_safety import SaveLifetime, close_upload_form, UploadUncertain
from test_multipart_cleanup import HEADER


class UploadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        return AdmissionService(ExpensiveAdmissionSettings())

    def request(self, stream):
        return SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}), stream=stream)

    async def terminal(self, owner):
        await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(owner._terminal)), 5)

    async def test_receive_abort_one_receiver_and_repeated_cancellation(self):
        service = self.service()
        entered = asyncio.Event()
        active = maximum = 0
        async def stream():
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                entered.set()
                await asyncio.Future()
                yield b''
            finally:
                active -= 1
        workflow = service.cleanup_workflow()
        parser = OwnedParser(workflow, self.request(stream), parse_upload, 100)
        waiter = asyncio.create_task(parser.result())
        await entered.wait()
        waiter.cancel()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        workflow.finish()
        await self.terminal(parser)
        await service.aclose()
        self.assertEqual((active, maximum), (0, 1))
        self.assertFalse(parser.task.cancelled())
        self.assertFalse(parser.uncertain)

    async def test_native_write_and_seek_survive_request_cancellation(self):
        for operation in ('write', 'seek'):
            with self.subTest(operation=operation):
                service = self.service()
                workflow = service.cleanup_workflow()
                loop = asyncio.get_running_loop()
                entered, release = asyncio.Event(), threading.Event()
                adapters = []
                def factory(*args, **kwargs):
                    adapter = ParserCleanup(*args, **kwargs)
                    adapters.append(adapter)
                    return adapter
                async def stream():
                    yield HEADER
                    spool = adapters[0].parser._files_to_close_on_error[0]
                    spool.rollover()
                    original = getattr(spool, operation)
                    def blocked(*args):
                        loop.call_soon_threadsafe(entered.set)
                        if not release.wait(5):
                            raise RuntimeError('test synchronization timeout')
                        self.assertFalse(spool.closed)
                        return original(*args)
                    setattr(spool, operation, blocked)
                    yield b'%PDF-data\r\n--b--\r\n'
                with patch('document_routes.ParserCleanup', side_effect=factory):
                    parser = OwnedParser(workflow, self.request(stream), parse_upload, 100)
                    waiter = asyncio.create_task(parser.result())
                    await asyncio.wait_for(entered.wait(), 5)
                    spool = adapters[0].parser._files_to_close_on_error[0]
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                    workflow.finish()
                    service.begin_shutdown()
                    try:
                        self.assertFalse(parser._terminal.done())
                        self.assertFalse(service._drained.done())
                        self.assertFalse(spool.closed)
                        self.assertEqual(parser.task.cancelling(), 0)
                    finally:
                        release.set()
                    await self.terminal(parser)
                    await service.aclose()
                    self.assertTrue(spool.closed)
                    self.assertFalse(parser.uncertain)

    async def test_disconnect_and_truncation_close_all_spools(self):
        for disconnect in (False, True):
            with self.subTest(disconnect=disconnect):
                service = self.service()
                workflow = service.cleanup_workflow()
                adapters = []
                def factory(*args, **kwargs):
                    adapter = ParserCleanup(*args, **kwargs)
                    adapters.append(adapter)
                    return adapter
                async def stream():
                    yield HEADER + b'%PDF-data'
                    if disconnect:
                        raise ClientDisconnect()
                with patch('document_routes.ParserCleanup', side_effect=factory):
                    parser = OwnedParser(workflow, self.request(stream), parse_upload, 100)
                    with self.assertRaises(ClientDisconnect if disconnect else HTTPException):
                        await parser.result()
                    workflow.finish()
                    await self.terminal(parser)
                await service.aclose()
                self.assertTrue(adapters[0].accounted)
                self.assertTrue(all(s.closed for s in adapters[0].streams.values()))

    async def test_completion_racing_cancellation_finalizes_unclaimed_form(self):
        service = self.service()
        workflow = service.cleanup_workflow()
        spool = io.BytesIO(b'pdf')
        release = asyncio.Event()
        async def parse(*args):
            await release.wait()
            asyncio.get_running_loop().call_soon(waiter.cancel)
            return FormData({'file': UploadFile(spool)})
        parser = OwnedParser(workflow, self.request(None), parse, 100)
        waiter = asyncio.create_task(parser.result())
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        workflow.finish()
        await self.terminal(parser)
        await service.aclose()
        self.assertFalse(parser.claimed)
        self.assertTrue(spool.closed)

    async def test_success_transfers_stream_without_upload_admission(self):
        service = self.service()
        workflow = service.cleanup_workflow()
        async def stream():
            yield HEADER + b'%PDF-data\r\n--b--\r\n'
        parser = OwnedParser(workflow, self.request(stream), parse_upload, 100)
        form = await parser.result()
        await self.terminal(parser)
        self.assertFalse(form['file'].file.closed)
        self.assertFalse(any(service._admission.outstanding.values()))
        lifetime = form['file']._deepdocs_save_lifetime
        await close_upload_form(form)
        workflow.finish()
        await service.aclose()
        self.assertTrue(lifetime.finalization_succeeded)
        self.assertFalse(lifetime.completed.is_set())  # No save was dispatched.

    async def test_direct_parser_cancellation_is_uncertain_not_successful_drain(self):
        service = self.service()
        workflow = service.cleanup_workflow()
        entered = asyncio.Event()
        async def stream():
            entered.set()
            await asyncio.Future()
            yield b''
        parser = OwnedParser(workflow, self.request(stream), parse_upload, 100)
        await entered.wait()
        with self.assertLogs('upload_lifecycle', level='WARNING') as logs:
            parser.task.cancel()
            await parser.task
        with self.assertRaises(UploadUncertain):
            await parser.result()
        workflow.finish()
        service.begin_shutdown()
        self.assertTrue(parser.uncertain)
        self.assertFalse(parser._terminal.done())
        self.assertFalse(service._drained.done())
        self.assertNotIn('Traceback', ''.join(logs.output))
        # Intentionally retained ownership; no native work or pending tasks in this fixture.

    async def test_save_native_completion_and_shutdown_cancellation(self):
        service = self.service()
        workflow = service.workflow(Resource.UPLOAD)
        lifetime = SaveLifetime(UploadFile(io.BytesIO(b'pdf')))
        workflow.own_cleanup(lifetime)
        lifetime.owned = True
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def save():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise RuntimeError('test timeout')
            self.assertFalse(lifetime.stream.closed)
        wrapper = asyncio.create_task(run_in_threadpool(lifetime.run, save))
        await entered.wait()
        wrapper.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await wrapper
        workflow.finish()
        shutdown = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        shutdown.cancel()
        try:
            self.assertFalse(lifetime.completed.is_set())
            self.assertFalse(lifetime._terminal.done())
            self.assertEqual(service._admission.outstanding[Resource.UPLOAD], 1)
            self.assertFalse(shutdown.done())
        finally:
            release.set()
        await self.terminal(lifetime)
        with self.assertRaises(asyncio.CancelledError):
            await shutdown
        self.assertTrue(lifetime.completed.is_set())
        self.assertTrue(lifetime.finalization_succeeded)
        self.assertFalse(any(service._admission.outstanding.values()))

    async def test_save_completion_distinct_from_blocked_stream_finalization(self):
        service = self.service()
        workflow = service.cleanup_workflow()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        class Stream(io.BytesIO):
            def close(self):
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(5):
                    raise OSError('test timeout')
                super().close()
        lifetime = SaveLifetime(UploadFile(Stream()))
        workflow.own_cleanup(lifetime)
        lifetime.run(lambda: None)
        finalizer = asyncio.create_task(lifetime.finalize())
        await entered.wait()
        finalizer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await finalizer
        workflow.finish()
        service.begin_shutdown()
        try:
            self.assertTrue(lifetime.completed.is_set())
            self.assertFalse(lifetime.finalized.is_set())
            self.assertFalse(service._drained.done())
        finally:
            release.set()
        await self.terminal(lifetime)
        await service.aclose()
        self.assertTrue(lifetime.finalization_succeeded)

    async def test_compensation_native_completion_survives_wrapper_cancellation(self):
        service = self.service()
        workflow = service.cleanup_workflow()
        lifetime = SaveLifetime(UploadFile(io.BytesIO()))
        workflow.own_cleanup(lifetime)
        lifetime.run(lambda: None)
        lifetime.prepare_compensation()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def delete():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise OSError('test timeout')
        wrapper = asyncio.create_task(run_in_threadpool(lifetime.compensate, delete))
        await entered.wait()
        wrapper.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await wrapper
        await lifetime.finalize()
        workflow.finish()
        service.begin_shutdown()
        try:
            self.assertTrue(lifetime.finalization_succeeded)
            self.assertFalse(lifetime.compensation_completed.is_set())
            self.assertFalse(service._drained.done())
        finally:
            release.set()
        await self.terminal(lifetime)
        await service.aclose()
        self.assertTrue(lifetime.compensation_completed.is_set())

    async def test_cleanup_failure_is_not_success_or_retried(self):
        class Stream(io.BytesIO):
            attempts = 0
            def close(self):
                self.attempts += 1
                raise OSError('private filesystem detail')
        stream = Stream()
        lifetime = SaveLifetime(UploadFile(stream))
        with self.assertLogs('upload_safety', level='WARNING') as logs:
            lifetime.close_when_finished()
        lifetime.close_when_finished()
        self.assertEqual(stream.attempts, 1)
        self.assertTrue(lifetime.finalized.is_set())
        self.assertFalse(lifetime.finalization_succeeded)
        self.assertFalse(lifetime._terminal.done())
        self.assertNotIn('private filesystem', ''.join(logs.output))
        io.BytesIO.close(stream)

    async def test_cleanup_isolation_and_shutdown_seal(self):
        first, second = self.service(), self.service()
        workflow = first.cleanup_workflow()
        first.begin_shutdown()
        with self.assertRaises(CapacityExceeded):
            first.cleanup_workflow()
        other = second.cleanup_workflow()
        self.assertFalse(first._drained.done())
        other.finish()
        await second.aclose()
        self.assertFalse(first._drained.done())
        workflow.finish()
        workflow.finish()
        await first.aclose()

    async def test_native_failures_are_preserved_and_completion_is_idempotent(self):
        lifetime = SaveLifetime(UploadFile(io.BytesIO()))
        failure = OSError('synthetic private detail')
        def broken():
            raise failure
        with self.assertRaises(OSError):
            lifetime.run(broken)
        self.assertIs(lifetime.save_error, failure)
        self.assertTrue(lifetime.completed.is_set())
        lifetime.prepare_compensation()
        with self.assertRaises(OSError):
            lifetime.compensate(broken)
        self.assertIs(lifetime.compensation_error, failure)
        self.assertTrue(lifetime.compensation_completed.is_set())
        lifetime.close_when_finished()
        lifetime.close_when_finished()
        self.assertTrue(lifetime._terminal.done())

    async def test_pre_dispatch_close_seals_native_save_and_compensation(self):
        lifetime = SaveLifetime(UploadFile(io.BytesIO()))
        lifetime.close_when_finished()
        with self.assertRaises(UploadUncertain):
            lifetime.run(lambda: self.fail('late save dispatched'))
        self.assertFalse(lifetime.completed.is_set())
        self.assertTrue(lifetime._terminal.done())
        other = SaveLifetime(UploadFile(io.BytesIO()))
        other.run(lambda: None)
        other.prepare_compensation()
        other.close_when_finished()
        with self.assertRaises(UploadUncertain):
            other.compensate(lambda: self.fail('late compensation dispatched'))
        self.assertEqual(other.compensation_state, 'sealed')

    async def test_application_keeps_clients_open_until_upload_finalization(self):
        from main import application_lifespan
        app = FastAPI()
        started, leave, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        events = []
        class Stream(io.BytesIO):
            def close(self):
                loop.call_soon_threadsafe(closing.set)
                if not release.wait(5):
                    raise OSError('test timeout')
                super().close()
        @asynccontextmanager
        async def database(app):
            try:
                yield
            finally:
                events.append('database closed')
        @asynccontextmanager
        async def auth(app):
            try:
                yield
            finally:
                events.append('auth closed')
        async def application():
            async with application_lifespan(app):
                with app.state.cpu_workers.cleanup_workflow() as workflow:
                    lifetime = SaveLifetime(UploadFile(Stream()))
                    workflow.own_cleanup(lifetime)
                started.set()
                await leave.wait()
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            task = asyncio.create_task(application())
            await started.wait()
            await closing.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            try:
                self.assertFalse(events)
                self.assertFalse(task.done())
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertEqual(events, ['auth closed', 'database closed'])
