"""Local PDF storage. No parsing, extraction, or public file serving."""
from pathlib import Path
import re

STORAGE_ROOT = Path(__file__).resolve().parent / "storage" / "documents"
CHUNK_SIZE = 64 * 1024


class UploadRejected(ValueError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class StorageError(OSError):
    pass


class StorageCleanupError(StorageError):
    """A partial file needs reconciliation; retain its KB reservation."""


class DocumentStorage:
    def __init__(self, root=STORAGE_ROOT):
        self.root = Path(root).absolute()

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}\.pdf", key):
            raise StorageError("Invalid storage reference.")
        # Reject redirected roots/ancestors and links, including dangling links.
        if self.root.resolve() != self.root or self.root.is_symlink():
            raise StorageError("Invalid storage directory.")
        target = self.root / key
        if target.is_symlink() or target.resolve().parent != self.root:
            raise StorageError("Invalid storage reference.")
        return target

    def save(self, upload, document_id, max_bytes):
        filename = (upload.filename or "").replace(chr(92), "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise UploadRejected(422, "Provide a valid PDF filename.")
        if not filename.lower().endswith(".pdf"):
            raise UploadRejected(415, "Only PDF files are accepted.")
        if (upload.content_type or "").lower() != "application/pdf":
            raise UploadRejected(415, "Use the application/pdf content type.")
        source = upload.file
        source.seek(0)
        signature = source.read(5)
        if not signature:
            raise UploadRejected(422, "The uploaded file is empty.")
        if signature != b"%PDF-":
            raise UploadRejected(415, "The file does not have a PDF signature.")
        source.seek(0)
        key = str(document_id) + ".pdf"
        target = self.path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(key)
        created = False
        size = 0
        try:
            # Exclusive creation prevents collisions from overwriting a file.
            with target.open("xb") as output:
                created = True
                while chunk := source.read(min(CHUNK_SIZE, max_bytes + 1)):
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadRejected(413, "PDF exceeds the configured upload limit.")
                    output.write(chunk)
            return {"original_filename": filename, "stored_filename": key,
                    "storage_path": key, "file_size": size, "content_type": "application/pdf"}
        except BaseException:
            if created:
                try:
                    self.path(key).unlink(missing_ok=True)
                except OSError:
                    raise StorageCleanupError("Upload cleanup is unavailable.") from None
            raise

    def delete(self, document):
        expected = str(document["_id"]) + ".pdf"
        # Do not trust even persisted paths, or allow deleting a different PDF.
        if document.get("stored_filename") != expected or document.get("storage_path") != expected:
            raise StorageError("Invalid storage reference.")
        self.path(expected).unlink(missing_ok=True)


def get_document_storage():
    return DocumentStorage()
