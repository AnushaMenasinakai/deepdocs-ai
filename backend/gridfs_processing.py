"""Scoped read-only originals, owned materialization and the existing PDF lane."""
import re
import tempfile
from gridfs_storage import GridFSPDFStorage
from gridfs_uploads import checksum
from parser_leases import ParserLeases
from storage_contract import MaterializedPDF, StorageUncertain, validate_scope
from document_storage import StorageError


class GridFSProcessing:
    def __init__(self, workers, database, cpu_workers, *, storage_factory=GridFSPDFStorage):
        self.db = database
        self.storage_factory = storage_factory
        self.leases = ParserLeases(workers, cpu_workers)

    async def parse(self, document, owner, identifier, settings, session):
        base = document.get('knowledge_base_id')
        if validate_scope(document, owner, base) != identifier:
            raise StorageError('Invalid authorized storage identity.')
        if (document.get('storage_backend') != 'gridfs'
                or document.get('storage_file_id') != identifier
                or type(document.get('file_size')) is not int
                or not 0 < document['file_size'] <= 5 * 1024**2
                or not isinstance(document.get('storage_checksum'), str)
                or not re.fullmatch('[0-9a-f]{64}', document['storage_checksum'])):
            raise StorageError('Invalid GridFS original identity.')
        # Freeze the authorized storage fields before handing them to a worker.
        original = dict(document)

        def materialize():
            root = tempfile.TemporaryDirectory(prefix='deepdocs-parser-')
            lease = None
            try:
                storage = self.storage_factory(self.db, owner_id=owner,
                    knowledge_base_id=base, temp_root=root.name)
                storage.reference(original)
                lease = storage.materialize(original)
                if (lease.path.stat().st_size != original['file_size']
                        or checksum(lease.path) != original['storage_checksum']):
                    raise StorageError('Stored PDF integrity check failed.')
                def cleanup():
                    lease.close()
                    if not lease.cleanup_complete:
                        raise StorageError('Temporary PDF cleanup is incomplete.')
                    root.cleanup()
                return MaterializedPDF(lease.path, cleanup)
            except BaseException:
                try:
                    if lease is not None:
                        lease.close()
                        if not lease.cleanup_complete:
                            raise StorageError('Temporary PDF cleanup is incomplete.')
                    root.cleanup()
                except BaseException:
                    raise StorageUncertain('Materialization cleanup requires review.') from None
                raise

        return await self.leases.parse(materialize, settings, session=session)
