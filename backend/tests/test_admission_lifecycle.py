"""Offline workflow ownership and application cleanup ordering tests."""
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
from config import ExpensiveAdmissionSettings, ConfigurationError
from admission_lifecycle import AdmissionService
from expensive_admission import Resource, CapacityExceeded
from main import application_lifespan


class CoordinationTests(unittest.IsolatedAsyncioTestCase):
    def service(self):
        return AdmissionService(ExpensiveAdmissionSettings())

    async def test_graceful_dispatch_and_duplicate_completion(self):
        service = self.service()
        workflow = service.workflow(Resource.DOCUMENT, Resource.CPU)
        cpu = workflow.reservation.split(Resource.CPU)
        service.begin_shutdown()
        with self.assertRaises(CapacityExceeded):
            service.workflow(Resource.QUERY)
        future = Future()
        future.set_running_or_notify_cancel()
        cpu.submit(lambda: future)  # admitted work may dispatch during drain
        workflow.finish()
        workflow.finish()
        self.assertEqual(service._admission.outstanding[Resource.CPU], 1)
        self.assertFalse(service._drained.done())
        future.set_result(None)
        await service.aclose()
        self.assertFalse(any(service._admission.outstanding.values()))
        self.assertTrue(service._admission._closed)

    async def test_cancel_before_registration(self):
        service = self.service()
        async def cancelled():
            asyncio.current_task().cancel()
            service.workflow(Resource.QUERY)
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.create_task(cancelled())
        self.assertEqual(service._active, 0)
        await service.aclose()

    async def test_cancel_registered_workflow_releases_split_held_slots(self):
        service = self.service()
        entered = asyncio.Event()
        async def request():
            with service.workflow(Resource.QUERY, Resource.CPU) as workflow:
                workflow.reservation.split(Resource.CPU)
                entered.set()
                await asyncio.Future()
        task = asyncio.create_task(request())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await service.aclose()
        self.assertFalse(any(service._admission.outstanding.values()))

    async def test_running_worker_survives_request_and_shutdown_cancellation(self):
        service = self.service()
        started = asyncio.Event()
        future = Future()
        future.set_running_or_notify_cancel()
        async def request():
            with service.workflow(Resource.CPU) as workflow:
                def submit():
                    started.set()
                    return future
                await workflow.reservation.run(submit)
        request_task = asyncio.create_task(request())
        await started.wait()
        request_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request_task
        service.begin_shutdown()
        shutdown = asyncio.create_task(service.aclose())
        # Event-loop barriers, not elapsed-time synchronization.
        for _ in range(3):
            barrier = asyncio.Event()
            asyncio.get_running_loop().call_soon(barrier.set)
            await barrier.wait()
            shutdown.cancel()
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        self.assertFalse(shutdown.done())
        self.assertEqual(service._admission.outstanding[Resource.CPU], 1)
        thread = threading.Thread(target=lambda: future.set_exception(ValueError('worker error')))
        thread.start()
        thread.join()
        with self.assertRaises(asyncio.CancelledError):
            await shutdown
        await service.aclose()
        self.assertFalse(any(service._admission.outstanding.values()))

    async def test_prestart_cancel_and_submission_failure(self):
        for failure in (False, True):
            service = self.service()
            with service.workflow(Resource.CPU) as workflow:
                if failure:
                    def submit():
                        raise ValueError('submission failed')
                    with self.assertRaises(ValueError):
                        workflow.reservation.submit(submit)
                else:
                    future = Future()
                    workflow.reservation.submit(lambda: future)
                    workflow.reservation.cancel()
                    self.assertTrue(future.cancelled())
            await service.aclose()
            self.assertFalse(any(service._admission.outstanding.values()))

    async def test_registration_shutdown_race_and_isolation(self):
        service = self.service()
        other = self.service()
        barrier = threading.Barrier(2)
        thread = threading.Thread(target=lambda: (barrier.wait(), service.begin_shutdown()))
        thread.start()
        barrier.wait()
        try:
            workflow = service.workflow(Resource.QUERY)
        except CapacityExceeded:
            workflow = None
        thread.join()
        if workflow is not None:
            workflow.finish()
        with self.assertRaises(CapacityExceeded):
            service.workflow(Resource.QUERY)
        with other.workflow(Resource.QUERY):
            pass
        await service.aclose()
        await other.aclose()

    async def test_bounded_workflow_registry_without_slots(self):
        service = self.service()
        workflows = []
        for _ in range(service._max_workflows):
            workflow = service.workflow(Resource.QUERY)
            workflow.reservation.release()
            workflows.append(workflow)
        with self.assertRaises(CapacityExceeded):
            service.workflow(Resource.QUERY)
        for workflow in workflows:
            workflow.finish()
        await service.aclose()

    async def test_completed_worker_does_not_end_workflow(self):
        service = self.service()
        workflow = service.workflow(Resource.CPU)
        future = Future()
        future.set_result(None)
        workflow.reservation.submit(lambda: future)
        service.begin_shutdown()
        self.assertFalse(service._drained.done())
        workflow.finish()
        await service.aclose()

    async def test_broken_submitter_retains_ownership(self):
        service = self.service()
        with service.workflow(Resource.CPU) as workflow:
            with self.assertRaises(TypeError):
                workflow.reservation.submit(lambda: object())
        service.begin_shutdown()
        self.assertFalse(service._drained.done())
        self.assertEqual(service._admission.outstanding[Resource.CPU], 1)
        # No aclose: this deliberately unprovable completion must not report drain.


class LifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_settings_before_database_startup(self):
        app = FastAPI()
        with patch('main.load_expensive_admission_settings', side_effect=ConfigurationError('Invalid expensive operation admission configuration.')), patch('main.database_lifespan') as database:
            with self.assertRaises(ConfigurationError):
                async with application_lifespan(app):
                    self.fail('startup should fail')
            database.assert_not_called()
        self.assertIsNone(app.state.expensive_admission)

    async def test_drain_precedes_auth_and_database_cleanup_with_cancellation(self):
        events = []
        app = FastAPI()
        entered = asyncio.Event()
        leave = asyncio.Event()
        future = Future()
        future.set_running_or_notify_cancel()
        @asynccontextmanager
        async def database(app):
            events.append('database start')
            try:
                yield
            finally:
                events.append('database close')
        @asynccontextmanager
        async def auth(app):
            events.append('auth start')
            try:
                yield
            finally:
                events.append('auth close')
        async def lifespan():
            async with application_lifespan(app):
                with app.state.expensive_admission.workflow(Resource.CPU) as workflow:
                    workflow.reservation.submit(lambda: future)
                entered.set()
                await leave.wait()
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            task = asyncio.create_task(lifespan())
            await entered.wait()
            task.cancel()
            for _ in range(3):
                barrier = asyncio.Event()
                asyncio.get_running_loop().call_soon(barrier.set)
                await barrier.wait()
                task.cancel()
            self.assertEqual(events, ['database start', 'auth start'])
            self.assertFalse(task.done())
            future.set_result(None)
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(events, ['database start', 'auth start', 'auth close', 'database close'])
        self.assertIsNone(app.state.expensive_admission)

    async def test_fresh_lifespans_and_startup_failure_cleanup(self):
        @asynccontextmanager
        async def dependency(app):
            yield
        app = FastAPI()
        with patch('main.database_lifespan', dependency), patch('main.auth_protection_lifespan', dependency):
            async with application_lifespan(app):
                first = app.state.expensive_admission
            async with application_lifespan(app):
                self.assertIsNot(first, app.state.expensive_admission)
        @asynccontextmanager
        async def failed(app):
            raise RuntimeError('startup failed')
            yield
        with patch('main.database_lifespan', failed):
            with self.assertRaisesRegex(RuntimeError, 'startup failed'):
                async with application_lifespan(app):
                    pass
        self.assertIsNone(app.state.expensive_admission)

    async def test_repeated_cancellation_during_client_cleanup(self):
        app = FastAPI()
        entered = asyncio.Event()
        leave = asyncio.Event()
        closing = asyncio.Event()
        release = asyncio.Event()
        closed = []
        @asynccontextmanager
        async def database(app):
            try:
                yield
            finally:
                closing.set()
                await release.wait()
                closed.append(True)
        @asynccontextmanager
        async def auth(app):
            yield
        async def lifespan():
            async with application_lifespan(app):
                entered.set()
                await leave.wait()
        with patch('main.database_lifespan', database), patch('main.auth_protection_lifespan', auth):
            task = asyncio.create_task(lifespan())
            await entered.wait()
            leave.set()
            await closing.wait()
            for _ in range(3):
                task.cancel()
                barrier = asyncio.Event()
                asyncio.get_running_loop().call_soon(barrier.set)
                await barrier.wait()
            self.assertFalse(task.done())
            self.assertEqual(closed, [])
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(closed, [True])
        self.assertIsNone(app.state.expensive_admission)

    async def test_configured_capacity_and_concurrent_app_isolation(self):
        @asynccontextmanager
        async def dependency(app):
            yield
        a, b = FastAPI(), FastAPI()
        settings = ExpensiveAdmissionSettings(query_max_inflight=1)
        with patch('main.database_lifespan', dependency), patch('main.auth_protection_lifespan', dependency), patch('main.load_expensive_admission_settings', return_value=settings):
            async with application_lifespan(a), application_lifespan(b):
                with a.state.expensive_admission.workflow(Resource.QUERY):
                    with self.assertRaises(CapacityExceeded):
                        a.state.expensive_admission.workflow(Resource.QUERY)
                    with b.state.expensive_admission.workflow(Resource.QUERY):
                        pass

class StageTests(unittest.IsolatedAsyncioTestCase):
    async def test_later_worker_stage_during_drain(self):
        service = AdmissionService(ExpensiveAdmissionSettings())
        with service.workflow(Resource.DOCUMENT) as workflow:
            service.begin_shutdown()
            for _ in range(2):
                cpu = workflow.acquire(Resource.CPU)
                with self.assertRaises(CapacityExceeded):
                    workflow.acquire(Resource.CPU)
                future = Future()
                cpu.submit(lambda: future)
                future.set_result(None)
            self.assertFalse(service._drained.done())
        with self.assertRaises(CapacityExceeded):
            workflow.acquire(Resource.CPU)
        await service.aclose()
        self.assertFalse(any(service._admission.outstanding.values()))
