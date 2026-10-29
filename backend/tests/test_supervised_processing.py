"""Real local PDFs and spawn; all document/vector dependencies are offline."""
import asyncio
import copy
from dataclasses import asdict, replace
from functools import partial
import multiprocessing
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import pymupdf
from bson import ObjectId
from config import ProcessingSettings
from cpu_workers import Lane, SubmissionRejected
from expensive_admission import Resource
from pdf_processing import extract_chunks, ProcessingFailure
from pdf_subprocess import PDFSupervisor, PDFExecutionNotStarted
from pdf_worker_protocol import ParsingFailure
from pdf_worker_child import recognized_failure_details
from pdf_subprocess_helpers import synthetic_child
import test_documents as document_fixtures
import test_processing as processing_fixtures
from test_documents import request


class SupervisedProcessingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = document_fixtures.DocumentTests.asyncSetUp
    asyncTearDown = document_fixtures.DocumentTests.asyncTearDown
    make_document = processing_fixtures.ProcessingTests.make_document
    process = processing_fixtures.ProcessingTests.process

    async def process_with_headers(self, body):
        application = document_fixtures.app
        headers = {}
        async def observe(scope, receive, send):
            async def capture(message):
                if message['type'] == 'http.response.start':
                    headers.update(message['headers'])
                await send(message)
            await application(scope, receive, capture)
        with patch.object(document_fixtures, 'app', observe):
            status, result, _ = await self.process(body)
        return status, result, headers

    async def test_real_pdf_output_parity_and_reaping(self):
        children_before = {child.pid for child in multiprocessing.active_children()}
        for pages in [('Single source.',), ('Café naïve résumé.', '', 'Page three. ' * 90), ('',)]:
            with self.subTest(pages=len(pages)):
                body = await self.make_document(pages)
                path = self.root / (body['id'] + '.pdf')
                settings = ProcessingSettings(chunk_target=200, chunk_overlap=30)
                try:
                    expected = extract_chunks(path, settings)
                except ProcessingFailure as error:
                    expected = (error.status, str(error))
                with self.workers.pdf_processing_session() as session:
                    actual = await session.run_pdf(str(path), asdict(settings))
                if isinstance(actual, ParsingFailure):
                    self.assertEqual(recognized_failure_details(actual.code), expected)
                else:
                    self.assertEqual(actual, expected)
                self.assertFalse({child.pid for child in multiprocessing.active_children()} - children_before)

    async def test_spawned_limits_and_invalid_pdf_match_original(self):
        body = await self.make_document(('words ' * 150, 'second page'))
        path = self.root / (body['id'] + '.pdf')
        original = path.read_bytes()
        settings = ProcessingSettings(chunk_target=100, chunk_overlap=10)
        cases = [(original, replace(settings, max_pages=1)),
                 (original, replace(settings, max_source_bytes=5)),
                 (original, replace(settings, max_text_characters=100)),
                 (original, replace(settings, max_chunks=1)),
                 (b'%PDF-invalid', settings)]
        pdf = pymupdf.open()
        pdf.new_page().insert_text((50, 50), 'Encrypted source')
        encrypted = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               user_pw='test-only', owner_pw='test-owner')
        pdf.close()
        cases.append((encrypted, settings))
        for data, limits in cases:
            with self.subTest(limit=limits):
                path.write_bytes(data)
                with self.assertRaises(ProcessingFailure) as expected:
                    extract_chunks(path, limits)
                with self.workers.pdf_processing_session() as session:
                    actual = await session.run_pdf(str(path), asdict(limits))
                self.assertIsInstance(actual, ParsingFailure)
                self.assertEqual(recognized_failure_details(actual.code),
                                 (expected.exception.status, str(expected.exception)))

    async def test_atomic_busy_rejection_preserves_state_and_ownership_precedence(self):
        body = await self.make_document()
        before = copy.deepcopy(self.docs.documents)
        for resource in (Resource.CPU, Resource.DOCUMENT):
            with self.subTest(resource=resource):
                with self.admission.workflow(resource):
                    counts = self.admission._admission.outstanding
                    with patch.object(self.docs, 'find_one_and_update', new_callable=AsyncMock) as claim, \
                            patch.object(self.docs, 'update_one', new_callable=AsyncMock) as write:
                        status, response, headers = await self.process_with_headers(body)
                        self.assertEqual(status, 503)
                        self.assertEqual(response, {'detail': 'Service is busy. Please try again later.'})
                        self.assertEqual(dict(headers)[b'retry-after'], b'1')
                        self.assertEqual(dict(headers)[b'cache-control'], b'no-store')
                        claim.assert_not_awaited()
                        write.assert_not_awaited()
                    self.assertEqual(self.admission._admission.outstanding, counts)
                    self.assertEqual(self.docs.documents, before)
                    self.current_owner = self.other
                    self.assertEqual((await self.process(body))[0], 404)
                    self.current_owner = self.owner
                    self.docs.documents[ObjectId(body['id'])]['_operation'] = ObjectId()
                    self.assertEqual((await self.process(body))[0], 409)
                    del self.docs.documents[ObjectId(body['id'])]['_operation']

    async def test_confirmed_predispatch_failure_publishes_failed_and_releases(self):
        for failure in (SubmissionRejected(), PDFExecutionNotStarted()):
            with self.subTest(failure=type(failure).__name__):
                body = await self.make_document()
                with patch('cpu_workers.ExecutionSession.run_pdf', side_effect=failure):
                    self.assertEqual((await self.process(body))[0], 503)
                row = self.docs.documents[ObjectId(body['id'])]
                self.assertEqual(row['status'], 'failed')
                self.assertNotIn('_operation', row)
                self.assertFalse(any(self.admission._admission.outstanding.values()))

    async def test_real_child_crash_retains_claim_without_chunk_or_vector_mutation(self):
        # Synthetic target sees an explicit scenario name, while the service
        # still validates and supplies its actual owned document storage path.
        body = await self.make_document()
        def submit(path, settings):
            return supervisor.submit(str(Path(path).with_name('crash')), settings)
        supervisor = PDFSupervisor(_target=synthetic_child)
        try:
            with patch.object(self.workers, '_submit_pdf', side_effect=submit), \
                    patch.object(self.chunks, 'insert_many', new_callable=AsyncMock) as insert, \
                    patch('document_processing.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
                status, error, _ = await self.process(body)
                self.assertEqual(status, 503)
                self.assertIn('Service review', error['detail'])
                insert.assert_not_awaited()
                vectors.assert_not_awaited()
            row = self.docs.documents[ObjectId(body['id'])]
            self.assertIn('_operation', row)
            self.assertEqual(row['status'], 'processing')
            self.assertIsNone(row['processing_error'])
            self.assertFalse(any(self.admission._admission.outstanding.values()))
        finally:
            await asyncio.to_thread(supervisor.close)

    async def test_cancelled_extraction_and_transfer_never_publish_late_results(self):
        context = multiprocessing.get_context('spawn')
        for mode in ('blocked', 'transfer'):
            with self.subTest(mode=mode):
                body = await self.make_document()
                entered, release = context.Event(), context.Event()
                supervisor = PDFSupervisor(_target=partial(synthetic_child, entered=entered, release=release))
                completion = []
                def submit(path, settings):
                    future = supervisor.submit(str(Path(path).with_name(mode)), settings)
                    completion.append(future)
                    return future
                with patch.object(self.workers, '_submit_pdf', side_effect=submit), \
                        patch.object(self.chunks, 'insert_many', new_callable=AsyncMock) as insert, \
                        patch('document_processing.cleanup_document_vectors', new_callable=AsyncMock) as vectors:
                    task = asyncio.create_task(self.process(body))
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 20))
                        # This async round trip progresses while native work is blocked.
                        self.assertEqual((await request('GET', '/api/health'))[0], 200)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                        self.assertEqual(self.admission._admission.outstanding[Resource.CPU], 1)
                        self.assertEqual(self.admission._admission.outstanding[Resource.DOCUMENT], 1)
                        self.assertIsNotNone(self.workers._lanes[Lane.PDF])
                    finally:
                        release.set()
                        if completion:
                            await asyncio.wrap_future(completion[0])
                        await asyncio.gather(task, return_exceptions=True)
                        await asyncio.to_thread(supervisor.close)
                    insert.assert_not_awaited()
                    vectors.assert_not_awaited()
                self.assertIn('_operation', self.docs.documents[ObjectId(body['id'])])
                self.assertFalse(any(self.admission._admission.outstanding.values()))
