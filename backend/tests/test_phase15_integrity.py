"""Isolated Phase 15A replay and fresh-measurement compatibility checks."""
import asyncio
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import dataset, historical_integrity as integrity, multi_query as multi, phase13


class Phase15IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.saved = json.loads((multi.ROOT/'reports/phase15a-multi-query.json').read_text(encoding='utf-8'))
        self.data = multi.load_historical_rankings()
        self.baseline = multi.replay(self.data)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'evaluation'
        self.names = ['corpus.json', 'cases.json', 'fixture_rankings.json',
                      'phase15a_safety.json', 'query_decomposition.py', 'phase15a_rankings.json']
        self.names += ['reports/'+n for n in multi.HISTORICAL]
        self.raw = {n: (multi.ROOT/n).read_bytes() for n in self.names}
        self.prepare('lf')
        # Windows creates a local socket pair when initializing an event loop.
        # Initialize before blocking connections; measurement itself stays offline.
        if self._testMethodName == 'test_fresh_measurement_fingerprints_current_bytes':
            self.runner = asyncio.Runner()
            self.runner.get_loop()
            self.addCleanup(self.runner.close)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (multi, dataset, phase13):
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

    def test_newlines_preserve_full_report_rankings_and_determinism(self):
        for ending in ('lf', 'crlf', 'mixed'):
            with self.subTest(ending=ending):
                self.prepare(ending)
                loaded = multi.load_historical_rankings()
                self.assertEqual(loaded, self.data)
                self.assertEqual(multi.replay(loaded), self.baseline)
                first = multi.compare(loaded)
                self.assertEqual(first, multi.compare(loaded))
                self.assertEqual(first, self.saved)
                self.assertEqual(multi.historical_hashes(), self.data['historical_hashes'])
                self.assertEqual(multi.fingerprints(), self.data['fingerprints'])

    def test_every_historical_input_and_report_tampering_rejected(self):
        for name in self.names:
            self.prepare('lf')
            with (self.root/name).open('ab') as stream:
                stream.write(b' ')
            with self.subTest(name=name), self.assertRaises(integrity.HistoricalIntegrityError):
                multi.compare(self.data)

    def test_missing_evidence_rejected(self):
        for name in self.names:
            self.prepare('lf')
            (self.root/name).unlink()
            with self.subTest(name=name), self.assertRaises(integrity.HistoricalIntegrityError):
                multi.compare(self.data)

    def test_wrong_fingerprints_and_changed_cached_scores_rejected(self):
        for field in ('fingerprints', 'historical_hashes'):
            changed = copy.deepcopy(self.data)
            changed[field] = {}
            with self.subTest(field=field), self.assertRaises(ValueError):
                multi.replay(changed)
        changed = copy.deepcopy(self.data)
        next(iter(changed['queries'].values()))['hits'][0]['score'] -= .001
        with self.assertRaises(integrity.HistoricalIntegrityError):
            multi.replay(changed)
        (self.root/'phase15a_rankings.json').write_text(json.dumps(changed), encoding='utf-8')
        with self.assertRaises(integrity.HistoricalIntegrityError):
            multi.load_historical_rankings()

    def test_rankings_changed_after_validation_are_not_parsed(self):
        real = multi.validate_historical_content
        def validate(name, **kwargs):
            digest = real(name, **kwargs)
            (self.root/name).write_bytes(b'{}')
            return digest
        with patch.object(multi, 'validate_historical_content', side_effect=validate):
            with self.assertRaisesRegex(integrity.HistoricalIntegrityError, 'during loading'):
                multi.load_historical_rankings()

    def test_fresh_replay_uses_current_inputs_not_cached_evidence(self):
        with (self.root/'phase15a_safety.json').open('ab') as stream:
            stream.write(b' ')
        (self.root/'phase15a_rankings.json').unlink()
        fresh = copy.deepcopy(self.data)
        fresh['fingerprints'] = multi.fingerprints(historical=False)
        self.assertNotEqual(fresh['fingerprints'], self.data['fingerprints'])
        self.assertEqual(multi.replay(fresh, historical=False), self.baseline)
        report = multi.compare(fresh, historical=False)
        self.assertEqual(report['fingerprints'], fresh['fingerprints'])
        self.assertEqual({k:v for k,v in report.items() if k != 'fingerprints'},
                         {k:v for k,v in self.saved.items() if k != 'fingerprints'})
        fresh['fingerprints'] = self.data['fingerprints']
        with self.assertRaisesRegex(ValueError, 'fingerprint mismatch'):
            multi.replay(fresh, historical=False)

    def test_fresh_measurement_fingerprints_current_bytes(self):
        for name in ('phase15a_safety.json', 'query_decomposition.py'):
            with (self.root/name).open('ab') as stream:
                stream.write(b' ')
        revision = self.root.parent/'.cache/embeddings/models--sentence-transformers--all-MiniLM-L6-v2/refs/main'
        revision.parent.mkdir(parents=True)
        revision.write_text('a'*40)
        async def retrieve(corpus, queries, fixtures, mode):
            self.assertEqual(mode, 'local-model')
            return {q['id']: [] for q in queries}, 384
        with patch('evaluation.adapters.retrieve_dataset', new=AsyncMock(side_effect=retrieve)):
            fresh = self.runner.run(multi.measure())
        expected = {n: hashlib.sha256((self.root/n).read_bytes()).hexdigest()
                    for n in self.data['fingerprints']}
        self.assertEqual(fresh['fingerprints'], expected)
        self.assertNotEqual(expected, self.data['fingerprints'])
        self.assertEqual(fresh['historical_hashes'], self.data['historical_hashes'])
        self.assertEqual(set(fresh), set(self.data))

    def test_fresh_malformed_scores_still_rejected(self):
        fresh = copy.deepcopy(self.data)
        fresh['fingerprints'] = multi.fingerprints(historical=False)
        next(iter(fresh['queries'].values()))['hits'][0]['score'] = float('nan')
        with self.assertRaisesRegex(ValueError, 'Invalid measured hit'):
            multi.replay(fresh, historical=False)


if __name__ == '__main__':
    unittest.main()
