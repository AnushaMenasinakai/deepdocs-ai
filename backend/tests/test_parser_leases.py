import asyncio
from concurrent.futures import Future
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from parser_leases import ParserLeases
from cpu_workers import CPUWorkers, SubmissionRejected
from admission_lifecycle import AdmissionService
from config import ExpensiveAdmissionSettings
from expensive_admission import CapacityExceeded, Resource
from storage_contract import MaterializedPDF, StorageUncertain
from storage_workers import StorageWorkers, StorageRejected
from pdf_subprocess import PDFSubmissionRejected, PDFInfrastructureFailure


class Supervisor:
    def __init__(self):
        self.future = Future()
        self.future.set_running_or_notify_cancel()
        self.calls = 0
        self.failure = None
        self.closed = False
    def submit(self, path, settings):
        self.calls += 1
        if self.failure: raise self.failure
        return self.future
    def close(self): self.closed = True


class LeaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'synthetic.pdf'
        self.path.write_bytes(b'%PDF-synthetic')
        self.lease = MaterializedPDF(self.path, lambda:self.path.unlink())
        self.workers = StorageWorkers(Mock())
        self.supervisor = Supervisor()
        self.admission = AdmissionService(ExpensiveAdmissionSettings())
        self.cpu = CPUWorkers(self.admission)
        self.cpu._pdf_supervisor = self.supervisor
        self.owner = ParserLeases(self.workers, self.cpu)
    async def asyncTearDown(self):
        # Test-only disposal of deliberately unresolved doubles. Production never
        # forces these signals or releases ambiguous leases.
        for job in tuple(self.owner.jobs.values()):
            job.session._inflight = False
            job.session._release_parser_lease()
            job.context.__exit__(None, None, None)
        await self.admission.aclose()
        await self.cpu.aclose()
        await self.workers.aclose()
        self.temp.cleanup()
    async def dispatched(self):
        for _ in range(1000):
            if self.supervisor.calls: return
            await asyncio.sleep(.001)
        self.fail('No dispatch')
    def start(self):
        return asyncio.create_task(self.owner.parse(lambda:self.lease, {}))

    async def test_success_cleanup_before_result_and_shutdown(self):
        task=self.start(); await self.dispatched()
        self.assertTrue(self.path.exists())
        self.supervisor.future.set_result('parsed')
        self.assertEqual(await task,'parsed')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.owner.unresolved(),())
        await self.owner.aclose()
        self.assertFalse(self.supervisor.closed) # CPU owner closes supervisor later

    async def test_immediate_completion(self):
        self.supervisor.future.set_result('parsed')
        self.assertEqual(await self.owner.parse(lambda:self.lease,{}),'parsed')
        self.assertFalse(self.path.exists())

    async def test_rejection_before_dispatch(self):
        self.supervisor.failure=PDFSubmissionRejected()
        with self.assertRaises(SubmissionRejected): await self.start()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.owner.unresolved(),())

    async def test_cancel_before_admission(self):
        task=self.start(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.supervisor.calls,0)
        self.assertTrue(self.path.exists()) # never transferred to boundary

    async def test_cancel_during_materialization(self):
        entered,release=threading.Event(),threading.Event()
        def download():
            entered.set(); release.wait(5); return self.lease
        task=asyncio.create_task(self.owner.parse(download,{}))
        while not entered.is_set(): await asyncio.sleep(.001)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(self.path.exists())
        release.set()
        await self.owner.aclose()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.supervisor.calls,0)

    async def test_repeated_cancel_and_shutdown_wait_for_reaping(self):
        task=self.start(); await self.dispatched()
        task.cancel(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        closing=asyncio.create_task(self.owner.aclose())
        await asyncio.sleep(.01); closing.cancel()
        self.assertTrue(self.path.exists())
        self.assertFalse(self.supervisor.closed)
        self.supervisor.future.set_result(None)
        with self.assertRaises(asyncio.CancelledError): await closing
        self.assertFalse(self.path.exists())
        self.assertFalse(self.supervisor.closed) # CPU owner closes supervisor later

    async def test_reaped_crash(self):
        task=self.start(); await self.dispatched()
        self.assertTrue(self.path.exists())
        self.supervisor.future.set_exception(PDFInfrastructureFailure())
        with self.assertRaises(PDFInfrastructureFailure): await task
        self.assertFalse(self.path.exists())

    async def test_unknown_dispatch_retains_lease(self):
        self.supervisor.failure=RuntimeError('private')
        with self.assertRaises(StorageUncertain): await self.start()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.owner.unresolved(),('dispatch_unresolved',))
        with self.assertRaises(StorageRejected): await self.owner.parse(lambda:self.lease,{})

    async def test_cleanup_failure_retains_ownership(self):
        def fail(): raise OSError('private path')
        self.lease=MaterializedPDF(self.path,fail)
        self.supervisor.future.set_result(None)
        with self.assertRaises(StorageUncertain) as error: await self.start()
        self.assertNotIn('private',str(error.exception))
        self.assertTrue(self.path.exists())
        self.assertEqual(self.owner.unresolved(),('cleanup_failed',))

    async def test_unresolved_future_keeps_file(self):
        task=self.start(); await self.dispatched(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        await asyncio.sleep(.01)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.owner.unresolved())
        # Test teardown supplies genuine simulated reaping, not a timeout.
        self.supervisor.future.set_result(None)
        await self.owner.aclose()

    async def test_shared_lane_rejects_second_parser_and_local_session(self):
        task = self.start()
        await self.dispatched()
        other = ParserLeases(self.workers, self.cpu)
        with self.assertRaises(CapacityExceeded):
            await other.parse(lambda:self.lease, {})
        with self.assertRaises(CapacityExceeded):
            with self.cpu.pdf_processing_session(): pass
        self.assertEqual(self.supervisor.calls, 1)
        self.supervisor.future.set_result(None)
        await task

    async def test_cleanup_holds_cpu_and_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        def cleanup():
            entered.set()
            if not release.wait(5): raise RuntimeError('test timeout')
            self.path.unlink()
        self.lease = MaterializedPDF(self.path, cleanup)
        task = self.start()
        await self.dispatched()
        self.supervisor.future.set_result(None)
        while not entered.is_set(): await asyncio.sleep(.001)
        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
        closing = asyncio.create_task(self.admission.aclose())
        await asyncio.sleep(.01)
        self.assertFalse(closing.done())
        release.set()
        await task
        await closing
        self.assertFalse(self.path.exists())

    async def test_local_path_uses_same_supervisor_without_materialization(self):
        self.supervisor.future.set_result('local')
        with self.cpu.pdf_processing_session() as session:
            self.assertEqual(await session.run_pdf(str(self.path), {}), 'local')
        self.assertTrue(self.path.exists())
        self.assertEqual(self.supervisor.calls, 1)
        self.assertEqual(self.owner.unresolved(), ())
