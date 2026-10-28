"""Import-isolated synthetic spawn targets. Never parse PDFs or load models."""
import os
from pathlib import Path
import sys
from types import ModuleType

import pdf_worker_protocol as p


def synthetic_child(connection, path, settings, version, *, entered=None, release=None):
    mode = Path(path).name
    try:
        if mode.startswith('entry_'):
            # Exercise the real entry's classification and encoding with a fake
            # extraction module. No PyMuPDF import, files or actual parsing.
            from pdf_worker_child import child_entry
            module = ModuleType('pdf_processing')
            class ProcessingFailure(ValueError):
                status = 422
            def extract_chunks(path, settings):
                if any(name in sys.modules for name in ('main', 'config', 'torch', 'fastapi')):
                    raise AssertionError('forbidden import')
                if 'DEEPDOCS_TEST_SECRET' in os.environ:
                    raise AssertionError('environment not cleared')
                if mode == 'entry_failure':
                    raise ProcessingFailure('The PDF could not be read. It may be damaged.')
                if mode == 'entry_unknown':
                    raise ProcessingFailure('private diagnostic')
                if mode == 'entry_exception':
                    raise RuntimeError('private traceback')
                return (1, [dict(chunk_index=0, text='entry', page_start=1,
                                 page_end=1, character_count=5)])
            module.extract_chunks = extract_chunks
            module.ProcessingFailure = ProcessingFailure
            sys.modules['pdf_processing'] = module
            child_entry(connection, path, settings, version)
            return
        if mode == 'crash':
            os._exit(7)
        if mode == 'failure':
            connection.send_bytes(p.encode_failure(p.FailureCode.DAMAGED))
            return
        if mode == 'malformed':
            connection.send_bytes(b'not a frame')
            return
        if mode == 'oversized':
            connection.send_bytes(b'x' * (p.MAX_FRAME_BYTES + 1))
            return
        if mode == 'empty':
            return
        text = '🙂Unicode\n' * 30000 if mode in {'large', 'transfer'} else 'synthetic'
        if mode == 'imports':
            forbidden = {'main', 'database', 'config', 'fastapi', 'pymongo', 'qdrant_client',
                         'embeddings', 'sentence_transformers', 'torch', 'gemini_provider'}
            import pdf_worker_child
            if forbidden.intersection(sys.modules):
                os._exit(8)
            text = 'isolated'
        frames = p.encode_success((1, [dict(chunk_index=0, text=text, page_start=1,
                                           page_end=1, character_count=len(text))]),
                                  p.Limits(settings['max_pages'], settings['max_chunks'],
                                           settings['max_text_characters'], settings['chunk_overlap']))
        if mode == 'blocked':
            entered.set()
            if not release.wait(20):
                os._exit(9)
        last = None
        for index, frame in enumerate(frames):
            connection.send_bytes(frame)
            last = frame
            if index == 0 and mode in {'partial', 'crash_partial'}:
                if mode == 'crash_partial':
                    os._exit(7)
                return
            if index == 2 and mode == 'transfer':
                entered.set()
                if not release.wait(20):
                    os._exit(9)
        if mode == 'trailing':
            connection.send_bytes(last)
        if mode == 'nonzero':
            os._exit(7)
        if mode == 'terminal_blocked':
            entered.set()
            if not release.wait(20):
                os._exit(9)
    except BaseException:
        os._exit(10)
    finally:
        connection.close()
