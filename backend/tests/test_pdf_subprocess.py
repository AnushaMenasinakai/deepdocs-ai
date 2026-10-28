"""Real spawn with synthetic targets, plus deterministic infrastructure fakes."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import partial
import multiprocessing
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from config import ProcessingSettings, ExpensiveAdmissionSettings
from admission_lifecycle import AdmissionService
from expensive_admission import Resource, CapacityExceeded
from cpu_workers import CPUWorkers, Lane
from pdf_subprocess import PDFSupervisor, PDFInfrastructureFailure, PDFSubmissionRejected
from pdf_worker_protocol import ParsingFailure, FailureCode, MAX_FRAME_BYTES
from pdf_worker_child import validate_job, _FAILURES
from pdf_subprocess_helpers import synthetic_child


SETTINGS = asdict(ProcessingSettings())


def job(mode):
    # Synthetic target uses only basename; this path is never opened.
    return str(Path.cwd() / mode)


class SupervisorTests(unittest.TestCase):
    def supervisor(self, **kwargs):
        supervisor = PDFSupervisor(_target=synthetic_child, **kwargs)
        self.addCleanup(supervisor.close)
        return supervisor

    def test_spawn_success_unicode_large_transfer_and_import_isolation(self):
        supervisor = self.supervisor()
        self.assertEqual(supervisor._context.get_start_method(), 'spawn')
        for mode in ['success', 'large', 'imports']:
            with self.subTest(mode=mode):
                result = supervisor.submit(job(mode), SETTINGS).result(20)
                expected = '🙂Unicode\n' * 30000 if mode == 'large' else (
                    'isolated' if mode == 'imports' else 'synthetic')
                self.assertEqual(result, (1, [dict(chunk_index=0, text=expected, page_start=1,
                                                  page_end=1, character_count=len(expected))]))
        self.assertIsNone(supervisor._active)

    def test_failure_classification(self):
        supervisor = self.supervisor()
        self.assertEqual(supervisor.submit(job('failure'), SETTINGS).result(20),
                         ParsingFailure(FailureCode.DAMAGED))
        for mode in ['crash', 'crash_partial', 'nonzero', 'empty', 'partial',
                     'malformed', 'oversized', 'trailing']:
            with self.subTest(mode=mode):
                future = supervisor.submit(job(mode), SETTINGS)
                with self.assertRaises(PDFInfrastructureFailure) as caught:
                    future.result(20)
                self.assertEqual(str(caught.exception),
                                 'PDF worker completion could not be confirmed as successful.')
                self.assertIsNone(supervisor._active)

    def test_real_entry_with_mock_extraction_sanitizes_failures_and_environment(self):
        supervisor = self.supervisor()
        with patch.dict(os.environ, {'DEEPDOCS_TEST_SECRET': 'synthetic-only'}):
            self.assertEqual(supervisor.submit(job('entry_success'), SETTINGS).result(20)[1][0]['text'],
                             'entry')
            self.assertEqual(supervisor.submit(job('entry_failure'), SETTINGS).result(20),
                             ParsingFailure(FailureCode.DAMAGED))
            for mode in ['entry_unknown', 'entry_exception']:
                with self.subTest(mode=mode):
                    with self.assertRaises(PDFInfrastructureFailure):
                        supervisor.submit(job(mode), SETTINGS).result(20)
            self.assertEqual(os.environ['DEEPDOCS_TEST_SECRET'], 'synthetic-only')

    def test_terminal_is_not_completion_no_queue_or_cancel_or_restart(self):
        context = multiprocessing.get_context('spawn')
        entered, release = context.Event(), context.Event()
        supervisor = PDFSupervisor(_target=partial(synthetic_child, entered=entered, release=release))
        try:
            future = supervisor.submit(job('terminal_blocked'), SETTINGS)
            self.assertTrue(entered.wait(20))
            self.assertFalse(future.done())
            self.assertFalse(future.cancel())
            with self.assertRaises(PDFSubmissionRejected):
                supervisor.submit(job('success'), SETTINGS)
            self.assertEqual(supervisor._mailbox.qsize(), 0)
            release.set()
            self.assertEqual(future.result(20)[0], 1)
        finally:
            release.set()
            supervisor.close()
        with self.assertRaises(PDFSubmissionRejected):
            supervisor.submit(job('success'), SETTINGS)
        self.assertFalse(supervisor._thread.is_alive())

    def test_input_validation_rejects_before_process_construction(self):
        supervisor = self.supervisor()
        with patch.object(supervisor._context, 'Process') as process:
            for path, settings in [('relative.pdf', SETTINGS), (job('x') + '\0', SETTINGS),
                                   (job('x'), {**SETTINGS, 'credentials': 'not accepted'}),
                                   (job('x'), {**SETTINGS, 'chunk_overlap': True})]:
                with self.subTest(path_kind=type(path)):
                    with self.assertRaises(PDFSubmissionRejected):
                        supervisor.submit(path, settings)
            process.assert_not_called()
        self.assertEqual(set(_FAILURES.values()), set(FailureCode))
        with self.assertRaises(ValueError):
            validate_job(job('x'), SETTINGS, 99)

    def test_startup_failure_before_creation_finalizes_pipe(self):
        context = multiprocessing.get_context('spawn')
        reader, writer = context.Pipe(duplex=False)
        with patch.object(context, 'Pipe', return_value=(reader, writer)), \
                patch.object(context, 'Process', side_effect=OSError('private startup detail')):
            supervisor = self.supervisor(_context=context)
            with self.assertRaises(PDFInfrastructureFailure):
                supervisor.submit(job('x'), SETTINGS).result(5)
        self.assertTrue(reader.closed)
        self.assertTrue(writer.closed)

    def test_ambiguous_start_with_observable_child_is_drained_and_reaped(self):
        context = multiprocessing.get_context('spawn')
        process_factory = context.Process
        created = []
        class RaiseAfterStart:
            def __init__(self, process):
                self.process = process
            def __getattr__(self, name):
                return getattr(self.process, name)
            def start(self):
                self.process.start()
                raise OSError('failure after OS child creation')
        def factory(**kwargs):
            process = process_factory(**kwargs)
            created.append(process)
            return RaiseAfterStart(process)
        with patch.object(context, 'Process', side_effect=factory):
            supervisor = self.supervisor(_context=context)
            with self.assertRaises(PDFInfrastructureFailure):
                supervisor.submit(job('large'), SETTINGS).result(20)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]._closed)

    def test_unobservable_start_retains_ownership_without_false_completion(self):
        class UnknownProcess:
            _popen = None
            def start(self):
                raise OSError('unknown start outcome')
        context = multiprocessing.get_context('spawn')
        with patch.object(context, 'Process', return_value=UnknownProcess()) as factory:
            supervisor = PDFSupervisor(_context=context, _target=synthetic_child)
            future = supervisor.submit(job('x'), SETTINGS)
            self.assertTrue(supervisor._uncertainty.wait(5))
            self.assertFalse(future.done())
            self.assertFalse(future.cancel())
            with self.assertRaises(PDFSubmissionRejected):
                supervisor.submit(job('x'), SETTINGS)
            self.assertEqual(factory.call_count, 1)
        # Only this fake has no OS process. Stop its unused monitor and close
        # test pipe handles; NEVER fabricate completion of its pending Future.
        _, reader, writer = supervisor._uncertain
        reader.close()
        writer.close()
        supervisor._mailbox.put(None)
        supervisor._thread.join(5)
        self.assertFalse(supervisor._thread.is_alive())
        self.assertFalse(future.done())

    def test_broken_ipc_retains_until_join_and_closes_resources(self):
        joined = threading.Event()
        release = threading.Event()
        class Pipe:
            closed = False
            def recv_bytes(self, maximum):
                self.maximum = maximum
                raise OSError('private broken transport detail')
            def close(self):
                self.closed = True
        class Process:
            _popen = object()
            exitcode = None
            closed = False
            def start(self):
                pass
            def join(self):
                joined.set()
                if not release.wait(5):
                    raise AssertionError('synchronization failed')
                self.exitcode = 1
            def close(self):
                self.closed = True
        context = multiprocessing.get_context('spawn')
        reader, writer, process = Pipe(), Pipe(), Process()
        with patch.object(context, 'Pipe', return_value=(reader, writer)), \
                patch.object(context, 'Process', return_value=process):
            supervisor = self.supervisor(_context=context)
            future = supervisor.submit(job('x'), SETTINGS)
            try:
                self.assertTrue(joined.wait(5))
                self.assertFalse(future.done())
                self.assertEqual(reader.maximum, MAX_FRAME_BYTES)
                self.assertTrue(reader.closed)
                self.assertTrue(writer.closed)
            finally:
                release.set()
            with self.assertRaises(PDFInfrastructureFailure):
                future.result(5)
            self.assertTrue(process.closed)


class PDFSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admission = AdmissionService(ExpensiveAdmissionSettings())
        self.workers = CPUWorkers(self.admission)
        self.context = multiprocessing.get_context('spawn')
        self.entered, self.release = self.context.Event(), self.context.Event()
        self.workers._pdf_supervisor = PDFSupervisor(
            _target=partial(synthetic_child, entered=self.entered, release=self.release))
        self.workflow = self.admission.workflow(Resource.DOCUMENT)

    async def asyncTearDown(self):
        self.release.set()
        self.workflow.finish()
        await self.admission.aclose()
        await self.workers.aclose()

    async def test_cancellation_during_execution_and_transfer_retains_capacity(self):
        for mode in ['blocked', 'transfer']:
            with self.subTest(mode=mode):
                self.entered.clear()
                self.release.clear()
                session = self.workers.session(self.workflow, Lane.PDF)
                task = asyncio.create_task(session.run_pdf(job(mode), SETTINGS))
                try:
                    self.assertTrue(await asyncio.to_thread(self.entered.wait, 20))
                    # Event loop remains usable while child/native IPC waits.
                    marker = asyncio.Event()
                    asyncio.get_running_loop().call_soon(marker.set)
                    await asyncio.wait_for(marker.wait(), 2)
                    task.cancel()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertFalse(session._terminal.done())
                    self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                    for lane in Lane:
                        with self.assertRaises(CapacityExceeded):
                            self.workers.session(self.workflow, lane)
                finally:
                    self.release.set()
                    session.close()
                    await asyncio.wait_for(asyncio.wrap_future(session._terminal), 20)
                    await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_predispatch_cancellation_and_shutdown_dispatch_for_existing_session(self):
        session = self.workers.session(self.workflow, Lane.PDF)
        async def cancelled():
            asyncio.current_task().cancel()
            await session.run_pdf(job('success'), SETTINGS)
        with patch.object(self.workers._pdf_supervisor, 'submit') as submit:
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.create_task(cancelled())
            submit.assert_not_called()
        self.assertTrue(session._terminal.done())
        with self.workers.session(self.workflow, Lane.PDF) as session:
            self.admission.begin_shutdown()
            self.workers.begin_shutdown()
            with self.assertRaises(CapacityExceeded):
                self.workers.session(self.workflow, Lane.PDF)
            self.assertEqual((await session.run_pdf(job('success'), SETTINGS))[0], 1)

    async def test_shutdown_repeated_cancellation_waits_for_child_and_thread(self):
        session = self.workers.session(self.workflow, Lane.PDF)
        task = asyncio.create_task(session.run_pdf(job('terminal_blocked'), SETTINGS))
        closing = None
        try:
            self.assertTrue(await asyncio.to_thread(self.entered.wait, 20))
            session.close()
            self.workflow.finish()
            closing = asyncio.create_task(self.workers.aclose())
            for _ in range(3):
                marker = asyncio.Event()
                asyncio.get_running_loop().call_soon(marker.set)
                await marker.wait()
                closing.cancel()
            marker = asyncio.Event()
            asyncio.get_running_loop().call_soon(marker.set)
            await marker.wait()
            self.assertFalse(closing.done())
            self.assertFalse(session._terminal.done())
        finally:
            self.release.set()
            await task
            session.close()
            if closing is not None:
                with self.assertRaises(asyncio.CancelledError):
                    await closing
        self.assertFalse(self.workers._pdf_supervisor._thread.is_alive())

    async def test_per_application_supervisor_isolation(self):
        other = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(other)
        workers._pdf_supervisor = PDFSupervisor(_target=synthetic_child)
        try:
            self.assertIsNot(workers._pdf_supervisor, self.workers._pdf_supervisor)
            with other.workflow(Resource.DOCUMENT) as workflow:
                with workers.session(workflow, Lane.PDF) as session:
                    self.assertEqual((await session.run_pdf(job('success'), SETTINGS))[0], 1)
            self.assertIsNone(self.workers._pdf_supervisor._active)
        finally:
            await other.aclose()
            await workers.aclose()

    async def test_lifespan_keeps_clients_until_pdf_reaped_and_supervisor_joined(self):
        from fastapi import FastAPI
        from main import application_lifespan
        entered = self.context.Event()
        release = self.context.Event()
        closed = []
        workers = None
        @asynccontextmanager
        async def database(app):
            try:
                yield
            finally:
                self.assertFalse(workers._pdf_supervisor._thread.is_alive())
                self.assertFalse(workers._thread.is_alive())
                closed.append('database')
        @asynccontextmanager
        async def auth(app):
            try:
                yield
            finally:
                closed.append('auth')
        app = FastAPI()
        async def lifetime():
            nonlocal workers
            async with application_lifespan(app):
                workers = app.state.cpu_workers
                workers._pdf_supervisor = PDFSupervisor(
                    _target=partial(synthetic_child, entered=entered, release=release))
                with app.state.expensive_admission.workflow(Resource.DOCUMENT) as workflow:
                    with workers.session(workflow, Lane.PDF) as session:
                        await session.run_pdf(job('terminal_blocked'), SETTINGS)
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            task = asyncio.create_task(lifetime())
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 20))
                for _ in range(3):
                    task.cancel()
                    marker = asyncio.Event()
                    asyncio.get_running_loop().call_soon(marker.set)
                    await marker.wait()
                self.assertEqual(closed, [])
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(closed, ['auth', 'database'])
