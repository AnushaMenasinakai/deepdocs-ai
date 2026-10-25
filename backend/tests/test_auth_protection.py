"""Deterministic primitive tests; no routes, databases or real password hashing."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (AuthProtectionSettings, AUTH_PROTECTION_BOUNDS,
                    ConfigurationError, load_auth_protection_settings)
from auth_protection import AuthRateLimiter, PasswordExecutor, PasswordCapacityExceeded, normalize_client_address


class ConfigurationTests(unittest.TestCase):
    def test_defaults_and_environment_precedence(self):
        with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_auth_protection_settings(), AuthProtectionSettings())
        with patch('config.dotenv_values', return_value={'AUTH_PASSWORD_MAX_CONCURRENT': '3'}) as read:
            with patch.dict(os.environ, {'AUTH_PASSWORD_MAX_CONCURRENT': ' 2 '}, clear=True):
                self.assertEqual(load_auth_protection_settings().password_max_concurrent, 2)
            self.assertFalse(read.call_args.kwargs['interpolate'])

    def test_every_bound_and_sanitized_invalid_setting(self):
        for field, (low, high) in AUTH_PROTECTION_BOUNDS.items():
            for value in (str(low), str(high)):
                with self.subTest(field=field, value=value):
                    with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, {'AUTH_'+field.upper(): value}, clear=True):
                        self.assertEqual(getattr(load_auth_protection_settings(), field), int(value))
            for value in ('', 'private-value', '-1', '1.5', 'true', str(low-1), str(high+1)):
                with self.subTest(field=field, value=value):
                    with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, {'AUTH_'+field.upper(): value}, clear=True):
                        with self.assertRaises(ConfigurationError) as error:
                            load_auth_protection_settings()
                        self.assertEqual(str(error.exception), 'Invalid authentication protection configuration.')
        with self.assertRaises(ConfigurationError):
            AuthProtectionSettings(password_max_concurrent=True)

    def test_file_read_failure_is_sanitized(self):
        with patch('config.dotenv_values', side_effect=OSError('private-path')):
            with self.assertRaisesRegex(ConfigurationError, '^Invalid authentication protection configuration.$'):
                load_auth_protection_settings()


class LimiterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.settings = AuthProtectionSettings()
        self.limiter = self.make()

    def make(self, **changes):
        return AuthRateLimiter(replace(self.settings, **changes), clock=lambda: self.now, key_secret=b'x'*32)

    def test_exact_boundary_refill_and_no_lockout_extension(self):
        for _ in range(5):
            self.assertTrue(self.limiter.check('login', '127.0.0.1', 'a@example.com').allowed)
        self.assertEqual(self.limiter.check('login', '127.0.0.1', 'a@example.com').retry_after, 60)
        self.now = 59.5
        self.assertEqual(self.limiter.check('login', '127.0.0.1', 'a@example.com').retry_after, 1)
        self.now = 60
        self.assertTrue(self.limiter.check('login', '127.0.0.1', 'a@example.com').allowed)

    def test_combined_rejection_does_not_consume_either_bucket(self):
        limiter = self.make(login_ip_burst=2, login_account_burst=1)
        self.assertTrue(limiter.check('login', '1.2.3.4', 'a').allowed)
        self.assertFalse(limiter.check('login', '1.2.3.4', 'a').allowed)
        self.assertTrue(limiter.check('login', '1.2.3.4', 'b').allowed)
        count = limiter.entry_count
        self.assertFalse(limiter.check('login', '1.2.3.4', 'c').allowed)
        self.assertEqual(limiter.entry_count, count)
        self.now = 3
        self.assertTrue(limiter.check('login', '1.2.3.4', 'c').allowed)

    def test_namespaces_and_no_account_only_block(self):
        for _ in range(5): self.limiter.check('login', '1.2.3.4', 'a')
        self.assertTrue(self.limiter.check('login', '1.2.3.5', 'a').allowed)
        for _ in range(3): self.assertTrue(self.limiter.check('register', '1.2.3.4').allowed)
        self.assertEqual(self.limiter.check('register', '1.2.3.4').retry_after, 720)
        self.now = 720
        self.assertTrue(self.limiter.check('register', '1.2.3.4').allowed)

    def test_address_normalization_and_shared_missing_bucket(self):
        for value, expected in [('::ffff:192.0.2.1','192.0.2.1'), ('2001:0db8::1','2001:db8::1'),
                                (' 127.0.0.1 ','127.0.0.1'), (None,'unknown'),
                                ('untrusted, 1.2.3.4','unknown'), ('fe80::1%eth0','unknown'), ('bad','unknown')]:
            with self.subTest(value=value): self.assertEqual(normalize_client_address(value), expected)
        for value in (None, 'bad', ''): self.assertTrue(self.limiter.check('register', value).allowed)
        self.assertFalse(self.limiter.check('register', 'another-invalid').allowed)
        for _ in range(3): self.limiter.check('register', '::ffff:192.0.2.1')
        self.assertFalse(self.limiter.check('register', '192.0.2.1').allowed)

    def test_hmac_keys_and_invalid_inputs(self):
        digest = self.limiter.account_key('private@example.com')
        self.assertEqual(len(digest), 32)
        self.assertEqual(digest, self.make().account_key('private@example.com'))
        other = AuthRateLimiter(key_secret=b'y'*32)
        self.assertNotEqual(digest, other.account_key('private@example.com'))
        self.assertNotEqual(digest, self.limiter.account_key('other@example.com'))
        self.limiter.check('login', '127.0.0.1', 'private@example.com')
        self.assertNotIn('private@example.com', repr(self.limiter._buckets))
        with self.assertRaises(ValueError): self.limiter.check('login', '127.0.0.1', '')
        with self.assertRaises(ValueError): self.limiter.check('other', '127.0.0.1')
        with self.assertRaises(ValueError): AuthRateLimiter(key_secret=b'short')

    def test_state_saturation_does_not_evict_or_consume(self):
        limiter = self.make(limiter_max_entries=2)
        self.assertTrue(limiter.check('login', '1.2.3.4', 'a').allowed)
        for n in range(100): self.assertFalse(limiter.check('login', '1.2.3.4', str(n)).allowed)
        self.assertEqual(limiter.entry_count, 2)
        for _ in range(4): self.assertTrue(limiter.check('login', '1.2.3.4', 'a').allowed)
        self.assertFalse(limiter.check('login', '1.2.3.4', 'a').allowed)

    def test_cleanup_requires_full_and_idle(self):
        limiter = self.make(limiter_max_entries=2, limiter_idle_seconds=60)
        for _ in range(5): limiter.check('login', '1.2.3.4', 'a')
        self.now = 60
        self.assertFalse(limiter.check('login', '1.2.3.5', 'b').allowed)
        self.now = 300
        self.assertTrue(limiter.check('login', '1.2.3.5', 'b').allowed)
        self.assertEqual(limiter.entry_count, 2)

    def test_cleanup_work_is_bounded(self):
        limiter = self.make(limiter_max_entries=100)
        for n in range(80): limiter.check('register', f'192.0.2.{n}')
        self.now = 4000
        limiter.check('register', '198.51.100.1')
        self.assertEqual(limiter.entry_count, 49)  # 32 removed, one added.

    def test_concurrent_checks_are_atomic(self):
        barrier = threading.Barrier(16)
        def attempt(_):
            barrier.wait()
            return self.limiter.check('login', '1.2.3.4', 'a').allowed
        with ThreadPoolExecutor(max_workers=16) as pool:
            self.assertEqual(sum(pool.map(attempt, range(16))), 5)
        self.assertEqual(self.limiter.entry_count, 2)


class ControlledExecutor:
    def __init__(self, **kwargs):
        self.jobs = []
        self.submitted = asyncio.Queue()
        self.shutdown_started = threading.Event()
        self.shutdown_release = threading.Event()
        self.shutdown_release.set()
        self.fail = False

    def submit(self, function, *args, **kwargs):
        if self.fail: raise RuntimeError('submit failure')
        future = Future()
        self.jobs.append(future)
        self.submitted.put_nowait(future)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_started.set()
        if cancel_futures:
            for future in self.jobs: future.cancel()
        self.shutdown_release.wait(5)


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = ControlledExecutor()
        self.gate = PasswordExecutor(executor_factory=lambda **kw: self.worker)

    async def asyncTearDown(self):
        for future in self.worker.jobs:
            if not future.done(): future.set_result(None)
        self.worker.shutdown_release.set()
        await self.gate.aclose()

    async def job(self, running=False):
        task = asyncio.create_task(self.gate.run(lambda: None))
        future = await self.worker.submitted.get()
        if running: future.set_running_or_notify_cancel()
        return task, future

    async def test_nonwaiting_shared_admission_and_completion(self):
        a, fa = await self.job()
        b, fb = await self.job()
        with self.assertRaises(PasswordCapacityExceeded): await self.gate.run(lambda: None)
        self.assertEqual(len(self.worker.jobs), 2)
        fa.set_result('hash')
        self.assertEqual(await a, 'hash')
        self.assertEqual(self.gate.outstanding, 1)
        c, fc = await self.job()
        fb.set_result(True); fc.set_result(False)
        self.assertEqual(await b, True); self.assertEqual(await c, False)
        self.assertEqual(self.gate.outstanding, 0)

    async def test_submission_failure_and_worker_exception(self):
        self.worker.fail = True
        with self.assertRaises(RuntimeError): await self.gate.run(lambda: None)
        self.assertEqual(self.gate.outstanding, 0)
        self.worker.fail = False
        task, future = await self.job()
        future.set_exception(ValueError('worker failure'))
        with self.assertRaises(ValueError): await task
        self.assertEqual(self.gate.outstanding, 0)

    async def test_cancel_before_start_releases(self):
        task, future = await self.job()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(future.cancelled())
        self.assertEqual(self.gate.outstanding, 0)

    async def test_cancel_running_retains_slot_until_future_finishes(self):
        task, future = await self.job(running=True)
        other, second = await self.job(running=True)
        task.cancel(); task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.gate.outstanding, 2)
        with self.assertRaises(PasswordCapacityExceeded): await self.gate.run(lambda: None)
        future.set_exception(ValueError('late sensitive failure'))
        self.assertEqual(self.gate.outstanding, 1)
        second.set_result(None); await other

    async def test_cancelled_before_coroutine_runs_never_submits(self):
        task = asyncio.create_task(self.gate.run(lambda: None))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(self.worker.jobs, [])

    async def test_shutdown_cancellation_and_repeated_close(self):
        self.worker.shutdown_release.clear()
        closing = asyncio.create_task(self.gate.aclose())
        await asyncio.to_thread(self.worker.shutdown_started.wait, 5)
        closing.cancel(); closing.cancel()
        await asyncio.sleep(0)  # Scheduler checkpoint, not a timing delay.
        self.assertFalse(closing.done())
        with self.assertRaises(PasswordCapacityExceeded): await self.gate.run(lambda: None)
        self.worker.shutdown_release.set()
        with self.assertRaises(asyncio.CancelledError): await closing
        await self.gate.aclose()
        await self.gate.aclose()

    async def test_shutdown_cancels_not_started_jobs(self):
        task, future = await self.job()
        await self.gate.aclose()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(future.cancelled())
        self.assertEqual(self.gate.outstanding, 0)

    async def test_real_executor_forwards_arguments_and_errors(self):
        gate = PasswordExecutor(1)
        try:
            self.assertEqual(await gate.run(lambda value, *, suffix: value + suffix, 'a', suffix='b'), 'ab')
            def fail():
                raise ValueError('controlled failure')
            with self.assertRaises(ValueError): await gate.run(fail)
            self.assertEqual(gate.outstanding, 0)
        finally:
            await gate.aclose()

    async def test_real_executor_keeps_event_loop_responsive(self):
        gate = PasswordExecutor(1)
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        def work():
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5): raise RuntimeError('test synchronization failed')
            return 7
        task = asyncio.create_task(gate.run(work))
        try:
            await asyncio.wait_for(started.wait(), 5)
            with self.assertRaises(PasswordCapacityExceeded): await gate.run(work)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(gate.outstanding, 1)
            release.set()
            await gate.aclose()
            self.assertEqual(gate.outstanding, 0)
        finally:
            release.set()
            await gate.aclose()

    async def test_concurrent_task_burst_is_bounded(self):
        tasks = [asyncio.create_task(self.gate.run(lambda: None)) for _ in range(20)]
        first = await self.worker.submitted.get()
        second = await self.worker.submitted.get()
        rejected = await asyncio.gather(*tasks[2:], return_exceptions=True)
        self.assertTrue(all(isinstance(value, PasswordCapacityExceeded) for value in rejected))
        self.assertEqual(self.gate.outstanding, 2)
        first.set_result(None); second.set_result(None)
        await asyncio.gather(*tasks[:2])


if __name__ == '__main__':
    unittest.main()
