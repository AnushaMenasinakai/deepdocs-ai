"""Minimal spawn target. No application/config/client/model imports.

Only a one-way Connection, absolute storage path, primitive settings and protocol
version cross the call boundary. Storage ownership/path containment must already
have been established by the future caller. Spawn is not an OS security sandbox;
Python bootstraps the parent's __main__ before this target runs. Entrypoints must
remain spawn-safe (no application startup at module import).
"""
import os
from pathlib import Path
from types import SimpleNamespace

from pdf_worker_protocol import VERSION, Limits, FailureCode, encode_failure, encode_success


def validate_job(path, values, version=VERSION):
    """Validate/copy primitive inputs without opening files or loading dotenv."""
    if (type(path) is not str or '\x00' in path or not Path(path).is_absolute()
            or type(version) is not int or version != VERSION):
        raise ValueError('Invalid PDF worker input.')
    ranges = {
        'chunk_target': (100, 5000), 'chunk_overlap': (0, 2499),
        'max_pages': (1, 5000), 'max_text_characters': (100, 10000000),
        'max_chunks': (1, 50000), 'max_source_bytes': (1, 100 * 1024 * 1024),
    }
    if type(values) is not dict or set(values) != set(ranges):
        raise ValueError('Invalid PDF worker input.')
    for key, (low, high) in ranges.items():
        if type(values[key]) is not int or not low <= values[key] <= high:
            raise ValueError('Invalid PDF worker input.')
    if values['chunk_overlap'] >= values['chunk_target'] // 2:
        raise ValueError('Invalid PDF worker input.')
    return dict(values)


# Exact existing completed extraction failures only. Unknown messages/statuses
# are NOT downgraded to parsing failures. No exception text crosses the pipe.
_FAILURES = {
    (503, 'Stored PDF is temporarily unavailable.'): FailureCode.STORAGE_UNAVAILABLE,
    (422, 'The PDF exceeds the configured processing file limit.'): FailureCode.SOURCE_LIMIT,
    (422, 'The PDF is unreadable or password-protected.'): FailureCode.UNREADABLE,
    (422, 'The PDF exceeds the processing page limit.'): FailureCode.PAGE_LIMIT,
    (422, 'The PDF exceeds the extracted text limit.'): FailureCode.TEXT_LIMIT,
    (422, 'The PDF could not be read. It may be damaged.'): FailureCode.DAMAGED,
    (422, 'The PDF exceeds the processing chunk limit.'): FailureCode.CHUNK_LIMIT,
    (422, 'No extractable text was found. OCR is not supported.'): FailureCode.NO_TEXT,
}


def child_entry(connection, path, values, version):
    try:
        # No environment-based service configuration is needed by extraction.
        # This clears inherited application secrets before PDF module imports;
        # it cannot isolate the preceding multiprocessing bootstrap itself.
        keep = {key: value for key, value in os.environ.items()
                if key.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        os.environ.clear()
        os.environ.update(keep)
        settings = SimpleNamespace(**validate_job(path, values, version))
        from pdf_processing import ProcessingFailure, extract_chunks
        try:
            result = extract_chunks(Path(path), settings)
        except ProcessingFailure as error:
            code = _FAILURES.get((error.status, str(error)))
            if code is None:
                raise RuntimeError('Unrecognized PDF failure.') from None
            connection.send_bytes(encode_failure(code))
        else:
            for frame in encode_success(result, Limits.from_settings(settings)):
                connection.send_bytes(frame)
        connection.close()
    except BaseException:
        # No arbitrary traceback, exception object or exception string leaves
        # the child. Nonzero exit is an infrastructure failure for supervision.
        try:
            connection.close()
        finally:
            os._exit(1)
