"""Offline upload fault injection: uncertain writes must retain their resources."""
import asyncio
import json
import io
import threading
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
from starlette.concurrency import run_in_threadpool

from bson import ObjectId
from bson.errors import InvalidDocument
from pymongo.errors import ConnectionFailure, DuplicateKeyError
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

import test_documents as fixtures
from test_documents import PDF, PUBLIC, request
from upload_safety import SaveLifetime, close_upload_form, observe_save_result, UploadUncertain


class UploadSafetyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.DocumentTests.asyncSetUp
    asyncTearDown = fixtures.DocumentTests.asyncTearDown
    assert_no_files = fixtures.DocumentTests.assert_no_files

    def view(self, collection):
        return patch.object(collection, "with_options", create=True, return_value=collection)

    def retained(self):
        ids = self.bases.documents[self.base_id]["_document_ids"]
        self.assertEqual(len(ids), 1)
        self.assertEqual((self.root / (str(ids[0]) + ".pdf")).read_bytes(), PDF)

    async def post(self):
        return await request("POST", self.path, PDF)

    async def test_normal_upload_contract_and_one_reservation(self):
        status, body, _ = await self.post()
        self.assertEqual(status, 201)
        self.assertEqual(set(body), PUBLIC)
        self.retained()
        self.assertEqual(self.bases.documents[self.base_id]["_document_revision"], 1)

    async def test_direct_save_cancellation_preserves_upload_resources_until_thread_finishes(self):
        loop = asyncio.get_running_loop()
        entered = loop.create_future()
        release = threading.Event()
        original = self.storage.save

        def paused(upload, *args):
            loop.call_soon_threadsafe(entered.set_result, upload)
            if not release.wait(5):
                raise OSError("test worker timeout")
            self.assertFalse(upload.file.closed)
            return original(upload, *args)

        with patch.object(self.storage, "save", side_effect=paused):
            task = asyncio.create_task(self.post())
            upload = await asyncio.wait_for(entered, 3)
            try:
                upload._deepdocs_save_task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(upload.file.closed)
                self.assertFalse(upload._deepdocs_save_lifetime.completed.is_set())
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(upload._deepdocs_save_lifetime.completed.wait, 3))
                # The same lock synchronizes with worker-side stream finalization.
                upload._deepdocs_save_lifetime.close_when_finished()
            self.assertTrue(upload.file.closed)
        self.retained()
        self.assertFalse(self.docs.documents)

    async def test_reservation_ack_loss_verified_without_second_write(self):
        original = self.bases.find_one_and_update

        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionFailure("private")

        with patch.object(self.bases, "find_one_and_update", side_effect=lost) as write, self.view(self.bases) as view:
            self.assertEqual((await self.post())[0], 201)
        self.assertEqual(write.await_count, 1)
        self.assertEqual(view.call_args.kwargs["read_preference"], ReadPreference.PRIMARY)
        self.assertEqual(view.call_args.kwargs["read_concern"].level, "majority")
        self.retained()

    async def test_reservation_uncertain_absence_does_not_save_or_release(self):
        with patch.object(self.bases, "find_one_and_update", side_effect=ConnectionFailure("private")), \
                self.view(self.bases), patch("documents.release_reservation", new_callable=AsyncMock) as release:
            self.assertEqual((await self.post())[0], 503)
        release.assert_not_awaited()
        self.assert_no_files()

    async def test_reservation_verification_unavailable_retains_reservation(self):
        original = self.bases.find_one_and_update

        async def lost(*args, **kwargs):
            await original(*args, **kwargs)
            raise ConnectionFailure("private")

        with patch.object(self.bases, "find_one_and_update", side_effect=lost), \
                patch.object(self.bases, "with_options", create=True, side_effect=ConnectionFailure("private")):
            self.assertEqual((await self.post())[0], 503)
        self.assertTrue(self.bases.documents[self.base_id]["_document_ids"])
        self.assert_no_files()

    async def test_insert_ack_loss_and_duplicate_key_verify_matching_metadata(self):
        for error in (ConnectionFailure, DuplicateKeyError):
            with self.subTest(error=error):
                original = self.docs.insert_one

                async def lost(document):
                    await original(document)
                    # A subsequent legitimate lifecycle change must survive.
                    self.docs.documents[document["_id"]]["status"] = "processed"
                    raise error("private")

                with patch.object(self.docs, "insert_one", side_effect=lost) as write, self.view(self.docs):
                    status, body, _ = await self.post()
                self.assertEqual(status, 201)
                self.assertEqual(body["status"], "processed")
                self.assertEqual(write.await_count, 1)
                self.assertEqual(set(body), PUBLIC)
                self.assertTrue((self.root / (body["id"] + ".pdf")).exists())

    async def test_insert_absence_never_proves_noncommit_or_retries(self):
        for error in (ConnectionFailure, DuplicateKeyError):
            with self.subTest(error=error):
                with patch.object(self.docs, "insert_one", side_effect=error("private")) as write, self.view(self.docs), \
                        patch("documents.release_reservation", new_callable=AsyncMock) as release:
                    status, body, _ = await self.post()
                self.assertEqual(status, 503)
                self.assertNotIn("private", json.dumps(body))
                self.assertEqual(write.await_count, 1)
                release.assert_not_awaited()
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 2)
        self.assertEqual(len(self.bases.documents[self.base_id]["_document_ids"]), 2)


    async def test_insert_verification_unavailable_preserves_committed_record(self):
        original = self.docs.insert_one

        async def lost(document):
            await original(document)
            raise ConnectionFailure("private")

        with patch.object(self.docs, "insert_one", side_effect=lost), \
                patch.object(self.docs, "with_options", create=True, side_effect=ConnectionFailure("private")):
            self.assertEqual((await self.post())[0], 503)
        self.retained()
        self.assertEqual(len(self.docs.documents), 1)

    async def test_mismatched_immutable_metadata_and_foreign_scope_preserved(self):
        for field, value in [("owner_id", ObjectId()), ("knowledge_base_id", ObjectId()),
                             ("storage_path", "different.pdf"), ("file_size", 999),
                             ("original_filename", "other.pdf"), ("stored_filename", "other.pdf"),
                             ("content_type", "text/plain")]:
            with self.subTest(field=field):
                original = self.docs.insert_one

                async def mismatch(document):
                    await original(document)
                    self.docs.documents[document["_id"]][field] = value
                    raise ConnectionFailure("private")

                with patch.object(self.docs, "insert_one", side_effect=mismatch), self.view(self.docs), \
                        patch.object(self.storage, "delete") as delete:
                    self.assertEqual((await self.post())[0], 503)
                delete.assert_not_called()
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 7)
        self.assertEqual(len(self.bases.documents[self.base_id]["_document_ids"]), 7)

    async def test_known_local_serialization_rejection_safely_compensates(self):
        with patch("documents.BSON.encode", side_effect=InvalidDocument("private")), \
                patch.object(self.docs, "insert_one", new_callable=AsyncMock) as insert:
            self.assertEqual((await self.post())[0], 503)
        insert.assert_not_awaited()
        self.assert_no_files()
        self.assertFalse(self.bases.documents[self.base_id]["_document_ids"])

    async def test_delayed_insert_after_absence_still_has_file_and_reservation(self):
        pending = []

        async def uncertain(document):
            pending.append(document.copy())
            raise ConnectionFailure("private")

        with patch.object(self.docs, "insert_one", side_effect=uncertain), self.view(self.docs):
            self.assertEqual((await self.post())[0], 503)
        # Remote work can become visible after the absence read and HTTP error.
        await self.docs.insert_one(pending[0])
        self.retained()
        self.assertEqual(len(self.docs.documents), 1)

    async def test_compensation_file_failure_preserves_reservation(self):
        with patch("documents.BSON.encode", side_effect=InvalidDocument("private")), \
                patch.object(self.storage, "delete", side_effect=OSError("private")), \
                patch("documents.release_reservation", new_callable=AsyncMock) as release:
            self.assertEqual((await self.post())[0], 503)
        release.assert_not_awaited()
        self.retained()

    async def test_cancel_compensation_does_not_release_before_file_cleanup(self):
        entered, finish = threading.Event(), threading.Event()
        original = self.storage.delete

        def paused(document):
            entered.set()
            finish.wait(3)
            original(document)

        with patch("documents.BSON.encode", side_effect=InvalidDocument("private")), \
                patch.object(self.storage, "delete", side_effect=paused), \
                patch("documents.release_reservation", new_callable=AsyncMock) as release:
            task = asyncio.create_task(self.post())
            try:
                for _ in range(300):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(entered.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                finish.set()
                for _ in range(300):
                    if not list(self.root.glob("*.pdf")):
                        break
                    await asyncio.sleep(0.005)
        release.assert_not_awaited()
        self.assertTrue(self.bases.documents[self.base_id]["_document_ids"])

    async def test_unacknowledged_configuration_rejected_before_writes(self):
        with patch.object(self.docs, "write_concern", WriteConcern(w=0), create=True), \
                patch.object(self.bases, "find_one_and_update", new_callable=AsyncMock) as reserve:
            self.assertEqual((await self.post())[0], 503)
        reserve.assert_not_awaited()
        self.assert_no_files()

    async def test_cancel_reservation_and_insert_preserves_possible_commit(self):
        for stage in ("reservation", "insert"):
            with self.subTest(stage=stage):
                entered = asyncio.Event()
                collection = self.bases if stage == "reservation" else self.docs
                method = "find_one_and_update" if stage == "reservation" else "insert_one"
                original = getattr(collection, method)

                async def pending(*args, **kwargs):
                    await original(*args, **kwargs)
                    entered.set()
                    await asyncio.Event().wait()

                with patch.object(collection, method, side_effect=pending), \
                        patch("documents.release_reservation", new_callable=AsyncMock) as release:
                    task = asyncio.create_task(self.post())
                    await asyncio.wait_for(entered.wait(), 3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                release.assert_not_awaited()
        self.assertEqual(len(self.docs.documents), 1)
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 1)
        self.assertEqual(len(self.bases.documents[self.base_id]["_document_ids"]), 2)

    async def test_cancel_verification_preserves_resources(self):
        entered = asyncio.Event()

        async def pending(*args):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(self.docs, "insert_one", side_effect=ConnectionFailure("private")), self.view(self.docs), \
                patch.object(self.docs, "find_one", side_effect=pending):
            task = asyncio.create_task(self.post())
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.retained()

    async def test_verification_timeout_is_bounded_and_preserves_resources(self):
        async def pending(*args):
            await asyncio.Event().wait()

        with patch.object(self.docs, "insert_one", side_effect=ConnectionFailure("private")), self.view(self.docs), \
                patch.object(self.docs, "find_one", side_effect=pending), \
                patch("upload_safety.VERIFICATION_TIMEOUT_SECONDS", 0.01):
            self.assertEqual((await self.post())[0], 503)
        self.retained()

    async def test_cancelled_save_cleanup_error_preserves_cancellation_and_reservation(self):
        from document_storage import StorageCleanupError
        entered, finish = threading.Event(), threading.Event()

        def failed(*args):
            entered.set()
            finish.wait(3)
            raise StorageCleanupError("private")

        with patch.object(self.storage, "save", side_effect=failed):
            task = asyncio.create_task(self.post())
            try:
                for _ in range(300):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.sleep(0)
            finally:
                finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(self.bases.documents[self.base_id]["_document_ids"])
        self.assertFalse(self.docs.documents)

    async def test_repeated_cancel_or_wait_timeout_keeps_stream_open_until_worker_finishes(self):
        for repeated in (True, False):
            with self.subTest(repeated=repeated):
                entered, finish = threading.Event(), threading.Event()
                captured = []
                original = self.storage.save

                def paused(upload, *args):
                    captured.append(upload)
                    entered.set()
                    if not finish.wait(3):
                        raise OSError("test worker timeout")
                    self.assertFalse(upload.file.closed)
                    return original(upload, *args)

                with patch.object(self.storage, "save", side_effect=paused), \
                        patch("documents.SAVE_CANCEL_WAIT_SECONDS", 0.05):
                    task = asyncio.create_task(self.post())
                    try:
                        for _ in range(300):
                            if entered.is_set():
                                break
                            await asyncio.sleep(0.005)
                        self.assertTrue(entered.is_set())
                        task.cancel()
                        await asyncio.sleep(0)
                        if repeated:
                            task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 1)
                        self.assertFalse(captured[0].file.closed)
                    finally:
                        finish.set()
                        await asyncio.shield(captured[0]._deepdocs_save_task)
                        for _ in range(100):
                            if captured[0].file.closed:
                                break
                            await asyncio.sleep(0.005)
                    self.assertTrue(captured[0].file.closed)
        self.assertFalse(self.docs.documents)
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 2)
        self.assertEqual(len(self.bases.documents[self.base_id]["_document_ids"]), 2)


class SaveLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_completion_failure_and_wrapper_cancellation(self):
        for mode in ("normal", "failure", "direct", "direct_failure", "shutdown", "repeated"):
            with self.subTest(mode=mode):
                loop = asyncio.get_running_loop()
                entered = loop.create_future()
                release, closed = threading.Event(), threading.Event()

                class Stream(io.BytesIO):
                    closes = 0

                    def close(self):
                        self.closes += 1
                        super().close()
                        closed.set()

                stream = Stream(b"synthetic PDF stream")
                upload = SimpleNamespace(file=stream)
                lifetime = SaveLifetime(upload)
                upload._deepdocs_save_lifetime = lifetime

                class Form(dict):
                    async def close(self):
                        raise AssertionError("Must use worker-aware stream finalization")

                form = Form(file=upload)

                def save():
                    loop.call_soon_threadsafe(entered.set_result, None)
                    if not release.wait(5):
                        raise AssertionError("test worker timeout")
                    self.assertFalse(stream.closed)
                    stream.read()
                    if mode in ("failure", "direct_failure"):
                        raise OSError("private failure")

                wrapper = asyncio.create_task(run_in_threadpool(lifetime.run, save))
                wrapper.add_done_callback(observe_save_result)

                async def request():
                    try:
                        await asyncio.shield(wrapper)
                    finally:
                        await close_upload_form(form)

                request_task = asyncio.create_task(request())
                await asyncio.wait_for(entered, 3)
                try:
                    if mode in ("direct", "direct_failure", "shutdown"):
                        wrapper.cancel()
                        if mode == "shutdown":
                            request_task.cancel()
                    elif mode == "repeated":
                        request_task.cancel()
                        request_task.cancel()
                    if mode in ("direct", "direct_failure", "shutdown", "repeated"):
                        with self.assertRaises(asyncio.CancelledError):
                            await request_task
                        self.assertFalse(lifetime.completed.is_set())
                        self.assertFalse(stream.closed)
                        await close_upload_form(form)  # Repeated finalizer, still running.
                        self.assertEqual(stream.closes, 0)
                    release.set()
                    if mode in ("normal", "failure"):
                        if mode == "failure":
                            with self.assertRaises(OSError):
                                await request_task
                        else:
                            await request_task
                    self.assertTrue(await asyncio.to_thread(closed.wait, 3))
                    self.assertTrue(lifetime.completed.is_set())
                    await close_upload_form(form)
                    self.assertEqual(stream.closes, 1)
                finally:
                    release.set()
                    await asyncio.gather(wrapper, request_task, return_exceptions=True)
                    self.assertTrue(await asyncio.to_thread(lifetime.completed.wait, 3))

    async def test_sealed_pending_worker_cannot_start_after_stream_closes(self):
        upload = SimpleNamespace(file=io.BytesIO(b"synthetic"))
        lifetime = SaveLifetime(upload)
        upload._deepdocs_save_lifetime = lifetime
        await close_upload_form({"file": upload})
        self.assertTrue(upload.file.closed)
        with self.assertRaises(UploadUncertain):
            lifetime.run(lambda: self.fail("Sealed worker ran"))

    async def test_close_failure_is_sanitized_and_not_repeated(self):
        class Stream:
            closes = 0

            def close(self):
                self.closes += 1
                raise OSError("private path")

        upload = SimpleNamespace(file=Stream())
        lifetime = SaveLifetime(upload)
        upload._deepdocs_save_lifetime = lifetime
        lifetime.run(lambda: None)
        with self.assertLogs("upload_safety", level="WARNING") as logs:
            await close_upload_form({"file": upload})
            await close_upload_form({"file": upload})
        self.assertEqual(upload.file.closes, 1)
        self.assertNotIn("private path", str(logs.output))
