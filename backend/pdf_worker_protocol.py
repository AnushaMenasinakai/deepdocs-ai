"""PDF protocol v1; no parsing, processes, pickle, or application imports.

Transport must use send_bytes(frame) and recv_bytes(MAX_FRAME_BYTES), NEVER
recv(). recv_bytes' maxlength is essential: feed() cannot prevent an upstream
transport from allocating an oversized message. EOF must call finish(). A
terminal envelope is not evidence that a child exited; supervision is separate.

Bound proof for the existing chunker: normalization never expands text;
successive source intervals overlap by at most O characters. Thus total chunk
characters S <= T + (C-1)*O. Stripping only reduces this. Long tokens need no
target-size bound. UTF-8 takes at most 4*S bytes. With payload size P, a sender
uses at most C + floor(4*S/P) text frames (ceil per nonempty chunk). Add fixed
start, chunk metadata and terminal frames. This is a transport bound, NOT a
native-memory bound. Receiver state also bounds counts and text before joining.

Empty results are structurally supported; the extractor still classifies no
extractable text as a parsing failure. No normalization is performed here.
"""
from dataclasses import dataclass
from enum import IntEnum
import struct

VERSION = 1
MAX_FRAME_BYTES = 65536
HEADER = struct.Struct('!4sBBQI')  # magic, version, type, sequence, payload length
PAYLOAD_BYTES = MAX_FRAME_BYTES - HEADER.size
PAGE = struct.Struct('!I')
CHUNK = struct.Struct('!IIIQ')
SUMMARY = struct.Struct('!IQQ')  # chunks, characters, UTF-8 bytes


class Record(IntEnum):
    START = 1
    CHUNK = 2
    TEXT = 3
    END_CHUNK = 4
    SUCCESS = 5
    FAILURE = 6


class FailureCode(IntEnum):
    STORAGE_UNAVAILABLE = 1
    SOURCE_LIMIT = 2
    UNREADABLE = 3
    PAGE_LIMIT = 4
    TEXT_LIMIT = 5
    DAMAGED = 6
    CHUNK_LIMIT = 7
    NO_TEXT = 8


@dataclass(frozen=True)
class ParsingFailure:
    """Recognized completed local failure; no arbitrary message or exception."""
    code: FailureCode


class ProtocolError(RuntimeError):
    """Infrastructure uncertainty, NEVER an ordinary completed parsing failure."""
    def __init__(self):
        super().__init__('Invalid or incomplete PDF worker transfer.')


@dataclass(frozen=True)
class Limits:
    max_pages: int
    max_chunks: int
    max_text_characters: int
    chunk_overlap: int

    def __post_init__(self):
        for value, low, high in ((self.max_pages, 1, 5000),
                                 (self.max_chunks, 1, 50000),
                                 (self.max_text_characters, 100, 10000000),
                                 (self.chunk_overlap, 0, 2499)):
            if type(value) is not int or not low <= value <= high:
                raise ProtocolError()

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.max_pages, settings.max_chunks,
                   settings.max_text_characters, settings.chunk_overlap)

    @property
    def characters(self):
        return self.max_text_characters + (self.max_chunks - 1) * self.chunk_overlap

    @property
    def wire_bytes(self):
        text = 4 * self.characters
        frames = self.max_chunks + text // PAYLOAD_BYTES
        return (2 * HEADER.size + PAGE.size + SUMMARY.size
                + self.max_chunks * (2 * HEADER.size + CHUNK.size)
                + text + frames * HEADER.size)


def _frame(kind, sequence, payload=b''):
    if len(payload) > PAYLOAD_BYTES:
        raise ProtocolError()
    return HEADER.pack(b'PDFW', VERSION, kind, sequence, len(payload)) + payload


