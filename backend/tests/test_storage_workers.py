"""No MongoDB clients: deterministic thread gates and fake client ownership."""
import asyncio
import threading
import unittest
from unittest.mock import Mock
from storage_workers import optional_storage_lifespan, StorageRejected


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_no_client(self):
        factory = Mock()
        async with optional_storage_lifespan(client_factory=factory) as owner:
            self.assertIsNone(owner)
        factory.assert_not_called()

    async def test_single_client_tls_and_close(self):
        factory = Mock()
        async with optional_storage_lifespan(enabled=True,client_factory=factory) as owner:
            self.assertEqual(await owner.run(lambda: 12),12)
            self.assertEqual(await owner.run(lambda: 13),13)
        factory.assert_called_once()
        options=factory.call_args.kwargs
        self.assertTrue(options['tls'])
        self.assertFalse(options['tlsAllowInvalidCertificates'])
        self.assertFalse(options['tlsAllowInvalidHostnames'])
        self.assertEqual(options['waitQueueTimeoutMS'],5000)
        factory.return_value.close.assert_called_once()

    async def test_bounded_queue_cancel_and_shutdown_order(self):
        factory=Mock(); entered=threading.Event(); release=threading.Event()
        calls=[]
        def work():
            entered.set()
            if not release.wait(5): raise RuntimeError('Test gate timed out')
            calls.append('finished')
        async with optional_storage_lifespan(enabled=True,client_factory=factory) as owner:
            first=asyncio.create_task(owner.run(work))
            while not entered.is_set(): await asyncio.sleep(.001)
            second=asyncio.create_task(owner.run(lambda:calls.append('queued')))
            await asyncio.sleep(.01)
            try:
                with self.assertRaises(StorageRejected): await owner.run(lambda:None)
                first.cancel(); second.cancel()
                for task in (first,second):
                    with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(owner.unresolved(),2)
                closing=asyncio.create_task(owner.aclose())
                await asyncio.sleep(.01)
                self.assertFalse(closing.done())
                factory.return_value.close.assert_not_called()
                with self.assertRaises(StorageRejected): await owner.run(lambda:None)
            finally:
                release.set()
            await closing
            self.assertEqual(calls,['finished','queued'])
            self.assertEqual(owner.unresolved(),0)
        factory.return_value.close.assert_called_once()

    async def test_cancel_before_dispatch(self):
        operation=Mock()
        async with optional_storage_lifespan(enabled=True,client_factory=Mock()) as owner:
            task=asyncio.create_task(owner.run(operation)); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            operation.assert_not_called()
            self.assertEqual(owner.unresolved(),0)

    async def test_exceptions_and_event_loop_thread(self):
        main=threading.get_ident()
        async with optional_storage_lifespan(enabled=True,client_factory=Mock()) as owner:
            self.assertNotEqual(await owner.run(threading.get_ident),main)
            def failure(): raise ValueError('expected')
            with self.assertRaises(ValueError): await owner.run(failure)
            self.assertEqual(await owner.run(lambda:42),42)

    async def test_cancelled_shutdown_still_drains(self):
        factory=Mock(); gate=threading.Event(); started=threading.Event()
        def work(): started.set(); gate.wait(5)
        async with optional_storage_lifespan(enabled=True,client_factory=factory) as owner:
            job=asyncio.create_task(owner.run(work))
            while not started.is_set(): await asyncio.sleep(.001)
            closing=asyncio.create_task(owner.aclose())
            await asyncio.sleep(.01); closing.cancel(); await asyncio.sleep(.01)
            factory.return_value.close.assert_not_called()
            gate.set(); await job
            with self.assertRaises(asyncio.CancelledError): await closing
        factory.return_value.close.assert_called_once()
