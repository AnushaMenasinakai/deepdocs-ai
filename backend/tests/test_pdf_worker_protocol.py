"""Synthetic protocol records only; no PDFs, processes, models or services."""
import ast
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from config import ProcessingSettings
import pdf_worker_protocol as p


def chunk(text, index=0, page=1):
    return dict(chunk_index=index, text=text, page_start=page,
                page_end=page, character_count=len(text))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.limits = p.Limits.from_settings(ProcessingSettings())

    def decode(self, frames, limits=None):
        decoder = p.Decoder(limits or self.limits)
        for frame in frames:
            decoder.feed(frame)
        return decoder.finish()

    def test_exact_round_trips(self):
        for result in [(0, []), (3, []), (1, [chunk('hello')]),
                       (3, [chunk('a'), chunk(' b\n\t', 1, 2), chunk('c', 2, 3)]),
                       (1, [chunk('नमस्ते🙂e\u0301\x00\r\n')]),
                       (1, [chunk('🙂' * 100000)])]:
            with self.subTest(result_size=sum(len(c['text']) for c in result[1])):
                frames = list(p.encode_success(result, self.limits))
                self.assertTrue(all(len(f) <= 65536 for f in frames))
                self.assertEqual(self.decode(frames), result)

    def test_fragment_splits_utf8_and_reconstructs_exactly(self):
        text = '🙂' * 40000
        frames = list(p.encode_success((1, [chunk(text)]), self.limits))
        payload = frames[2][p.HEADER.size:]
        self.assertEqual(len(frames[2]), 65536)
        with self.assertRaises(UnicodeDecodeError):
            payload.decode('utf-8')
        self.assertEqual(self.decode(frames), (1, [chunk(text)]))

    def test_all_recognized_failures(self):
        for code in p.FailureCode:
            with self.subTest(code=code):
                self.assertEqual(self.decode([p.encode_failure(code)]), p.ParsingFailure(code))
        for value in [1, 'secret', ValueError('secret')]:
            with self.subTest(value_type=type(value)):
                with self.assertRaises(p.ProtocolError):
                    p.encode_failure(value)

    def test_frame_header_rejections(self):
        valid = p._frame(p.Record.START, 0, p.PAGE.pack(1))
        for frame in [b'', valid[:p.HEADER.size-1], valid[:-1], valid + b'x',
                      p.HEADER.pack(b'BAD!', 1, 1, 0, 4) + p.PAGE.pack(1),
                      p.HEADER.pack(b'PDFW', 2, 1, 0, 4) + p.PAGE.pack(1),
                      p.HEADER.pack(b'PDFW', 1, 99, 0, 0),
                      p.HEADER.pack(b'PDFW', 1, 1, 0, 0xffffffff),
                      p._frame(p.Record.START, 1, p.PAGE.pack(1)),
                      b'x' * 65537]:
            with self.subTest(size=len(frame)):
                with self.assertRaises(p.ProtocolError):
                    self.decode([frame])

    def test_terminal_and_sequence_rules(self):
        frames = list(p.encode_success((1, [chunk('abc')]), self.limits))
        for altered in [frames[:-1], frames + [frames[-1]], frames + [frames[0]],
                        [frames[0], frames[2], frames[1]], frames[1:],
                        [frames[0], p._frame(p.Record.START, 1, p.PAGE.pack(1))],
                        [frames[0], p._frame(p.Record.FAILURE, 1, b'\x01')]]:
            with self.subTest(length=len(altered)):
                with self.assertRaises(p.ProtocolError):
                    self.decode(altered)

    def test_metadata_rejections(self):
        valid = chunk('abc')
        for changes in [dict(chunk_index=1), dict(chunk_index=-1), dict(page_start=0),
                        dict(page_end=2), dict(page_start=2, page_end=2),
                        dict(character_count=2), dict(character_count=0),
                        dict(character_count=True), dict(text=123), dict(extra='x')]:
            with self.subTest(changes=changes):
                with self.assertRaises(p.ProtocolError):
                    list(p.encode_success((1, [{**valid, **changes}]), self.limits))
        for key in valid:
            with self.subTest(missing=key):
                altered = dict(valid)
                del altered[key]
                with self.assertRaises(p.ProtocolError):
                    list(p.encode_success((1, [altered]), self.limits))
        for pages in [-1, True, self.limits.max_pages + 1]:
            with self.subTest(pages=pages):
                with self.assertRaises(p.ProtocolError):
                    list(p.encode_success((pages, []), self.limits))

    def test_payload_and_summary_rejections(self):
        frames = list(p.encode_success((1, [chunk('abc')]), self.limits))
        mutations = [(1, p.Record.CHUNK, b'bad'),
                     (2, p.Record.TEXT, b'\xff\xff\xff'),
                     (2, p.Record.TEXT, b'ab'),
                     (2, p.Record.TEXT, b''),
                     (2, p.Record.TEXT, b'x' * 13),
                     (3, p.Record.END_CHUNK, b'extra'),
                     (4, p.Record.SUCCESS, p.SUMMARY.pack(2, 3, 3)),
                     (4, p.Record.SUCCESS, p.SUMMARY.pack(1, 4, 3)),
                     (4, p.Record.SUCCESS, p.SUMMARY.pack(1, 3, 4))]
        for index, kind, payload in mutations:
            with self.subTest(index=index, payload_size=len(payload)):
                altered = list(frames)
                altered[index] = p._frame(kind, index, payload)
                with self.assertRaises(p.ProtocolError):
                    self.decode(altered)

    def test_fail_closed_after_error(self):
        decoder = p.Decoder(self.limits)
        with self.assertRaises(p.ProtocolError):
            decoder.feed(b'bad')
        with self.assertRaises(p.ProtocolError):
            decoder.feed(p.encode_failure(p.FailureCode.DAMAGED))
        with self.assertRaises(p.ProtocolError):
            decoder.finish()

    def test_missing_terminal_poisons_decoder(self):
        decoder = p.Decoder(self.limits)
        with self.assertRaises(p.ProtocolError):
            decoder.finish()
        with self.assertRaises(p.ProtocolError):
            decoder.feed(p.encode_failure(p.FailureCode.NO_TEXT))

    def test_aggregate_wire_bound(self):
        limits = p.Limits(1, 1, 100, 0)
        decoder = p.Decoder(limits)
        decoder.feed(p._frame(p.Record.START, 0, p.PAGE.pack(1)))
        decoder.feed(p._frame(p.Record.CHUNK, 1, p.CHUNK.pack(0, 1, 1, 100)))
        # Excessively tiny frames exceed transport overhead allowance while
        # remaining below text/count bounds. A canonical sender never does this.
        with self.assertRaises(p.ProtocolError):
            for seq in range(2, 102):
                decoder.feed(p._frame(p.Record.TEXT, seq, b'a'))
        self.assertLess(decoder.pending_bytes, 100)

    def test_maximum_character_bound_and_overlap(self):
        limits = p.Limits(1, 2, 100, 10)
        result = (1, [chunk('🙂' * 60), chunk('🙂' * 50, 1)])
        self.assertEqual(limits.characters, 110)
        frames = list(p.encode_success(result, limits))
        self.assertLessEqual(sum(map(len, frames)), limits.wire_bytes)
        self.assertEqual(self.decode(frames, limits), result)
        with self.assertRaises(p.ProtocolError):
            list(p.encode_success((1, [chunk('a' * 60), chunk('b' * 51, 1)]), limits))
        with self.assertRaises(p.ProtocolError):
            list(p.encode_success((1, [chunk('a' * 101)]), limits))
        with self.assertRaises(p.ProtocolError):
            list(p.encode_success((1, [chunk('a'), chunk('b', 1), chunk('c', 2)]), limits))

    def test_configured_maximum_long_token(self):
        # Maximum configured extracted characters in one legal whole token.
        limits = p.Limits(5000, 50000, 10000000, 2499)
        text = 'x' * limits.max_text_characters
        result = (5000, [chunk(text, page=5000)])
        self.assertEqual(self.decode(p.encode_success(result, limits), limits), result)

    def test_maximum_chunk_count(self):
        limits = p.Limits(5000, 50000, 10000000, 2499)
        result = (1, [chunk('x', index) for index in range(limits.max_chunks)])
        self.assertEqual(self.decode(p.encode_success(result, limits), limits), result)

    def test_bound_against_actual_text_chunker(self):
        # Text-only helpers: no PDF opening or parsing. Guard the proof's
        # assumptions against future normalization/chunking changes.
        from pdf_processing import normalize_text, chunk_page
        for raw in ['🙂' * 70000, 'word ' * 3000,
                    ('First sentence.\n\nSecond paragraph!\t' * 500),
                    ' \r\n\t' * 100, ('a' * 300 + ' ') * 100]:
            for target, overlap in [(100, 0), (100, 49), (1000, 150), (5000, 2499)]:
                with self.subTest(length=len(raw), target=target, overlap=overlap):
                    clean = normalize_text(raw)
                    values = chunk_page(clean, target, overlap)
                    limits = p.Limits(1, max(1, len(values)), max(100, len(raw)), overlap)
                    self.assertLessEqual(len(clean), len(raw))
                    self.assertLessEqual(sum(map(len, values)), limits.characters)
                    result = (1, [chunk(value, i) for i, value in enumerate(values)])
                    frames = list(p.encode_success(result, limits))
                    self.assertLessEqual(sum(map(len, frames)), limits.wire_bytes)
                    self.assertEqual(self.decode(frames, limits), result)

    def test_no_pickle_or_application_imports(self):
        source = Path(p.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                       for alias in node.names)
        self.assertEqual(imports, {'dataclasses', 'enum', 'struct'})
        with patch('pickle.loads', side_effect=AssertionError('unpickling forbidden')):
            with self.assertRaises(p.ProtocolError):
                self.decode([b'\x80\x04secret'])
            self.assertEqual(self.decode(p.encode_success((0, []), self.limits)), (0, []))

    def test_errors_are_sanitized(self):
        for frames in [[b'secret-token'], [p._frame(p.Record.FAILURE, 0, b'\xff')]]:
            with self.assertRaises(p.ProtocolError) as caught:
                self.decode(frames)
            self.assertEqual(str(caught.exception), 'Invalid or incomplete PDF worker transfer.')
            self.assertIsNone(caught.exception.__cause__)

    def test_invalid_limits(self):
        for field, value in [('max_pages', 0), ('max_chunks', True),
                             ('max_text_characters', 99), ('chunk_overlap', -1)]:
            with self.subTest(field=field):
                with self.assertRaises(p.ProtocolError):
                    replace(self.limits, **{field: value})

    def test_adversarial_utf8_and_incomplete_states(self):
        for data in [b'\xf0\x9f\x99', b'\xc0\xaf', b'\xed\xa0\x80']:
            with self.subTest(data=data):
                frames = [p._frame(p.Record.START, 0, p.PAGE.pack(1)),
                          p._frame(p.Record.CHUNK, 1, p.CHUNK.pack(0, 1, 1, 1)),
                          p._frame(p.Record.TEXT, 2, data),
                          p._frame(p.Record.END_CHUNK, 3)]
                with self.assertRaises(p.ProtocolError):
                    self.decode(frames)
        start = p._frame(p.Record.START, 0, p.PAGE.pack(2))
        metadata = p._frame(p.Record.CHUNK, 1, p.CHUNK.pack(0, 2, 2, 1))
        for frames in [
            [start, p._frame(p.Record.TEXT, 1, b'x')],
            [start, p._frame(p.Record.END_CHUNK, 1)],
            [start, metadata, p._frame(p.Record.CHUNK, 2, p.CHUNK.pack(1, 2, 2, 1))],
            [start, metadata, p._frame(p.Record.SUCCESS, 2, p.SUMMARY.pack(0, 0, 0))],
            [start, p._frame(p.Record.CHUNK, 2**64 - 1, p.CHUNK.pack(0, 1, 1, 1))],
            [start, metadata, metadata],
            [start, p._frame(p.Record.SUCCESS, 1, b'')],
            [p._frame(p.Record.START, 0, b'')],
        ]:
            with self.subTest(count=len(frames)):
                with self.assertRaises(p.ProtocolError):
                    self.decode(frames)
        with self.assertRaises(p.ProtocolError):
            list(p.encode_success((2, [chunk('a', page=2), chunk('b', 1, 1)]), self.limits))
