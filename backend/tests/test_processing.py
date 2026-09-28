"""Synthetic PDFs, in-memory MongoDB, and real ASGI requests. No Atlas or .env."""
import asyncio
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pymupdf
from bson import ObjectId
from pymongo.errors import ConnectionFailure

from config import ProcessingSettings, ConfigurationError, load_processing_settings
from document_routes import processing_settings
from document_processing import ensure_chunk_indexes
from pdf_processing import normalize_text, chunk_page, extract_chunks, ProcessingFailure
from main import app
from auth import get_current_user
import test_documents as document_test_helpers
from test_documents import request


class TextTests(unittest.TestCase):
    def test_conservative_normalization(self):
        raw = "  Heading\r\n\r\n\r\nItem\t  12.50!\r- list item\r  Another line  "
        self.assertEqual(normalize_text(raw), "Heading\n\nItem 12.50!\n- list item\nAnother line")

    def test_determinism_order_overlap_and_whole_words(self):
        text = "\n\n".join(" ".join(f"word{n}" for n in range(i, i + 30)) + "." for i in range(0, 240, 30))
        values = chunk_page(text, 200, 40)
        self.assertEqual(values, chunk_page(text, 200, 40))
        self.assertGreater(len(values), 2)
        for value in values:
            self.assertTrue(value)
            self.assertLessEqual(len(value), 240)
            self.assertIn(value, text)
        for previous, current in zip(values, values[1:]):
            self.assertTrue(any(previous.endswith(current[:n]) for n in range(1, 41)))
        for word in text.split():
            self.assertTrue(any(word in value.split() for value in values))

    def test_long_words_empty_pages_and_no_overlap(self):
        self.assertEqual(chunk_page("", 100, 10), [])
        self.assertEqual(chunk_page("x" * 250, 100, 10), ["x" * 250])
        text = " ".join(f"token{i}" for i in range(100))
        values = chunk_page(text, 100, 0)
        self.assertEqual(" ".join(values), text)

    def test_config_defaults_and_safe_errors(self):
        with patch("config.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_processing_settings(), ProcessingSettings())
        for config in [
            {"PDF_CHUNK_TARGET": "private-invalid"}, {"PDF_CHUNK_TARGET": "99"},
            {"PDF_CHUNK_OVERLAP": "500"}, {"PDF_CHUNK_OVERLAP": "-1"},
            {"PDF_MAX_PAGES": "0"}, {"PDF_MAX_CHUNKS": "0"},
            {"PDF_MAX_TEXT_CHARACTERS": "0"},
        ]:
            with self.subTest(config=list(config)), patch("config.dotenv_values", return_value=config), patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(ConfigurationError) as error:
                    load_processing_settings()
                self.assertNotIn("private-invalid", str(error.exception))


# Reuse setup helpers without inheriting/re-running the original test methods.
class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = document_test_helpers.DocumentTests.asyncSetUp
    asyncTearDown = document_test_helpers.DocumentTests.asyncTearDown

    async def make_document(self, pages=("A first paragraph. Clear source text.",)):
        pdf = pymupdf.open()
        for text in pages:
            page = pdf.new_page()
            if text:
                page.insert_textbox(pymupdf.Rect(40, 40, 550, 800), text, fontsize=10)
        data = pdf.tobytes()
        pdf.close()
        # Reuse the upload API with a suitable synthetic-fixture size limit.
        from document_routes import upload_settings
        from config import DocumentSettings
        app.dependency_overrides[upload_settings] = lambda: DocumentSettings(1024 * 1024)
        status, body, _ = await request("POST", self.path, data)
        self.assertEqual(status, 201)
        app.dependency_overrides[processing_settings] = lambda: ProcessingSettings(chunk_target=200, chunk_overlap=30)
        return body

    async def process(self, body):
        return await request("POST", "/api/documents/" + body["id"] + "/process")

    async def inspect(self, body):
        return await request("GET", "/api/documents/" + body["id"] + "/chunks")

    async def test_one_page_success_status_counts_public_provenance(self):
        body = await self.make_document()
        original_insert = self.chunks.insert_many

        async def observe(values, **kwargs):
            self.assertEqual(self.docs.documents[ObjectId(body["id"])]["status"], "processing")
            await original_insert(values, **kwargs)

        self.chunks.insert_many = observe
        status, result, _ = await self.process(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["page_count"], 1)
        self.assertEqual(result["chunk_count"], 1)
        self.assertTrue(result["processed_at"].endswith("Z"))
        self.assertIsNone(result["processing_error"])
        status, chunks, _ = await self.inspect(body)
        self.assertEqual(status, 200)
        self.assertEqual(set(chunks[0]), {
            "id", "document_id", "knowledge_base_id", "source_filename", "chunk_index",
            "text", "page_start", "page_end", "character_count", "created_at",
        })
        self.assertEqual(chunks[0]["source_filename"], "notes.pdf")
        self.assertEqual(chunks[0]["document_id"], body["id"])
        self.assertEqual(chunks[0]["knowledge_base_id"], str(self.base_id))
        self.assertEqual(chunks[0]["page_start"], 1)
        stored = next(iter(self.chunks.documents.values()))
        for field in ["document_id", "knowledge_base_id", "owner_id"]:
            self.assertIsInstance(stored[field], ObjectId)
        self.assertNotIn("_operation", self.docs.documents[ObjectId(body["id"])])

    async def test_multiple_pages_empty_page_and_chunk_order(self):
        long = "\n\n".join("Sentence about a source document. " * 8 for _ in range(4))
        body = await self.make_document([long, "", "Last page has separate content."])
        status, result, _ = await self.process(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["page_count"], 3)
        _, chunks, _ = await self.inspect(body)
        self.assertGreater(len(chunks), 3)
        self.assertEqual([c["chunk_index"] for c in chunks], list(range(len(chunks))))
        self.assertEqual({c["page_start"] for c in chunks}, {1, 3})
        for chunk in chunks:
            self.assertEqual(chunk["page_start"], chunk["page_end"])
            self.assertEqual(chunk["character_count"], len(chunk["text"]))
            self.assertTrue(chunk["text"])

    async def test_reprocessing_replaces_without_duplicates(self):
        body = await self.make_document()
        await self.process(body)
        old_ids = set(self.chunks.documents)
        _, first, _ = await self.inspect(body)
        await self.process(body)
        _, second, _ = await self.inspect(body)
        self.assertEqual(len(self.chunks.documents), len(first))
        self.assertTrue(old_ids.isdisjoint(self.chunks.documents))
        self.assertEqual([c["text"] for c in first], [c["text"] for c in second])

    async def test_no_text_and_malformed_fail_without_chunks(self):
        body = await self.make_document([""])
        status, error, _ = await self.process(body)
        self.assertEqual(status, 422)
        self.assertIn("No extractable text", error["detail"])
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["status"], "failed")
        self.assertFalse(self.chunks.documents)
        path = self.root / (body["id"] + ".pdf")
        path.write_bytes(b"%PDF-not-valid")
        status, error, _ = await self.process(body)
        self.assertEqual(status, 422)
        self.assertNotIn(str(path), error["detail"])

    async def test_missing_and_unsafe_source_never_extracted(self):
        body = await self.make_document()
        record = self.docs.documents[ObjectId(body["id"])]
        path = self.root / record["stored_filename"]
        path.unlink()
        with patch("document_processing.extract_chunks") as extract:
            self.assertEqual((await self.process(body))[0], 503)
            extract.assert_not_called()
        record["storage_path"] = "../private.pdf"
        with patch("document_processing.extract_chunks") as extract:
            self.assertEqual((await self.process(body))[0], 503)
            extract.assert_not_called()
        self.assertFalse(self.chunks.documents)

    async def test_failed_reprocessing_preserves_old_generation(self):
        body = await self.make_document()
        await self.process(body)
        _, original, _ = await self.inspect(body)
        with patch("document_processing.extract_chunks", side_effect=ProcessingFailure("No extractable text was found. OCR is not supported.")):
            self.assertEqual((await self.process(body))[0], 422)
        self.assertEqual((await self.inspect(body))[1], original)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["status"], "failed")

    async def test_partial_insert_failure_is_invisible_and_cleaned(self):
        body = await self.make_document()
        await self.process(body)
        _, original, _ = await self.inspect(body)

        async def partial(values, **kwargs):
            await self.chunks.insert_one(values[0])
            raise ConnectionFailure("private-driver-details")

        self.chunks.insert_many = partial
        status, error, _ = await self.process(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private-driver", str(error))
        self.assertEqual((await self.inspect(body))[1], original)
        self.assertEqual(len(self.chunks.documents), len(original))
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["status"], "failed")

    async def test_ambiguous_activation_retains_old_and_staged_data_locked(self):
        body = await self.make_document()
        await self.process(body)
        _, original, _ = await self.inspect(body)
        original_update = self.docs.update_one

        async def fail_activation(query, update):
            if update.get("$set", {}).get("status") == "processed":
                raise ConnectionFailure("private-activation")
            return await original_update(query, update)

        self.docs.update_one = fail_activation
        self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual((await self.inspect(body))[1], original)
        record = self.docs.documents[ObjectId(body["id"])]
        self.assertEqual(record["status"], "processing")
        self.assertIn("_operation", record)
        self.assertEqual((await self.process(body))[0], 409)

    async def test_ownership_invalid_id_and_unauthenticated(self):
        body = await self.make_document()
        self.current_owner = self.other
        for method, suffix in [("POST", "process"), ("GET", "chunks")]:
            self.assertEqual((await request(method, "/api/documents/" + body["id"] + "/" + suffix))[0], 404)
            self.assertEqual((await request(method, "/api/documents/not-id/" + suffix))[0], 422)
        self.assertFalse(self.chunks.documents)
        del app.dependency_overrides[get_current_user]
        for method, suffix in [("POST", "process"), ("GET", "chunks")]:
            self.assertEqual((await request(method, "/api/documents/" + body["id"] + "/" + suffix))[0], 401)

    async def test_process_and_delete_conflict_with_active_operation(self):
        body = await self.make_document()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.chunks.insert_many

        async def wait_insert(values, **kwargs):
            entered.set()
            await release.wait()
            await original(values, **kwargs)

        self.chunks.insert_many = wait_insert
        task = asyncio.create_task(self.process(body))
        await entered.wait()
        try:
            self.assertEqual((await self.process(body))[0], 409)
            self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 409)
            self.assertEqual((await self.inspect(body))[1], [])
        finally:
            release.set()
            self.assertEqual((await task)[0], 200)

    async def test_delete_processed_document_removes_only_owned_chunks(self):
        body = await self.make_document()
        await self.process(body)
        unrelated = copy.deepcopy(next(iter(self.chunks.documents.values())))
        unrelated["_id"], unrelated["owner_id"] = ObjectId(), self.other
        self.chunks.documents[unrelated["_id"]] = unrelated
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)
        self.assertEqual(list(self.chunks.documents), [unrelated["_id"]])
        self.assertFalse((self.root / (body["id"] + ".pdf")).exists())
        self.assertEqual((await self.inspect(body))[0], 404)
        self.assertEqual((await request("DELETE", "/api/knowledge-bases/" + str(self.base_id)))[0], 204)

    async def test_chunk_delete_failure_keeps_retryable_metadata(self):
        body = await self.make_document()
        await self.process(body)
        original = self.chunks.delete_many
        self.chunks.delete_many = AsyncMock(side_effect=ConnectionFailure("private"))
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 503)
        self.assertEqual(self.docs.documents[ObjectId(body["id"])]["status"], "failed")
        self.chunks.delete_many = original
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)

    async def test_extraction_safety_limits(self):
        body = await self.make_document(["Page one.", "Page two."])
        path = self.root / (body["id"] + ".pdf")
        for settings in [ProcessingSettings(max_pages=1), ProcessingSettings(max_text_characters=2), ProcessingSettings(max_chunks=1)]:
            with self.subTest(settings=settings):
                with self.assertRaises(ProcessingFailure):
                    extract_chunks(path, settings)

    async def test_failed_cleanup_never_exposes_staged_or_inactive_chunks(self):
        body = await self.make_document()
        await self.process(body)
        _, original, _ = await self.inspect(body)
        real_insert = self.chunks.insert_many
        real_delete = self.chunks.delete_many

        async def partial(values, **kwargs):
            await self.chunks.insert_one(values[0])
            raise ConnectionFailure("private-insert")

        self.chunks.insert_many = partial
        self.chunks.delete_many = AsyncMock(side_effect=ConnectionFailure("private-cleanup"))
        self.assertEqual((await self.process(body))[0], 503)
        self.assertEqual((await self.inspect(body))[1], original)
        self.chunks.insert_many = real_insert
        self.assertEqual((await self.process(body))[0], 200)
        _, current, _ = await self.inspect(body)
        self.assertEqual(len(current), len(original))
        self.assertGreater(len(self.chunks.documents), len(current))
        self.chunks.delete_many = real_delete
        self.assertEqual((await request("DELETE", "/api/documents/" + body["id"]))[0], 204)
        self.assertFalse(self.chunks.documents)

    async def test_chunk_generation_error_is_sanitized_and_keeps_previous_set(self):
        body = await self.make_document()
        await self.process(body)
        _, original, _ = await self.inspect(body)
        with patch("pdf_processing.chunk_page", side_effect=RuntimeError("private-internal-details")):
            status, result, _ = await self.process(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private-internal", str(result))
        self.assertEqual((await self.inspect(body))[1], original)
        self.assertNotIn("private-internal", self.docs.documents[ObjectId(body["id"])]["processing_error"])

    async def test_chunk_database_failure_and_processing_config_errors_are_safe(self):
        body = await self.make_document()
        await self.process(body)
        self.chunks.failure = ConnectionFailure("private-driver")
        self.assertEqual((await self.inspect(body))[0], 503)
        app.dependency_overrides.pop(processing_settings)
        with patch("document_routes.load_processing_settings", side_effect=ConfigurationError("private-config")):
            status, result, _ = await self.process(body)
        self.assertEqual(status, 503)
        self.assertNotIn("private-config", str(result))

    async def test_protected_pdf_and_source_size_limit(self):
        body = await self.make_document()
        path = self.root / (body["id"] + ".pdf")
        pdf = pymupdf.open()
        pdf.new_page().insert_text((50, 50), "Protected text")
        encrypted = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="synthetic-only", owner_pw="synthetic-owner")
        pdf.close()
        path.write_bytes(encrypted)
        self.assertEqual((await self.process(body))[0], 422)
        with self.assertRaises(ProcessingFailure):
            extract_chunks(path, ProcessingSettings(max_source_bytes=5))

    async def test_chunk_index(self):
        collection = SimpleNamespace(create_index=AsyncMock())
        await ensure_chunk_indexes(SimpleNamespace(get_collection=lambda name: collection))
        collection.create_index.assert_awaited_once_with(
            [("owner_id", 1), ("document_id", 1), ("generation", 1), ("chunk_index", 1)],
            name="chunks_owner_document_generation_order",
        )
