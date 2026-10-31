"""Coalescing, cancellation and fresh results without any actual providers."""
import asyncio
from types import SimpleNamespace
import unittest
from fastapi import FastAPI
from dependency_diagnostics import DependencyDiagnostics, get_diagnostics
from vector_store import VectorFailure


class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesced_callers_cancellation_and_fresh_results(self):
        for name in ('database', 'qdrant'):
            with self.subTest(name=name):
                service = DependencyDiagnostics()
                entered, release = asyncio.Event(), asyncio.Event()
                calls = 0
                async def probe():
                    nonlocal calls
                    calls += 1
                    entered.set()
                    await release.wait()
                    return True
                tasks = [asyncio.create_task(service.run(name, probe)) for _ in range(10)]
                await entered.wait()
                tasks[0].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[0]
                self.assertEqual(calls, 1)
                self.assertFalse(service._tasks[name].cancelled())
                release.set()
                self.assertEqual(await asyncio.gather(*tasks[1:]), [True] * 9)
                self.assertTrue(await service.run(name, probe))
                self.assertEqual(calls, 2)
                await service.close()

    async def test_error_is_not_cached_and_per_app_isolation(self):
        first_request = SimpleNamespace(app=FastAPI())
        first = await get_diagnostics(first_request)
        second = await get_diagnostics(SimpleNamespace(app=FastAPI()))
        self.assertIs(await get_diagnostics(first_request), first)
        self.assertIsNot(first, second)
        calls = 0
        async def failed():
            nonlocal calls
            calls += 1
            raise VectorFailure('synthetic')
        for _ in range(2):
            with self.assertRaises(VectorFailure):
                await first.run('qdrant', failed)
        self.assertEqual(calls, 2)
        self.assertFalse(second._tasks)
        await first.close()
        await second.close()

    async def test_shutdown_drains_probe_and_prevents_new_dispatch(self):
        service = DependencyDiagnostics()
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe():
            entered.set()
            await release.wait()
            return True
        caller = asyncio.create_task(service.run('database', probe))
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        service.begin_shutdown()
        closing = asyncio.create_task(service.close())
        self.assertFalse(await service.run('database', probe))
        with self.assertRaises(VectorFailure):
            await service.run('qdrant', probe)
        self.assertFalse(closing.done())
        release.set()
        await closing
