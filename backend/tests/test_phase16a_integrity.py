"""Phase 16A historical replay across isolated checkout representations."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import dataset, historical_integrity as integrity, multi_query, phase13, phase16a


class Phase16AIntegrityTests(unittest.TestCase):
    def setUp(self):
        original = phase16a.ROOT
        self.saved = json.loads((original/'reports/phase16a-reranking.json').read_text(encoding='utf-8'))
        self.markdown = (original/'reports/phase16a-reranking.md').read_text(encoding='utf-8')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'evaluation'
        names = ['corpus.json', 'cases.json', 'fixture_rankings.json', 'phase15a_safety.json',
                 'query_decomposition.py', 'phase15a_rankings.json']
        names += ['reports/'+n for n in phase16a.HISTORICAL]
        self.raw = {n: (original/n).read_bytes() for n in names}
        self.prepare('lf')
        for name in phase16a.PRODUCTION:
            (self.root.parent/name).write_bytes((original.parent/name).read_bytes())
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (phase16a, multi_query, phase13, dataset):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'embeddings.load_model', 'rag.get_gemini_provider'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('Unexpected external work')))

    def prepare(self, ending):
        for name, raw in self.raw.items():
            raw = raw.replace(b'\r\n', b'\n')
            if ending == 'crlf':
                raw = raw.replace(b'\n', b'\r\n')
            elif ending == 'mixed':
                lines = raw.split(b'\n')
                raw = b''.join(line+(b'\r\n' if i % 2 else b'\n') for i,line in enumerate(lines[:-1]))+lines[-1]
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def report(self):
        return phase16a.build_report(self.saved['environment'])

    def assert_behavior(self, report):
        self.assertEqual({k:v for k,v in report.items() if k != 'production_sha256'},
                         {k:v for k,v in self.saved.items() if k != 'production_sha256'})
        self.assertEqual(phase16a.markdown(report), self.markdown)

    def test_newlines_preserve_determinism_complete_behavior_and_fingerprints(self):
        for ending in ('lf', 'crlf', 'mixed'):
            with self.subTest(ending=ending):
                self.prepare(ending)
                report = self.report()
                self.assertEqual(report, self.report())
                self.assert_behavior(report)
                self.assertEqual(phase16a.snapshot_hashes(), self.saved['historical_report_sha256'])
                self.assertEqual(report['production_sha256'], {
                    n:hashlib.sha256((self.root.parent/n).read_bytes()).hexdigest() for n in phase16a.PRODUCTION})

    def test_each_historical_report_missing_or_tampered_rejected(self):
        for name in phase16a.HISTORICAL:
            for missing in (False, True):
                self.prepare('lf')
                path = self.root/'reports'/name
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(path.read_bytes()+b' ')
                with self.subTest(name=name, missing=missing), self.assertRaises(integrity.HistoricalIntegrityError):
                    self.report()

    def test_cached_rankings_missing_or_changed_rejected(self):
        path = self.root/'phase15a_rankings.json'
        original = path.read_bytes()
        for replacement in (None, b'{}', original+b' '):
            if replacement is None:
                path.unlink()
            else:
                path.write_bytes(replacement)
            with self.subTest(replacement=replacement is None), self.assertRaises(integrity.HistoricalIntegrityError):
                self.report()

    def test_existing_validated_loader_reused_and_unrelated_data_rejected(self):
        with patch.object(phase16a, 'load_historical_rankings', wraps=multi_query.load_historical_rankings) as loader:
            self.assert_behavior(self.report())
            loader.assert_called_once_with()
        changed = copy.deepcopy(multi_query.load_historical_rankings())
        next(iter(changed['queries'].values()))['hits'][0]['score'] -= .001
        with patch.object(phase16a, 'load_historical_rankings', return_value=changed):
            with self.assertRaises(integrity.HistoricalIntegrityError):
                self.report()

    def test_current_source_bytes_are_not_historical_or_normalized(self):
        path = self.root.parent/'config.py'
        lf = path.read_bytes().replace(b'\r\n', b'\n')
        hashes = []
        for content in (lf, lf.replace(b'\n', b'\r\n'), lf+b'\n# Current configuration change\n'):
            path.write_bytes(content)
            report = self.report()
            self.assert_behavior(report)
            self.assertEqual(report['production_sha256']['config.py'], hashlib.sha256(content).hexdigest())
            hashes.append(report['production_sha256']['config.py'])
        self.assertEqual(len(set(hashes)), 3)


if __name__ == '__main__':
    unittest.main()
