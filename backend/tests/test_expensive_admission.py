"""Deterministic admission tests with controlled futures, no native workloads."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (ConfigurationError, ExpensiveAdmissionSettings,
                    EXPENSIVE_ADMISSION_BOUNDS, load_expensive_admission_settings)
from expensive_admission import Admission, Resource, CapacityExceeded, capacity_unavailable


class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_precedence(self):
        with patch('config.dotenv_values', return_value={}) as read, patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_expensive_admission_settings(), ExpensiveAdmissionSettings())
            self.assertFalse(read.call_args.kwargs['interpolate'])
        for name in EXPENSIVE_ADMISSION_BOUNDS:
            with self.subTest(name=name), patch('config.dotenv_values', return_value={name.upper(): '1'}):
                with patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(getattr(load_expensive_admission_settings(), name), 1)
                with patch.dict(os.environ, {name.upper(): ' 2 '}, clear=True):
                    self.assertEqual(getattr(load_expensive_admission_settings(), name), 2)

    def test_boundaries_and_invalid_values(self):
        for name, (low, high) in EXPENSIVE_ADMISSION_BOUNDS.items():
            for value in (str(low), str(high)):
                with self.subTest(name=name, value=value), patch('config.dotenv_values', return_value={name.upper(): value}), patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(getattr(load_expensive_admission_settings(), name), int(value))
            for value in ('', ' ', 'secret', '1.5', '0', '-1', str(high+1), 'true', '+1', '１'):
                with self.subTest(name=name, value=value), patch('config.dotenv_values', return_value={name.upper(): '1'}), patch.dict(os.environ, {name.upper(): value}, clear=True):
                    with self.assertRaisesRegex(ConfigurationError, '^Invalid expensive operation admission configuration.$'):
                        load_expensive_admission_settings()
            for value in (True, 1.0, None, 0, high+1):
                with self.subTest(name=name, value=value), self.assertRaises(ConfigurationError):
                    replace(ExpensiveAdmissionSettings(), **{name:value})

    def test_read_and_valueless_errors_are_sanitized(self):
        for error in (OSError('secret-path'), UnicodeError('private')):
            with self.subTest(error=type(error)), patch('config.dotenv_values', side_effect=error):
                with self.assertRaisesRegex(ConfigurationError, '^Invalid expensive operation admission configuration.$'):
                    load_expensive_admission_settings()
        with patch('config.dotenv_values', return_value={'UPLOAD_MAX_INFLIGHT':None}), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError): load_expensive_admission_settings()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.gate = Admission(ExpensiveAdmissionSettings())

    def test_each_capacity_boundary_and_recovery(self):
        for resource, limit in zip(Resource, (2,1,2,1,2)):
            with self.subTest(resource=resource):
                leases = [self.gate.acquire(resource) for _ in range(limit)]
                with self.assertRaises(CapacityExceeded): self.gate.acquire(resource)
                leases[0].release()
                renewed = self.gate.acquire(resource)
                for lease in leases: lease.release(); lease.release()
                self.assertEqual(self.gate.outstanding[resource], 1)
                renewed.release()
                self.assertEqual(self.gate.outstanding[resource], 0)

    def test_atomic_failure_does_not_retain_other_resources(self):
        held = self.gate.acquire(Resource.CPU)
        before = self.gate.outstanding
        for names in ((Resource.QUERY,Resource.CPU), (Resource.CPU,Resource.QUERY)):
            with self.assertRaises(CapacityExceeded): self.gate.acquire(*names)
            self.assertEqual(self.gate.outstanding, before)
        held.release()
        with self.gate.acquire(Resource.CPU,Resource.QUERY):
            self.assertEqual(self.gate.outstanding[Resource.QUERY], 1)
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_split_preserves_atomic_accounting_and_independent_lifetimes(self):
        workflow = self.gate.acquire(Resource.DOCUMENT,Resource.CPU)
        before = self.gate.outstanding
        cpu = workflow.split(Resource.CPU)
        self.assertEqual(self.gate.outstanding, before)
        cpu.release()
        self.assertEqual(self.gate.outstanding[Resource.DOCUMENT], 1)
        with self.gate.acquire(Resource.CPU): pass
        workflow.release(); cpu.release()
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_invalid_names_duplicates_and_split_cannot_corrupt_state(self):
        for names in ((), ('cpu',), (Resource.CPU,Resource.CPU)):
            with self.assertRaises(ValueError): self.gate.acquire(*names)
        reservation = self.gate.acquire(Resource.CPU)
        with self.assertRaises(ValueError): reservation.split(Resource.QUERY)
        child = reservation.split(Resource.CPU)
        reservation.release()
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)
        with self.assertRaises(ValueError): reservation.split(Resource.CPU)
        child.release()
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_concurrent_atomic_admission_and_reverse_order(self):
        barrier = threading.Barrier(16)
        finish = threading.Barrier(16)
        def attempt(index):
            barrier.wait(timeout=5)
            lease = None
            try:
                names = (Resource.CPU,Resource.QUERY) if index%2 else (Resource.QUERY,Resource.CPU)
                lease = self.gate.acquire(*names)
            except CapacityExceeded: pass
            finish.wait(timeout=5)  # All attempts finish before any release.
            if lease: lease.release()
            return lease is not None
        with ThreadPoolExecutor(max_workers=16) as threads:
            results = list(threads.map(attempt, range(16)))
        self.assertEqual(sum(results),1)
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_concurrent_double_release(self):
        reservation = self.gate.acquire(Resource.CPU)
        with ThreadPoolExecutor(max_workers=8) as threads:
            list(threads.map(lambda _:reservation.release(), range(32)))
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_instances_and_reservations_are_isolated(self):
        first = self.gate.acquire(Resource.QUERY)
        second = self.gate.acquire(Resource.QUERY)
        first.release(); first.release()
        self.assertEqual(self.gate.outstanding[Resource.QUERY],1)
        other = Admission(ExpensiveAdmissionSettings())
        with other.acquire(Resource.QUERY): self.assertEqual(self.gate.outstanding[Resource.QUERY],1)
        second.release()

    def test_shutdown_preserves_owned_capacity_and_rejects_dispatch(self):
        held = self.gate.acquire(Resource.CPU)
        self.gate.close(); self.gate.close()
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)
        with self.assertRaises(CapacityExceeded): self.gate.acquire(Resource.QUERY)
        submit = Mock()
        with self.assertRaises(CapacityExceeded): held.submit(submit)
        submit.assert_not_called()
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_submission_failure_and_completed_future(self):
        failed = Mock(side_effect=RuntimeError('private'))
        with self.assertRaises(RuntimeError): self.gate.acquire(Resource.CPU).submit(failed)
        failed.assert_called_once()
        self.assertFalse(any(self.gate.outstanding.values()))
        ready = Future(); ready.set_result(42)
        reservation = self.gate.acquire(Resource.CPU)
        self.assertIs(reservation.submit(lambda:ready),ready)
        reservation.release(); reservation.cancel()
        self.assertFalse(any(self.gate.outstanding.values()))

    def test_broken_submitter_fails_closed(self):
        reservation = self.gate.acquire(Resource.CPU)
        with self.assertRaises(TypeError): reservation.submit(lambda:None)
        reservation.release(); reservation.cancel(); self.gate.close()
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)

    def test_generic_http_contract(self):
        error = capacity_unavailable()
        self.assertEqual(error.status_code,503)
        self.assertEqual({'detail':error.detail},{'detail':'Service is busy. Please try again later.'})
        self.assertEqual(error.headers,{'Cache-Control':'no-store','Retry-After':'1'})
        self.assertEqual(str(CapacityExceeded()),'')


class WorkerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gate = Admission(ExpensiveAdmissionSettings())

    async def job(self, running=False, reservation=None):
        future = Future()
        if running: future.set_running_or_notify_cancel()
        submitted = asyncio.Event()
        def submit(): submitted.set(); return future
        reservation = reservation or self.gate.acquire(Resource.CPU)
        task = asyncio.create_task(reservation.run(submit))
        await submitted.wait()
        return task, future, reservation

    async def test_cancel_before_dispatch_context_releases(self):
        submit = Mock()
        async def request():
            with self.gate.acquire(Resource.CPU) as reservation:
                asyncio.current_task().cancel()
                await reservation.run(submit)
        task = asyncio.create_task(request())
        with self.assertRaises(asyncio.CancelledError): await task
        submit.assert_not_called()
        self.assertFalse(any(self.gate.outstanding.values()))

    async def test_cancellation_while_workflow_waits_before_submission(self):
        entered = asyncio.Event()
        async def request():
            with self.gate.acquire(Resource.DOCUMENT,Resource.CPU):
                entered.set()
                await asyncio.Future()
        task = asyncio.create_task(request()); await entered.wait(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(any(self.gate.outstanding.values()))

    async def test_queued_cancellation_releases_once(self):
        task,future,reservation = await self.job()
        task.cancel(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(future.cancelled())
        reservation.cancel(); reservation.release()
        self.assertFalse(any(self.gate.outstanding.values()))

    async def test_running_cancellation_and_shutdown_retain_until_completion(self):
        task,future,reservation = await self.job(running=True)
        task.cancel(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        reservation.release(); reservation.cancel()
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)
        with self.assertRaises(CapacityExceeded): self.gate.acquire(Resource.CPU)
        self.gate.close(); self.gate.close()
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)
        future.set_exception(ValueError('private worker error'))
        self.assertFalse(any(self.gate.outstanding.values()))
        with self.assertRaises(CapacityExceeded): self.gate.acquire(Resource.CPU)

    async def test_worker_completion_and_error_allow_new_admission(self):
        for error in (False,True):
            with self.subTest(error=error):
                task,future,reservation = await self.job(running=True)
                if error:
                    future.set_exception(ValueError('failed'))
                    with self.assertRaises(ValueError): await task
                else:
                    future.set_result(42)
                    self.assertEqual(await task,42)
                reservation.release()
                with self.gate.acquire(Resource.CPU): pass

    async def test_split_worker_can_outlive_workflow(self):
        workflow = self.gate.acquire(Resource.DOCUMENT,Resource.CPU)
        task,future,cpu = await self.job(running=True,reservation=workflow.split(Resource.CPU))
        workflow.release()
        self.assertEqual(self.gate.outstanding[Resource.DOCUMENT],0)
        self.assertEqual(self.gate.outstanding[Resource.CPU],1)
        future.set_result(None); await task
        self.assertFalse(any(self.gate.outstanding.values()))

    async def test_no_admission_tasks_waiters_or_retries(self):
        before = asyncio.all_tasks()
        held = self.gate.acquire(Resource.CPU)
        for _ in range(100):
            with self.assertRaises(CapacityExceeded): self.gate.acquire(Resource.CPU)
        self.assertEqual(asyncio.all_tasks(), before)
        held.release()
        self.assertFalse(any(self.gate.outstanding.values()))

    async def test_submission_failure_propagates_once(self):
        submit = Mock(side_effect=RuntimeError('failed'))
        with self.assertRaises(RuntimeError): await self.gate.acquire(Resource.CPU).run(submit)
        submit.assert_called_once()
        self.assertFalse(any(self.gate.outstanding.values()))
