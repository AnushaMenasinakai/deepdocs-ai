"""Workflow authorization and real completion ownership; no external services."""
import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import patch
import threading
import unittest

from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from cpu_workers import CPUWorkers, Lane
from expensive_admission import Resource, CapacityExceeded
from fastapi import FastAPI
from main import application_lifespan


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = AdmissionService(ExpensiveAdmissionSettings())
        self.workers = CPUWorkers(self.service)
        self.workflow = self.service.workflow(Resource.QUERY, Resource.GEMINI)

    async def asyncTearDown(self):
        self.workflow.finish()
        await self.service.aclose()
        await self.workers.aclose()

    async def test_drain_authorizes_live_owner_without_false_idle(self):
        self.workers.begin_drain()
        with self.assertRaises(CapacityExceeded):
            self.service.workflow(Resource.QUERY)
        with self.assertRaises(CapacityExceeded):
            with self.workers.embedding_session():
                self.fail('unrelated session admitted')
        self.assertFalse(self.workers._idle_scheduled)
        with self.workers.session(self.workflow) as session:
            self.assertEqual(await session.run(lambda: 42), 42)
        self.assertFalse(self.service._drained.done())
        self.workflow.finish()
        await self.service.aclose()
        await self.workers.aclose()
        self.assertFalse(self.workers._thread.is_alive())

    async def test_invalid_completed_and_foreign_owners(self):
        with self.assertRaises(CapacityExceeded):
            self.workers.session(SimpleNamespace(_service=self.service))
        other = AdmissionService(ExpensiveAdmissionSettings())
        with other.workflow(Resource.QUERY) as foreign:
            with self.assertRaises(ValueError):
                self.workers.session(foreign)
        await other.aclose()
        self.workflow.finish()
        with self.assertRaises(CapacityExceeded):
            self.workers.session(self.workflow)

    async def test_pdf_completion_retains_query_and_gemini_after_cleanup(self):
        session = self.workers.session(self.workflow, Lane.PDF)
        native = Future()
        native.set_running_or_notify_cancel()
        result = session.submit_pdf(lambda: native)
        self.workflow.finish()
        self.workflow.finish()
        self.workers.begin_drain()
        self.assertEqual(self.service._active, 1)
        for resource in (Resource.QUERY, Resource.GEMINI, Resource.CPU):
            self.assertEqual(self.service._admission.outstanding[resource], 1)
        self.assertFalse(self.service._drained_scheduled)
        native.set_exception(ValueError('fake failure'))
        with self.assertRaises(ValueError):
            result.result()
        self.workflow._native_finished(session._terminal)  # duplicate callback
        self.assertEqual(self.service._active, 0)
        self.assertFalse(any(self.service._admission.outstanding.values()))

    async def test_cpu_release_is_independent_of_request_reservations(self):
        with self.workers.session(self.workflow) as session:
            await session.run(lambda: None)
        counts = self.service._admission.outstanding
        self.assertEqual(counts[Resource.CPU], 0)
        self.assertEqual(counts[Resource.QUERY], 1)
        self.assertEqual(counts[Resource.GEMINI], 1)

    async def test_request_cancellation_and_repeated_drain_cancellation(self):
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def native():
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(10):
                raise AssertionError('synchronization failed')
        async def request():
            with self.workflow:
                with self.workers.session(self.workflow) as session:
                    await session.run(native)
        task = asyncio.create_task(request())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.workers.begin_drain()
            closing = asyncio.create_task(self.service.aclose())
            for _ in range(3):
                checkpoint = asyncio.Event()
                loop.call_soon(checkpoint.set)
                await checkpoint.wait()
                closing.cancel()
            self.assertEqual(self.service._admission.outstanding[Resource.QUERY], 1)
            self.assertEqual(self.service._admission.outstanding[Resource.GEMINI], 1)
            self.assertFalse(self.service._drained_scheduled)
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertFalse(any(self.service._admission.outstanding.values()))

    async def test_unused_session_closes_on_workflow_cleanup(self):
        session = self.workers.session(self.workflow)
        self.workflow.finish()
        self.assertTrue(session._terminal.done())
        self.assertFalse(any(self.service._admission.outstanding.values()))

    async def test_final_seal_rejects_even_live_owner(self):
        self.workers.begin_shutdown()
        with self.assertRaises(CapacityExceeded):
            self.workers.session(self.workflow)

    async def test_session_before_drain_can_dispatch_sequential_batches(self):
        with self.workers.session(self.workflow) as session:
            self.workers.begin_drain()
            for value in range(3):
                self.assertEqual(await session.run(lambda x: x, value), value)

    async def test_lifespan_late_dispatch_before_client_close(self):
        closed = []
        @asynccontextmanager
        async def dependency(app):
            try:
                yield
            finally:
                self.assertFalse(workers._thread.is_alive())
                closed.append(True)
        app = FastAPI()
        with patch('main.database_lifespan', dependency), patch('main.auth_protection_lifespan', dependency):
            lifespan = application_lifespan(app)
            await lifespan.__aenter__()
            service = app.state.expensive_admission
            workers = app.state.cpu_workers
            workflow = service.workflow(Resource.QUERY, Resource.GEMINI)
            draining = asyncio.Event()
            original = workers.begin_drain
            def seal():
                original()
                draining.set()
            with patch.object(workers, 'begin_drain', seal):
                closing = asyncio.create_task(lifespan.__aexit__(None, None, None))
                await draining.wait()
                self.assertEqual(closed, [])
                with workers.session(workflow) as session:
                    self.assertEqual(await session.run(lambda: 7), 7)
                self.assertFalse(closing.done())
                workflow.finish()
                await closing
            self.assertEqual(closed, [True, True])

    async def test_dispatch_and_shutdown_share_atomic_lock(self):
        # Both legal serializations of the race permit the already-owned work.
        for shutdown_first in (False, True):
            with self.subTest(shutdown_first=shutdown_first):
                service = AdmissionService(ExpensiveAdmissionSettings())
                workers = CPUWorkers(service)
                workflow = service.workflow(Resource.QUERY)
                try:
                    if shutdown_first:
                        workers.begin_drain()
                    session = workers.session(workflow, Lane.PDF)
                    native = Future()
                    native.set_running_or_notify_cancel()
                    result = session.submit_pdf(lambda: native)
                    if not shutdown_first:
                        workers.begin_drain()
                    workflow.finish()
                    self.assertFalse(service._drained_scheduled)
                    native.set_result(None)
                    result.result()
                    await service.aclose()
                finally:
                    workflow.finish()
                    await workers.aclose()
