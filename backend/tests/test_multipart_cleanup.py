"""Synthetic parser-instance cleanup checks; no services or PDF extraction."""
import asyncio
import io
from importlib.metadata import version, requires
from pathlib import Path
from packaging.requirements import Requirement
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.formparsers import MultiPartParser, MultiPartException
from multipart_cleanup import ParserCleanup
from document_routes import parse_upload


HEADER = b'--b\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\nContent-Type: application/pdf\r\n\r\n'


async def chunks(raw):
    for offset in range(0, len(raw), 65536):
        yield raw[offset:offset + 65536]


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def test_pinned_runtime_satisfies_fastapi_and_adapter_contract(self):
        pins = (Path(__file__).resolve().parents[1] / 'requirements.txt').read_text().splitlines()
        for package, expected in (('starlette', '1.6.0'), ('python-multipart', '0.0.32')):
            with self.subTest(package=package):
                self.assertIn(f'{package}=={expected}', pins)
                self.assertEqual(version(package), expected)
                constraints = [Requirement(item) for item in requires('fastapi')
                               if Requirement(item).name == package]
                self.assertTrue(constraints)
                self.assertTrue(all(expected in item.specifier for item in constraints))
        self.assertEqual(version('fastapi'), '0.141.1')
        adapter = self.adapter(b'')
        self.assertEqual(adapter.state, 'pending')

    def adapter(self, raw):
        return ParserCleanup(Headers({'content-type': 'multipart/form-data; boundary=b'}), chunks(raw))

    async def test_truncated_completed_and_rollover(self):
        for size in (12, 1100000):
            for complete in (False, True):
                with self.subTest(size=size, complete=complete):
                    adapter = self.adapter(HEADER + b'x' * size + (b'\r\n--b--\r\n' if complete else b''))
                    form = await adapter.parse()
                    spool = adapter.parser._files_to_close_on_error[0]
                    try:
                        self.assertEqual(len(form), int(complete))
                        self.assertFalse(spool.closed)
                        if size > 1048576:
                            self.assertIsNotNone(spool.name)
                            self.assertGreaterEqual(spool.fileno(), 0)
                        adapter.account(form if complete else None)
                        self.assertEqual(spool.closed, not complete)
                        self.assertEqual(adapter.closures[id(spool)], 'transferred' if complete else 'closed')
                        adapter.account(form if complete else None)
                        await form.close()
                        self.assertTrue(spool.closed)
                    finally:
                        spool.close()

    async def test_truncation_positions_and_original_422(self):
        raw = HEADER + b'%PDF-data\r\n--b--\r\n'
        for cut in (1, 8, len(HEADER)-2, len(HEADER), len(HEADER)+5):
            with self.subTest(cut=cut):
                request = SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}),
                                          stream=lambda: chunks(raw[:cut]))
                with self.assertRaises(HTTPException) as error:
                    await parse_upload(request, 2000000)
                self.assertEqual(error.exception.status_code, 422)

    async def test_exception_registry_already_closed_and_size_limit(self):
        async def broken():
            yield HEADER + b'x'
            raise HTTPException(413, 'PDF exceeds the configured upload limit.')
        adapter = ParserCleanup(Headers({'content-type': 'multipart/form-data; boundary=b'}), broken())
        with self.assertRaises(HTTPException) as error:
            await adapter.parse()
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(adapter.state, 'exception')
        self.assertTrue(adapter.accounted)
        self.assertTrue(all(s.closed for s in adapter.streams.values()))
        request = SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}),
                                  stream=lambda: chunks(HEADER + b'x' * 70000))
        with self.assertRaises(HTTPException) as error:
            await parse_upload(request, 10)
        self.assertEqual(error.exception.status_code, 413)

    async def test_versions_fail_before_factory_or_body(self):
        for package in ('starlette', 'python-multipart'):
            with self.subTest(package=package):
                def versions(name):
                    return 'unsupported' if name == package else {'starlette': '1.6.0', 'python-multipart': '0.0.32'}[name]
                with patch('multipart_cleanup.version', side_effect=versions):
                    factory = unittest.mock.Mock()
                    with self.assertRaises(HTTPException) as error:
                        ParserCleanup(Headers(), chunks(b''), parser_factory=factory)
                    self.assertEqual(error.exception.status_code, 503)
                    self.assertNotIn('unsupported', str(error.exception.detail))
                    factory.assert_not_called()

    async def test_interface_guard_before_parsing(self):
        for registry in ('missing', None, (), {}, [object()]):
            with self.subTest(registry=registry):
                parser = SimpleNamespace(parse=unittest.mock.AsyncMock())
                if registry != 'missing':
                    parser._files_to_close_on_error = registry
                with self.assertRaises(HTTPException):
                    ParserCleanup(Headers(), chunks(b''), parser_factory=lambda *a, **k: parser)
                parser.parse.assert_not_awaited()

    async def test_identity_deduplication_close_failure_and_no_retry(self):
        class Stream(io.BytesIO):
            calls = 0
            def close(self):
                self.calls += 1
                super().close()
        stream = Stream()
        adapter = self.adapter(b'')
        adapter.state = 'normal'
        adapter.parser._files_to_close_on_error.extend([stream, stream])
        adapter.account()
        adapter.account()
        self.assertEqual(stream.calls, 1)
        class Failure:
            closed = False
            calls = 0
            def close(self):
                self.calls += 1
                raise OSError('private path')
        stream = Failure()
        adapter = self.adapter(b'')
        adapter.state = 'normal'
        adapter.parser._files_to_close_on_error.append(stream)
        for _ in range(2):
            with self.assertRaises(HTTPException) as error:
                adapter.account()
            self.assertNotIn('private', str(error.exception.detail))
        self.assertEqual(stream.calls, 1)
        self.assertEqual(adapter.closures[id(stream)], 'failed')

    async def test_parser_close_failure_not_retried(self):
        class Stream:
            closed = False
            calls = 0
            def close(self):
                self.calls += 1
                raise OSError('private')
        stream = Stream()
        class FakeParser:
            def __init__(self, *args, **kwargs):
                self._files_to_close_on_error = []
            async def parse(self):
                self._files_to_close_on_error.append(stream)
                stream.close()
        adapter = ParserCleanup(Headers(), chunks(b''), parser_factory=FakeParser)
        with self.assertRaises(HTTPException):
            await adapter.parse()
        self.assertEqual(stream.calls, 1)
        self.assertEqual(adapter.closures[id(stream)], 'unconfirmed')

    async def test_guard_does_not_consume_body_and_checks_async_interface(self):
        consumed = []
        async def body():
            consumed.append(True)
            yield HEADER
        request = SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}), stream=body)
        with patch('multipart_cleanup.version', return_value='unsupported'):
            with self.assertRaises(HTTPException) as error:
                await parse_upload(request, 100)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(consumed, [])
        for parser in (SimpleNamespace(_files_to_close_on_error=[]),
                       SimpleNamespace(_files_to_close_on_error=[], parse=lambda: None)):
            with self.assertRaises(HTTPException):
                ParserCleanup(Headers(), body(), parser_factory=lambda *a, **k: parser)
        self.assertEqual(consumed, [])

    async def test_failed_orphan_cleanup_aborts_transfer_and_is_sticky(self):
        class Failure:
            closed = False
            calls = 0
            def close(self):
                self.calls += 1
                raise OSError('private')
        orphan, returned = Failure(), io.BytesIO()
        adapter = self.adapter(b'')
        adapter.state = 'normal'
        adapter.parser._files_to_close_on_error.extend([returned, orphan])
        form = FormData({'file': UploadFile(returned)})
        for _ in range(2):
            with self.assertRaises(HTTPException):
                adapter.account(form)
        self.assertTrue(returned.closed)
        self.assertEqual(adapter.closures[id(returned)], 'closed')
        self.assertEqual(orphan.calls, 1)
        self.assertTrue(adapter.failure)

    async def test_unknown_returned_stream_is_rejected_on_repeated_accounting(self):
        stream = io.BytesIO()
        adapter = self.adapter(b'')
        adapter.state = 'normal'
        form = FormData({'file': UploadFile(stream)})
        try:
            for _ in range(2):
                with self.assertRaises(HTTPException):
                    adapter.account(form)
            self.assertTrue(adapter.failure)
        finally:
            stream.close()

    async def test_parser_errors_close_owned_spools_and_preserve_translation(self):
        # A second file exceeds the unchanged max_files=1 bound.
        raw = HEADER + b'%PDF-first\r\n' + HEADER + b'%PDF-second\r\n--b--\r\n'
        adapter = self.adapter(raw)
        with self.assertRaises(MultiPartException):
            await adapter.parse()
        self.assertEqual(adapter.state, 'exception')
        self.assertTrue(adapter.accounted)
        self.assertFalse(adapter.failure)
        self.assertTrue(all(stream.closed for stream in adapter.streams.values()))
        request = SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}),
                                  stream=lambda: chunks(raw))
        with self.assertRaises(HTTPException) as error:
            await parse_upload(request, 10000)
        self.assertEqual(error.exception.status_code, 422)

    async def test_direct_cancellation_is_not_quiescence(self):
        entered = asyncio.Event()
        async def pending():
            entered.set()
            await asyncio.Event().wait()
            yield b''
        adapter = ParserCleanup(Headers({'content-type': 'multipart/form-data; boundary=b'}), pending())
        task = asyncio.create_task(adapter.parse())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(adapter.state, 'cancelled')
        self.assertFalse(adapter.accounted)
        with self.assertRaises(HTTPException):
            adapter.account()

    async def test_no_global_parser_change_and_normal_handoff(self):
        original = MultiPartParser.parse
        request = SimpleNamespace(headers=Headers({'content-type': 'multipart/form-data; boundary=b'}),
                                  stream=lambda: chunks(HEADER + b'%PDF-data\r\n--b--\r\n'))
        form = await parse_upload(request, 100)
        try:
            self.assertFalse(form['file'].file.closed)
            self.assertEqual(await form['file'].read(), b'%PDF-data')
            self.assertIs(MultiPartParser.parse, original)
        finally:
            await form.close()
