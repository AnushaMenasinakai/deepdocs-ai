"""Offline contracts and legacy lifecycle uncertainty compatibility."""
import unittest
from unittest.mock import patch
from bson import ObjectId
from document_storage import DocumentStorage, StorageError, StorageCleanupError
from storage_contract import (LocalPDFStorage, DeletionOutcome, DeletionUncertain,
                              StorageUncertain, compensation_document)
import test_gridfs_storage as grid_helpers
import test_documents as document_helpers


class StorageContractTests(unittest.TestCase):
    setUp = grid_helpers.GridFSStorageTests.setUp
    upload = grid_helpers.GridFSStorageTests.upload
    save = grid_helpers.GridFSStorageTests.save

    def local(self):
        return LocalPDFStorage(self.temp.name, owner_id=self.owner, knowledge_base_id=self.base)

    def test_reference_compatible(self):
        doc = self.save()
        self.assertEqual(self.storage.reference(doc), self.identifier)
        local = self.local()
        receipt = local.save(self.upload(), self.identifier, 100)
        scoped = compensation_document(self.identifier, self.owner, self.base, receipt)
        self.assertEqual(local.reference(scoped), DocumentStorage(self.temp.name).reference(scoped))

    def test_all_scoped_operations_reject_wrong_ids(self):
        doc = self.save()
        for storage in (self.storage, self.local()):
            for key in ('_id', 'owner_id', 'knowledge_base_id'):
                for value in ('untrusted', None, ObjectId()):
                    for name in ('reference', 'exists', 'materialize', 'delete_confirmed', 'compensate'):
                        with self.subTest(key=key, method=name), self.assertRaises(StorageError):
                            getattr(storage, name)({**doc, key:value})

    def test_local_legacy_return_and_explicit_absence(self):
        local = self.local()
        doc = compensation_document(self.identifier, self.owner, self.base,
                                    local.save(self.upload(), self.identifier, 100))
        self.assertIsNone(local.delete(doc))
        self.assertIsNone(local.delete(doc))
        self.assertIs(local.delete_confirmed(doc), DeletionOutcome.ABSENT)

    def test_local_confirmed_delete(self):
        local = self.local()
        doc = compensation_document(self.identifier, self.owner, self.base,
                                    local.save(self.upload(), self.identifier, 100))
        self.assertIs(local.compensate(doc), DeletionOutcome.DELETED)

    def test_scoped_gridfs_compensation(self):
        receipt = self.storage.save(self.upload(), self.identifier, 100)
        with self.assertRaises(StorageError):
            self.storage.compensate({'_id':self.identifier, **receipt})
        doc = compensation_document(self.identifier, self.owner, self.base, receipt)
        self.assertIs(self.storage.compensate(doc), DeletionOutcome.DELETED)
        with self.assertRaises(DeletionUncertain) as error:
            self.storage.compensate(doc)
        self.assertIs(error.exception.outcome, DeletionOutcome.UNCONFIRMED)
        self.assertEqual(self.bucket.deletes, 1)

    def test_conflicting_compensation_receipt_rejected(self):
        for key in ('_id', 'owner_id', 'knowledge_base_id'):
            with self.assertRaises(StorageError):
                compensation_document(self.identifier, self.owner, self.base, {key:ObjectId()})

    def test_uncertainty_uses_legacy_reservation_retention_category(self):
        self.assertIsInstance(StorageUncertain(), StorageCleanupError)
        for value in DeletionOutcome:
            with self.assertRaises(TypeError): bool(value)


class UploadUncertaintyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = document_helpers.DocumentTests.asyncSetUp
    asyncTearDown = document_helpers.DocumentTests.asyncTearDown

    async def test_uncertain_save_never_releases_reservation(self):
        from documents import upload_document
        from types import SimpleNamespace
        import io
        upload = SimpleNamespace(filename='sample.pdf', content_type='application/pdf', file=io.BytesIO(b'%PDF-'))
        with patch.object(self.storage, 'save', side_effect=StorageUncertain('Outcome uncertain')):
            with self.assertRaises(StorageUncertain):
                await upload_document(self.database, self.storage, self.owner, self.base_id, upload, 100)
        self.assertEqual(len(self.bases.documents[self.base_id]['_document_ids']), 1)
        self.assertEqual(self.docs.documents, {})
