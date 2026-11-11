"""Offline storage adapter tests. No MongoClient or remote services."""
import io
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from bson import ObjectId
from gridfs import NoFile
from document_storage import StorageError, UploadRejected
from gridfs_storage import GridFSPDFStorage
from storage_contract import LocalPDFStorage, StorageUncertain, DeletionOutcome

PDF = b'%PDF-1.7\nsynthetic only'

class Bucket:
    def __init__(self):
        self.files = {}
        self.failure = None
        self.deletes = 0
    def open_upload_stream_with_id(self, identifier, filename, **kwargs):
        bucket = self
        class Upload(io.BytesIO):
            def write(self, data):
                if bucket.failure == 'write':
                    raise InterruptedError('sensitive-provider-detail')
                return super().write(data)
            def close(self):
                if not self.closed:
                    bucket.files[identifier] = (self.getvalue(), kwargs['metadata'])
                    super().close()
                    if bucket.failure == 'close':
                        raise TimeoutError('sensitive-provider-detail')
        return Upload()
    def open_download_stream(self, identifier):
        if self.failure == 'read':
            raise OSError('sensitive-provider-detail')
        if identifier not in self.files:
            raise NoFile('missing')
        data, metadata = self.files[identifier]
        stream = io.BytesIO(data)
        stream.metadata, stream.length = metadata, len(data)
        return stream
    def delete(self, identifier):
        self.deletes += 1
        if self.failure == 'delete':
            raise TimeoutError('sensitive-provider-detail')
        self.files.pop(identifier, None)

class GridFSStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner, self.base, self.identifier = ObjectId(), ObjectId(), ObjectId()
        self.bucket = Bucket()
        self.storage = GridFSPDFStorage(None, owner_id=self.owner, knowledge_base_id=self.base,
                                      temp_root=self.temp.name, bucket=self.bucket)
    def upload(self, data=PDF):
        return SimpleNamespace(filename='notes.pdf', content_type='application/pdf', file=io.BytesIO(data))
    def save(self):
        return {'_id':self.identifier, 'owner_id':self.owner, 'knowledge_base_id':self.base,
                **self.storage.save(self.upload(), self.identifier, 100)}
    def test_success_and_exists(self):
        doc = self.save()
        self.assertTrue(self.storage.exists(doc))
        self.assertEqual(self.bucket.files[self.identifier][0], PDF)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_interrupted_upload_is_uncertain_no_delete(self):
        self.bucket.failure = 'write'
        with self.assertRaises(StorageUncertain) as error:
            self.save()
        self.assertNotIn('sensitive', str(error.exception))
        self.assertEqual(self.bucket.deletes, 0)
    def test_close_timeout_can_have_committed(self):
        self.bucket.failure = 'close'
        with self.assertRaises(StorageUncertain): self.save()
        self.assertIn(self.identifier, self.bucket.files)
        self.assertEqual(self.bucket.deletes, 0)
    def test_validation_before_remote_write(self):
        for data in (b'bad', PDF * 20):
            with self.assertRaises(UploadRejected):
                self.storage.save(self.upload(data), self.identifier, 100)
        self.assertEqual(self.bucket.files, {})
    def test_materialization_and_cleanup(self):
        lease = self.storage.materialize(self.save())
        self.assertEqual(lease.path.read_bytes(), PDF)
        lease.close()
        lease.close()
        self.assertFalse(lease.path.exists())
    def test_native_completion_retains_file(self):
        lease = self.storage.materialize(self.save())
        native = Future()
        native.set_running_or_notify_cancel()
        lease.retain_until(native)
        lease.close()
        self.assertFalse(native.cancel())
        self.assertTrue(lease.path.exists())
        native.set_result(None)
        self.assertFalse(lease.path.exists())
    def test_missing_and_repeated_deletion(self):
        doc = self.save()
        self.assertIs(self.storage.delete(doc), DeletionOutcome.DELETED)
        with self.assertRaises(StorageUncertain): self.storage.delete(doc)
        self.assertEqual(self.bucket.deletes, 1)
        self.assertFalse(self.storage.exists(doc))
        with self.assertRaises(StorageError): self.storage.materialize(doc)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_storage_errors_sanitized(self):
        doc = self.save()
        self.bucket.failure = 'read'
        for call in (self.storage.exists, self.storage.materialize, self.storage.delete):
            with self.assertRaises(StorageError) as error: call(doc)
            self.assertNotIn('sensitive', str(error.exception))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
    def test_delete_timeout_uncertain(self):
        doc = self.save()
        self.bucket.failure = 'delete'
        with self.assertRaises(StorageUncertain): self.storage.delete(doc)
    def test_scope_and_forged_reference_rejected(self):
        doc = self.save()
        for key in ('owner_id', 'knowledge_base_id', 'storage_file_id'):
            with self.assertRaises(StorageError):
                self.storage.materialize({**doc, key:ObjectId()})
        self.bucket.files[self.identifier] = (PDF, {})
        with self.assertRaises(StorageError): self.storage.materialize(doc)
    def test_local_facade_keeps_original(self):
        local = LocalPDFStorage(self.temp.name, owner_id=self.owner, knowledge_base_id=self.base)
        doc = {'_id':self.identifier, 'owner_id':self.owner, 'knowledge_base_id':self.base,
               **local.save(self.upload(), self.identifier, 100)}
        lease = local.materialize(doc)
        lease.close()
        self.assertTrue(local.exists(doc))
        local.delete(doc)
        self.assertFalse(local.exists(doc))

    def test_invalid_signature_cleanup(self):
        doc = self.save()
        self.bucket.files[self.identifier] = (b'not a PDF', self.storage._scope(self.identifier))
        with self.assertRaises(StorageError): self.storage.materialize(doc)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_materialization_rejects_request_task(self):
        lease = self.storage.materialize(self.save())
        try:
            with self.assertRaises(TypeError): lease.retain_until(object())
        finally:
            lease.close()

    def test_multiple_native_owners(self):
        lease = self.storage.materialize(self.save())
        first, second = Future(), Future()
        lease.retain_until(first)
        lease.retain_until(second)
        lease.close()
        first.set_result(None)
        self.assertTrue(lease.path.exists())
        second.set_result(None)
        self.assertFalse(lease.path.exists())

if __name__ == '__main__': unittest.main()