class Decoder:
    """One transfer, fail closed permanently after any protocol error."""
    def __init__(self, limits):
        self.limits = limits
        self.sequence = self.received = self.characters = self.text_bytes = 0
        self.pages = None
        self.chunks = []
        self.pending = None
        self.parts = []
        self.pending_bytes = 0
        self.terminal = self.failed = False
        self.failure = None

    def feed(self, frame):
        try:
            self._feed(frame)
        except (ValueError, TypeError, struct.error, UnicodeError, ProtocolError):
            self.failed = True
            raise ProtocolError() from None

    def _feed(self, frame):
        if self.failed or self.terminal or type(frame) is not bytes:
            raise ProtocolError()
        if not HEADER.size <= len(frame) <= MAX_FRAME_BYTES:
            raise ProtocolError()
        magic, version, kind, sequence, size = HEADER.unpack_from(frame)
        if (magic != b'PDFW' or version != VERSION or sequence != self.sequence
                or size > PAYLOAD_BYTES or size != len(frame) - HEADER.size):
            raise ProtocolError()
        self.received += len(frame)
        if self.received > self.limits.wire_bytes:
            raise ProtocolError()
        self.sequence += 1
        payload = frame[HEADER.size:]  # only after declared/actual bounds checked
        kind = Record(kind)
        if kind == Record.FAILURE:
            if sequence != 0 or len(payload) != 1:
                raise ProtocolError()
            self.failure = ParsingFailure(FailureCode(payload[0]))
            self.terminal = True
        elif kind == Record.START:
            if sequence != 0:
                raise ProtocolError()
            self.pages, = PAGE.unpack(payload)
            if self.pages > self.limits.max_pages:
                raise ProtocolError()
        elif self.pages is None:
            raise ProtocolError()
        elif kind == Record.CHUNK:
            index, start, end, count = CHUNK.unpack(payload)
            if (self.pending is not None or index != len(self.chunks)
                    or index >= self.limits.max_chunks or count == 0
                    or count > self.limits.max_text_characters
                    or self.characters + count > self.limits.characters
                    or not 1 <= start == end <= self.pages
                    or (self.chunks and start < self.chunks[-1]['page_end'])):
                raise ProtocolError()
            self.pending = (index, start, end, count)
        elif kind == Record.TEXT:
            if self.pending is None or not payload:
                raise ProtocolError()
            self.pending_bytes += len(payload)
            if self.pending_bytes > 4 * self.pending[3]:
                raise ProtocolError()
            self.parts.append(payload)
        elif kind == Record.END_CHUNK:
            if payload or self.pending is None:
                raise ProtocolError()
            index, start, end, count = self.pending
            text = b''.join(self.parts).decode('utf-8', errors='strict')
            if len(text) != count:
                raise ProtocolError()
            self.chunks.append(dict(chunk_index=index, text=text, page_start=start,
                                    page_end=end, character_count=count))
            self.characters += count
            self.text_bytes += self.pending_bytes
            self.parts = []
            self.pending_bytes = 0
            self.pending = None
        elif kind == Record.SUCCESS:
            if (self.pending is not None or SUMMARY.unpack(payload) !=
                    (len(self.chunks), self.characters, self.text_bytes)):
                raise ProtocolError()
            self.terminal = True
        else:
            raise ProtocolError()

    def finish(self):
        if self.failed or not self.terminal:
            self.failed = True
            raise ProtocolError()
        return self.failure if self.failure is not None else (self.pages, self.chunks)


def encode_success(result, limits):
    """Yield bounded frames; validate producer metadata using the same grammar.

    Caller must treat an interrupted encoding as infrastructure failure. No
    failure terminal is appended to an already partially emitted success.
    """
    decoder = Decoder(limits)
    sequence = 0

    def emit(kind, payload=b''):
        nonlocal sequence
        frame = _frame(kind, sequence, payload)
        decoder.feed(frame)
        sequence += 1
        return frame

    try:
        pages, chunks = result
        if type(pages) is not int or type(chunks) is not list:
            raise ProtocolError()
        yield emit(Record.START, PAGE.pack(pages))
        for chunk in chunks:
            if type(chunk) is not dict or set(chunk) != {
                    'chunk_index', 'text', 'page_start', 'page_end', 'character_count'}:
                raise ProtocolError()
            values = [chunk[key] for key in ('chunk_index', 'page_start', 'page_end', 'character_count')]
            if any(type(value) is not int for value in values) or type(chunk['text']) is not str:
                raise ProtocolError()
            yield emit(Record.CHUNK, CHUNK.pack(*values))
            if len(chunk['text']) != values[-1]:
                raise ProtocolError()
            data = chunk['text'].encode('utf-8', errors='strict')
            for offset in range(0, len(data), PAYLOAD_BYTES):
                yield emit(Record.TEXT, data[offset:offset + PAYLOAD_BYTES])
            yield emit(Record.END_CHUNK)
        yield emit(Record.SUCCESS, SUMMARY.pack(len(chunks), decoder.characters, decoder.text_bytes))
    except (ValueError, TypeError, KeyError, OverflowError, struct.error, UnicodeError):
        raise ProtocolError() from None


def encode_failure(code):
    if type(code) is not FailureCode:
        raise ProtocolError()
    return _frame(Record.FAILURE, 0, bytes([code]))
