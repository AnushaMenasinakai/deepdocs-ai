"""Real ASGI multipart/storage tests; mocked MongoDB and temporary PDFs only."""
import asyncio
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import ObjectId
from pymongo.errors import ConnectionFailure
from starlette.datastructures import UploadFile, Headers
from auth import get_current_user
from config import DocumentSettings, ConfigurationError, load_document_settings, Settings
from database import get_database, database_lifespan
from document_routes import upload_settings
from document_storage import DocumentStorage, get_document_storage, StorageError
from documents import ensure_document_indexes
from main import app
from user_schemas import UserResponse
from test_knowledge_bases import MemoryCollection

PDF = b"%PDF-1.7\nfixture only; not processed\n%%EOF"
PUBLIC = {"id", "knowledge_base_id", "filename", "content_type", "file_size", "status", "created_at", "updated_at"}


class Collection(MemoryCollection):
    async def find_one_and_update(self, query, update, **kwargs):
        matches = self.matching(query)
        if not matches:
            return None
        old = copy.deepcopy(matches[0])
        self.apply(matches[0], update)
        return old

    def apply(self, document, update):
        for key, value in update.get("$addToSet", {}).items():
            if value not in document.setdefault(key, []):
                document[key].append(value)
        for key, value in update.get("$pull", {}).items():
            document[key] = [item for item in document.get(key, []) if item != value]
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value

    async def update_one(self, query, update):
        matches = self.matching(query)
        if matches:
            self.apply(matches[0], update)
        return SimpleNamespace(matched_count=int(bool(matches)))


async def request(method, path, content=None, filename="notes.pdf", mime="application/pdf", raw=None, content_type=None):
    if raw is None and content is not None:
        raw = (
            b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="' +
            filename.encode() + b'"\r\nContent-Type: ' + mime.encode() + b"\r\n\r\n" +
            content + b"\r\n--test-boundary--\r\n"
        )
    raw = raw or b""
    headers = [(b"content-type", (content_type or "multipart/form-data; boundary=test-boundary").encode())]
    # Several receive events prove enforcement independent of Content-Length.
    chunks = [raw[index:index + 8192] for index in range(0, len(raw), 8192)] or [b""]
    messages = []
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        chunk = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "server": ("test", 80), "client": ("test", 123),
    }, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return start["status"], json.loads(body) if body else None, calls


class DocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Temp roots live under ignored verification, never persistent storage.
        verification = Path(__file__).resolve().parents[2] / ".verification"
        verification.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=verification)
        self.root = Path(self.temp.name) / "pdfs"
        self.storage = DocumentStorage(self.root)
        self.bases, self.docs = Collection(), Collection()
        self.database = SimpleNamespace(get_collection=lambda name: {
            "knowledge_bases": self.bases, "documents": self.docs,
        }[name])
        self.owner, self.other, self.base_id = ObjectId(), ObjectId(), ObjectId()
        now = datetime.now(timezone.utc)
        self.bases.documents[self.base_id] = {
            "_id": self.base_id, "owner_id": self.owner, "name": "Notes", "description": None,
            "created_at": now, "updated_at": now,
        }
        self.current_owner = self.owner
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_database] = lambda: self.database
        app.dependency_overrides[get_document_storage] = lambda: self.storage
        app.dependency_overrides[upload_settings] = lambda: DocumentSettings(100)
        app.dependency_overrides[get_current_user] = lambda: UserResponse(
            id=str(self.current_owner), name="Offline", email="user@example.com", created_at=now,
        )
        self.path = "/api/knowledge-bases/" + str(self.base_id) + "/documents"

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        self.temp.cleanup()

    async def upload(self, **kwargs):
        status, body, _ = await request("POST", self.path, content=PDF, **kwargs)
        self.assertEqual(status, 201)
        return body

    def assert_no_files(self):
        self.assertEqual(list(self.root.glob("*")), [])

    async def test_valid_upload_safe_metadata_generated_paths_and_equal_utc_dates(self):
        body = await self.upload(filename="../../outside.pdf")
        self.assertEqual(set(body), PUBLIC)
        self.assertEqual(body["filename"], "outside.pdf")
        self.assertEqual(body["status"], "uploaded")
        self.assertEqual(body["file_size"], len(PDF))
        self.assertEqual(body["created_at"], body["updated_at"])
        self.assertTrue(body["created_at"].endswith("Z"))
        record = self.docs.documents[ObjectId(body["id"])]
        for field in ["_id", "owner_id", "knowledge_base_id"]:
            self.assertIsInstance(record[field], ObjectId)
        self.assertEqual(record["owner_id"], self.owner)
        self.assertEqual(record["storage_path"], body["id"] + ".pdf")
        self.assertEqual((self.root / record["stored_filename"]).read_bytes(), PDF)
        self.assertNotIn(str(self.root), json.dumps(body))

    async def test_validation_and_oversize_leave_no_file_or_metadata(self):
        cases = [
            (b"", "notes.pdf", "application/pdf", 422),
            (PDF, "notes.txt", "application/pdf", 415),
            (PDF, "notes.pdf", "text/plain", 415),
            (b"not a pdf", "notes.pdf", "application/pdf", 415),
            (b"%PDF-" + b"x" * 100, "notes.pdf", "application/pdf", 413),
            (b"%PDF-" + b"x" * 100000, "notes.pdf", "application/pdf", 413),
        ]
        for content, filename, mime, expected in cases:
            with self.subTest(expected=expected, size=len(content)):
                status, body, _ = await request("POST", self.path, content, filename, mime)
                self.assertEqual(status, expected)
                self.assertNotIn(str(self.root), json.dumps(body))
                self.assert_no_files()
                self.assertFalse(self.docs.documents)
                self.assertFalse(self.bases.documents[self.base_id].get("_document_ids"))

    async def test_exact_limit_and_uppercase_extension_accepted(self):
        status, body, _ = await request("POST", self.path, b"%PDF-" + b"x" * 95, "NOTES.PDF")
        self.assertEqual(status, 201)
        self.assertEqual(body["file_size"], 100)

    async def test_missing_file_wrong_field_multiple_files_and_malformed_multipart(self):
        part = b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\nContent-Type: application/pdf\r\n\r\n' + PDF + b"\r\n"
        for raw, content_type in [
            (b"--test-boundary--\r\n", None),
            (part.replace(b'name="file"', b'name="other"') + b"--test-boundary--\r\n", None),
            (part + part + b"--test-boundary--\r\n", None),
            (b"broken", "multipart/form-data"),
            (b"{}", "application/json"),
        ]:
            with self.subTest(content_type=content_type):
                status, _, _ = await request("POST", self.path, raw=raw, content_type=content_type)
                self.assertIn(status, [415, 422])
                self.assert_no_files()
                self.assertFalse(self.docs.documents)

    async def test_auth_required_for_all_endpoints_before_body_read(self):
        del app.dependency_overrides[get_current_user]
        for method, path in [
            ("POST", self.path), ("GET", self.path),
            ("GET", "/api/documents/" + str(ObjectId())),
            ("DELETE", "/api/documents/" + str(ObjectId())),
        ]:
            with self.subTest(method=method, path=path):
                status, _, calls = await request(method, path, PDF)
                self.assertEqual(status, 401)
                self.assertEqual(calls, 0)
        self.assert_no_files()

    async def test_missing_and_other_owner_bases_same_404_before_parsing(self):
        for base_id in [self.base_id, ObjectId()]:
            self.current_owner = self.other
            for method in ["GET", "POST"]:
                with self.subTest(method=method):
                    status, body, calls = await request(method, "/api/knowledge-bases/" + str(base_id) + "/documents", PDF)
                    self.assertEqual((status, body), (404, {"detail": "Knowledge Base not found."}))
                    self.assertEqual(calls, 0)
        self.assert_no_files()

    async def test_insert_failure_compensates_file_and_reservation(self):
        self.docs.failure = ConnectionFailure("private-driver-path")
        status, body, _ = await request("POST", self.path, PDF)
        self.assertEqual(status, 503)
        self.assertNotIn("private-driver-path", json.dumps(body))
        self.assert_no_files()
        self.assertFalse(self.bases.documents[self.base_id]["_document_ids"])

    async def test_write_failure_never_inserts_and_cleans_partial_file(self):
        with patch("document_storage.Path.open", side_effect=OSError("private-filesystem-path")):
            status, body, _ = await request("POST", self.path, PDF)
        self.assertEqual(status, 503)
        self.assertNotIn("private-filesystem-path", json.dumps(body))
        self.assertFalse(self.docs.documents)
        self.assert_no_files()

    async def test_list_empty_then_owner_base_filtered_and_stable_order(self):
        self.assertEqual((await request("GET", self.path))[:2], (200, []))
        first, second = await self.upload(), await self.upload()
        first_record = self.docs.documents[ObjectId(first["id"])]
        self.docs.documents[ObjectId(second["id"])]["created_at"] = first_record["created_at"]
        for changes in [{"owner_id": self.other}, {"knowledge_base_id": ObjectId()}]:
            unrelated = {**first_record, "_id": ObjectId(), **changes}
            self.docs.documents[unrelated["_id"]] = unrelated
        status, body, _ = await request("GET", self.path)
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in body], sorted([first["id"], second["id"]], reverse=True))
        self.assertTrue(all(set(item) == PUBLIC for item in body))

    async def test_metadata_and_cross_user_isolation_get_delete(self):
        body = await self.upload()
        path = "/api/documents/" + body["id"]
        self.assertEqual((await request("GET", path))[:2], (200, body))
        self.current_owner = self.other
        for method in ["GET", "DELETE"]:
            status, response, _ = await request(method, path)
            self.assertEqual((status, response), (404, {"detail": "Document not found."}))
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 1)
        self.assertEqual(len(self.docs.documents), 1)

    async def test_owned_delete_removes_only_its_file_and_metadata(self):
        first, second = await self.upload(), await self.upload()
        path = "/api/documents/" + first["id"]
        self.assertEqual((await request("DELETE", path))[:2], (204, None))
        self.assertFalse((self.root / (first["id"] + ".pdf")).exists())
        self.assertTrue((self.root / (second["id"] + ".pdf")).exists())
        self.assertEqual((await request("DELETE", path))[0], 404)
        self.assertIn(self.base_id, self.bases.documents)

    async def test_unsafe_or_different_document_path_never_deleted(self):
        body = await self.upload()
        other = await self.upload()
        outside = Path(self.temp.name) / "outside.pdf"
        outside.write_bytes(PDF)
        record = self.docs.documents[ObjectId(body["id"])]
        original = copy.deepcopy(record)
        for key in [str(outside), "../outside.pdf", other["id"] + ".pdf"]:
            record["storage_path"] = record["stored_filename"] = key
            status, response, _ = await request("DELETE", "/api/documents/" + body["id"])
            self.assertEqual(status, 503)
            self.assertTrue(outside.exists())
            self.assertTrue((self.root / (other["id"] + ".pdf")).exists())
            self.assertIn(ObjectId(body["id"]), self.docs.documents)
            self.assertNotIn(str(outside), json.dumps(response))
        record.update(original)

    async def test_delete_io_failure_retains_metadata_for_retry(self):
        body = await self.upload()
        with patch.object(self.storage, "delete", side_effect=OSError("private-detail")):
            self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertIn(ObjectId(body["id"]), self.docs.documents)
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)

    async def test_metadata_delete_failure_can_be_retried_with_missing_file(self):
        body = await self.upload()
        original = self.docs.delete_one
        self.docs.delete_one = AsyncMock(side_effect=ConnectionFailure("private"))
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertIn(ObjectId(body["id"]), self.docs.documents)
        self.assert_no_files()
        self.docs.delete_one = original
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)

    async def test_kb_conflict_until_documents_deleted_and_foreign_docs_do_not_block(self):
        body = await self.upload()
        base_path = "/api/knowledge-bases/" + str(self.base_id)
        self.assertEqual((await request("DELETE", base_path))[0], 409)
        record = copy.deepcopy(self.docs.documents[ObjectId(body["id"])])
        await request("DELETE", "/api/documents/" + body["id"])
        record["_id"], record["owner_id"] = ObjectId(), self.other
        self.docs.documents[record["_id"]] = record
        self.assertEqual((await request("DELETE", base_path))[0], 204)

    async def test_inflight_reservation_blocks_kb_deletion(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.docs.insert_one

        async def paused_insert(document):
            entered.set()
            await release.wait()
            await original(document)

        self.docs.insert_one = paused_insert
        task = asyncio.create_task(request("POST", self.path, PDF))
        await entered.wait()
        try:
            self.assertEqual((await request("DELETE", "/api/knowledge-bases/" + str(self.base_id)))[0], 409)
        finally:
            release.set()
            self.assertEqual((await task)[0], 201)

    async def test_kb_delete_revision_guard_detects_interleaved_upload(self):
        original = self.docs.find_one

        async def interleaved(query):
            result = await original(query)
            await self.upload()
            return result

        self.docs.find_one = interleaved
        self.assertEqual((await request("DELETE", "/api/knowledge-bases/" + str(self.base_id)))[0], 409)
        self.assertIn(self.base_id, self.bases.documents)

    async def test_invalid_ids_and_database_failures_are_safe(self):
        for method, path in [
            ("GET", "/api/documents/not-id"), ("DELETE", "/api/documents/not-id"),
            ("POST", "/api/knowledge-bases/not-id/documents"), ("GET", "/api/knowledge-bases/not-id/documents"),
        ]:
            self.assertEqual((await request(method, path, PDF))[0], 422)
        self.docs.failure = ConnectionFailure("private-driver-detail")
        for method, path in [("GET", self.path), ("GET", "/api/documents/" + str(ObjectId())), ("DELETE", "/api/documents/" + str(ObjectId()))]:
            status, body, _ = await request(method, path)
            self.assertEqual(status, 503)
            self.assertNotIn("private-driver-detail", json.dumps(body))

    async def test_partial_write_failure_removes_file_and_never_inserts(self):
        original_open = Path.open

        class FailingWriter:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                return self

            def write(self, chunk):
                self.file.write(chunk[:5])
                raise OSError("private-write-failure")

            def __exit__(self, *args):
                self.file.close()

        def failing_open(path, mode="r", *args, **kwargs):
            file = original_open(path, mode, *args, **kwargs)
            return FailingWriter(file) if mode == "xb" else file

        with patch("document_storage.Path.open", new=failing_open):
            status, response, _ = await request("POST", self.path, PDF)
        self.assertEqual(status, 503)
        self.assertNotIn("private-write-failure", json.dumps(response))
        self.assert_no_files()
        self.assertFalse(self.docs.documents)

    async def test_file_cleanup_failure_keeps_metadata_and_kb_protected(self):
        body = await self.upload()
        with patch("document_storage.Path.unlink", side_effect=OSError("private-cleanup")):
            self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertIn(ObjectId(body["id"]), self.docs.documents)
        self.assertEqual((await request("DELETE", "/api/knowledge-bases/" + str(self.base_id)))[0], 409)

    async def test_symlink_reference_is_rejected_without_touching_target(self):
        body = await self.upload()
        file = self.root / (body["id"] + ".pdf")
        original = Path.is_symlink
        with patch("document_storage.Path.is_symlink", new=lambda path: True if path == file else original(path)):
            self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertEqual(file.read_bytes(), PDF)
        self.assertIn(ObjectId(body["id"]), self.docs.documents)

    async def test_invalid_storage_configuration_does_not_read_request_body(self):
        app.dependency_overrides.pop(upload_settings)
        with patch("document_routes.load_document_settings", side_effect=ConfigurationError("private-setting")):
            status, body, calls = await request("POST", self.path, PDF)
        self.assertEqual(status, 503)
        self.assertEqual(calls, 0)
        self.assertNotIn("private-setting", json.dumps(body))

    async def test_failed_upload_cleanup_retains_kb_reservation(self):
        self.docs.failure = ConnectionFailure("private-insert")
        with patch.object(self.storage, "delete", side_effect=OSError("private-cleanup")):
            self.assertEqual((await request("POST", self.path, PDF))[0], 503)
        self.docs.failure = None
        self.assertTrue(self.bases.documents[self.base_id]["_document_ids"])
        self.assertEqual((await request("DELETE", "/api/knowledge-bases/" + str(self.base_id)))[0], 409)

    async def test_cancel_during_file_write_waits_then_cleans_up(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        original = self.storage.save

        def paused_save(*args):
            entered.set()
            if not release.wait(timeout=5):
                raise OSError("Test write timed out.")
            return original(*args)

        with patch.object(self.storage, "save", new=paused_save):
            task = asyncio.create_task(request("POST", self.path, PDF))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(entered.is_set())
                task.cancel()
                await asyncio.sleep(0)
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assert_no_files()
        self.assertFalse(self.docs.documents)
        self.assertFalse(self.bases.documents[self.base_id]["_document_ids"])

    def test_openapi_exposes_bearer_and_file_input(self):
        schema = app.openapi()
        for path, methods in [
            ("/api/knowledge-bases/{knowledge_base_id}/documents", ["post", "get"]),
            ("/api/documents/{document_id}", ["get", "delete"]),
        ]:
            for method in methods:
                self.assertEqual(schema["paths"][path][method]["security"], [{"HTTPBearer": []}])
        self.assertIn("multipart/form-data", schema["paths"]["/api/knowledge-bases/{knowledge_base_id}/documents"]["post"]["requestBody"]["content"])


class DocumentConfigurationTests(unittest.TestCase):
    def test_defaults_precedence_and_invalid_limits(self):
        with patch("config.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_document_settings().max_upload_bytes, 10 * 1024 * 1024)
        with patch("config.dotenv_values", return_value={"DOCUMENT_MAX_UPLOAD_BYTES": "10"}), patch.dict(os.environ, {"DOCUMENT_MAX_UPLOAD_BYTES": "20"}, clear=True):
            self.assertEqual(load_document_settings().max_upload_bytes, 20)
        for value in ["0", "-1", "private-invalid", str(101 * 1024 * 1024), None]:
            with self.subTest(value=value), patch("config.dotenv_values", return_value={"DOCUMENT_MAX_UPLOAD_BYTES": value}), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError):
                    load_document_settings()


class DocumentIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_index_and_safe_startup_failure(self):
        collection = SimpleNamespace(create_index=AsyncMock())
        database = SimpleNamespace(get_collection=MagicMock(return_value=collection))
        await ensure_document_indexes(database)
        collection.create_index.assert_awaited_once_with(
            [("owner_id", 1), ("knowledge_base_id", 1), ("created_at", -1), ("_id", -1)],
            name="documents_owner_base_created",
        )
        collection.create_index.side_effect = ConnectionFailure("private-detail")
        other = SimpleNamespace(create_index=AsyncMock())
        database.get_collection.side_effect = lambda name: collection if name == "documents" else other
        database.command = AsyncMock(return_value={"ok": 1})
        client = MagicMock()
        client.get_database.return_value = database
        client.close = AsyncMock()
        with patch("database.load_settings", return_value=Settings("mock-only")), patch("database.AsyncMongoClient", return_value=client), patch("database.logger") as logger:
            async with database_lifespan(app):
                self.assertTrue(app.state.users_index_ready)
            self.assertNotIn("private-detail", str(logger.mock_calls))
            logger.error.assert_called_once()
            client.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
