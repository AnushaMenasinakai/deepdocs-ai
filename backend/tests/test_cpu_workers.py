"""Deterministic CPU foundation tests: no models, PDFs or services."""
import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from cpu_workers import CPUWorkers, Lane, SubmissionRejected, WorkerInfrastructureFailure, PDFParsingFailure
from expensive_admission import Resource, CapacityExceeded
from main import application_lifespan


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admission = AdmissionService(ExpensiveAdmissionSettings())
        self.workers = CPUWorkers(self.admission)
        self.workflow = self.admission.workflow(Resource.QUERY)

    async def asyncTearDown(self):
        self.workflow.finish()
        await self.admission.aclose()
        await self.workers.aclose()

    def session(self, lane=Lane.EMBEDDING):
        return self.workers.session(self.workflow, lane)

    async def test_repeated_batches_keep_capacity_and_same_thread(self):
        threads = []
        with self.session() as session:
            for index in range(3):
                self.assertEqual(await session.run(lambda n: (threads.append(threading.get_ident()), n)[1], index), index)
                self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                with self.assertRaises(CapacityExceeded):
                    self.session()
                with self.assertRaises(CapacityExceeded):
                    self.session(Lane.PDF)
            self.assertFalse(session._terminal.done())
        session.close()
        self.assertTrue(session._terminal.done())
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_running_cancel_retains_capacity_and_event_loop_progress(self):
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def work():
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise AssertionError('test synchronization failed')
            return 42
        session = self.session()
        task = asyncio.create_task(session.run(work))
        try:
            await asyncio.wait_for(started.wait(), 5)
            # This coroutine progresses while the fake native work is blocked.
            with self.assertRaises(CapacityExceeded):
                session.submit(lambda: None)
            self.assertEqual(self.workers._mailbox.qsize(), 0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(session._terminal.done())
            self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
        finally:
            release.set()
            await asyncio.wrap_future(session._terminal)
            await asyncio.gather(task, return_exceptions=True)

    async def test_predispatch_cancellation(self):
        called = []
        session = self.session()
        async def request():
            asyncio.current_task().cancel()
            await session.run(lambda: called.append(True))
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.create_task(request())
        self.assertEqual(called, [])
        self.assertTrue(session._terminal.done())

    async def test_cancelled_session_registration_does_not_acquire(self):
        async def request():
            asyncio.current_task().cancel()
            self.session()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.create_task(request())
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_worker_exception_and_stopiteration(self):
        with self.session() as session:
            def failure():
                raise ValueError('fake failure')
            with self.assertRaises(ValueError):
                await session.run(failure)
            with self.assertRaises(WorkerInfrastructureFailure):
                await session.run(lambda: next(iter(())))
            self.assertEqual(await session.run(lambda: 7), 7)

    async def test_known_submission_failure_releases(self):
        session = self.session()
        with patch.object(self.workers, '_submit_embedding', side_effect=SubmissionRejected()):
            with self.assertRaises(SubmissionRejected):
                session.submit(lambda: None)
        self.assertTrue(session._terminal.done())
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_pdf_interface_completion_and_duplicate_callback(self):
        session = self.session(Lane.PDF)
        native = Future()
        native.set_running_or_notify_cancel()
        result = session.submit_pdf(lambda: native)
        session.close()
        self.assertFalse(session._terminal.done())
        native.set_result(('page-count', []))
        self.assertEqual(result.result(), ('page-count', []))
        session._completed(native, result)
        session.close()
        self.assertTrue(session._terminal.done())
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 0)

    async def test_pdf_parsing_and_infrastructure_errors_are_distinct(self):
        for error in (PDFParsingFailure('sanitized parser failure'), WorkerInfrastructureFailure(), StopIteration()):
            with self.session(Lane.PDF) as session:
                native = Future()
                result = session.submit_pdf(lambda: native)
                native.set_exception(error)
                expected = WorkerInfrastructureFailure if isinstance(error, StopIteration) else type(error)
                with self.assertRaises(expected):
                    await asyncio.wrap_future(result)

    async def test_cancel_before_native_execution(self):
        native = Future()
        session = self.session()
        started = asyncio.Event()
        def submit(*args):
            started.set()
            return native
        with patch.object(self.workers, '_submit_embedding', side_effect=submit):
            task = asyncio.create_task(session.run(lambda: None))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(native.cancelled())
        self.assertTrue(session._terminal.done())

    async def test_shutdown_allows_existing_session_batches_only(self):
        session = self.session()
        self.admission.begin_shutdown()
        self.workers.begin_shutdown()
        with self.assertRaises(CapacityExceeded):
            self.session(Lane.PDF)
        self.assertEqual(await session.run(lambda: 1), 1)
        self.assertEqual(await session.run(lambda: 2), 2)
        session.close()
        with self.assertRaises(CapacityExceeded):
            session.submit(lambda: 3)

    async def test_repeated_worker_shutdown_cancellation_drains_and_joins(self):
        session = self.session(Lane.PDF)
        future = Future()
        future.set_running_or_notify_cancel()
        session.submit_pdf(lambda: future)
        session.close()
        started = asyncio.Event()
        async def shutdown():
            started.set()
            await self.workers.aclose()
        closing = asyncio.create_task(shutdown())
        await started.wait()
        for _ in range(3):
            closing.cancel()
            checkpoint = asyncio.Event()
            asyncio.get_running_loop().call_soon(checkpoint.set)
            await checkpoint.wait()
        self.assertFalse(closing.done())
        future.set_result(None)
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertFalse(self.workers._thread.is_alive())

    async def test_two_cpu_slots_still_one_job_per_lane_and_foreign_workflow_rejected(self):
        other = AdmissionService(ExpensiveAdmissionSettings(expensive_cpu_max_outstanding=2))
        workers = CPUWorkers(other)
        try:
            with other.workflow(Resource.QUERY) as workflow:
                with workers.session(workflow), workers.session(workflow, Lane.PDF):
                    self.assertEqual(other._admission.outstanding[Resource.CPU], 2)
                    for lane in Lane:
                        with self.assertRaises(CapacityExceeded):
                            workers.session(workflow, lane)
                with self.assertRaises(ValueError):
                    workers.session(self.workflow)
        finally:
            await other.aclose()
            await workers.aclose()

    async def test_unconfirmed_submission_retains_capacity(self):
        # Isolated service: intentionally unprovable dispatch has no safe drain.
        other = AdmissionService(ExpensiveAdmissionSettings())
        workers = CPUWorkers(other)
        with other.workflow(Resource.QUERY) as workflow:
            session = workers.session(workflow, Lane.PDF)
            with self.assertRaises(WorkerInfrastructureFailure):
                session.submit_pdf(lambda: object())
        session.close()
        other.begin_shutdown()
        self.assertEqual(other._admission.outstanding[Resource.CPU], 1)
        self.assertFalse(other._drained.done())
        # Only stop this test's unused embedding thread; never fake PDF completion.
        workers._mailbox.put_nowait(None)
        await asyncio.to_thread(workers._thread.join)


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolation_and_join_before_client_cleanup(self):
        clients_closed = []
        instances = []
        @asynccontextmanager
        async def database(app):
            try:
                yield
            finally:
                self.assertFalse(instances[-1]._thread.is_alive())
                clients_closed.append(True)
        @asynccontextmanager
        async def auth(app):
            yield
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            for _ in range(2):
                app = FastAPI()
                async with application_lifespan(app):
                    workers = app.state.cpu_workers
                    instances.append(workers)
                    with app.state.expensive_admission.workflow(Resource.QUERY) as workflow:
                        with workers.session(workflow) as session:
                            self.assertEqual(await session.run(lambda: 9), 9)
                self.assertIsNone(app.state.cpu_workers)
        self.assertIsNot(instances[0], instances[1])
        self.assertEqual(clients_closed, [True, True])

    async def test_cancelled_lifespan_keeps_clients_until_worker_finishes(self):
        entered = asyncio.Event()
        leave = asyncio.Event()
        release = threading.Event()
        closed = []
        app = FastAPI()
        @asynccontextmanager
        async def database(app):
            try:
                yield
            finally:
                self.assertFalse(workers._thread.is_alive())
                closed.append(True)
        @asynccontextmanager
        async def auth(app):
            yield
        def native():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError('test synchronization failed')
        loop = asyncio.get_running_loop()
        async def lifecycle():
            nonlocal workers
            async with application_lifespan(app):
                workers = app.state.cpu_workers
                with app.state.expensive_admission.workflow(Resource.QUERY) as workflow:
                    with workers.session(workflow) as session:
                        session.submit(native)
                        await leave.wait()
        workers = None
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            task = asyncio.create_task(lifecycle())
            try:
                await asyncio.wait_for(entered.wait(), 5)
                for _ in range(3):
                    task.cancel()
                    checkpoint = asyncio.Event()
                    loop.call_soon(checkpoint.set)
                    await checkpoint.wait()
                self.assertEqual(closed, [])
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(closed, [True])
