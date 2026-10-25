"""Measured replay portability and mocked fresh/repeated CLI paths."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import dataset, historical_integrity as integrity, multi_query, phase13, phase16a, phase16r as replay


class MeasuredIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.data, self.timing = replay.load_historical_measurement()
        self.saved = json.loads((replay.ROOT/'reports/phase16a-reranking-measured.json').read_text())
        self.markdown = (replay.ROOT/'reports/phase16a-reranking-measured.md').read_text(encoding='utf-8')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'evaluation'
        manifest = json.loads(integrity.MANIFEST_PATH.read_text())
        self.raw = {n: (replay.ROOT/n).read_bytes() for n in manifest['artifacts']}
        self.prepare('lf')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (replay, phase16a, multi_query, phase13, dataset):
            self.stack.enter_context(patch.object(module, 'ROOT', self.root))
        self.stack.enter_context(patch.object(replay, 'SCORES', self.root/'phase16a_real_scores.json'))
        self.stack.enter_context(patch.object(replay, 'TIMING', self.root/'reports/phase16a-reranking-timing.json'))
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'embeddings.load_model',
                       'rag.get_gemini_provider', 'evaluation.cross_encoder_local.load',
                       'evaluation.cross_encoder_local.download'):
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

    def test_newline_forms_preserve_complete_replay_and_candidate_fingerprint(self):
        for ending in ('lf', 'crlf', 'mixed'):
            with self.subTest(ending=ending):
                self.prepare(ending)
                data, timing = replay.load_historical_measurement()
                self.assertEqual((data, timing), (self.data, self.timing))
                first = replay.compare(data, timing)
                self.assertEqual(first, replay.compare(data, timing))
                self.assertEqual(first, self.saved)
                self.assertEqual(replay.markdown(first), self.markdown)
                self.assertEqual(replay.candidate_fingerprint(), data['candidate_fingerprint'])
                self.assertEqual(replay.historical_hashes(), data['historical_hashes'])

    def test_ten_reports_missing_or_tampered_fail(self):
        for name in self.data['historical_hashes']:
            for missing in (False, True):
                self.prepare('lf')
                path = self.root/'reports'/name
                if missing: path.unlink()
                else: path.write_bytes(path.read_bytes()+b' ')
                with self.subTest(name=name, missing=missing), self.assertRaises(integrity.HistoricalIntegrityError):
                    replay.historical_hashes()

    def test_frozen_scores_timing_missing_or_tampered_fail(self):
        for name in ('phase16a_real_scores.json', 'reports/phase16a-reranking-timing.json'):
            for missing in (False, True):
                self.prepare('lf')
                path = self.root/name
                if missing: path.unlink()
                else: path.write_bytes(path.read_bytes()+b' ')
                with self.subTest(name=name, missing=missing), self.assertRaises(integrity.HistoricalIntegrityError):
                    replay.load_historical_measurement()

    def test_saved_reports_and_offline_check_integrity(self):
        for name in ('reports/phase16a-reranking-measured.json', 'reports/phase16a-reranking-measured.md',
                     'reports/phase16a-reranking-offline-check.json'):
            integrity.validate_historical_content(name, root=self.root)
            path = self.root/name
            path.write_bytes(path.read_bytes()+b' ')
            with self.subTest(name=name), self.assertRaises(integrity.HistoricalIntegrityError):
                integrity.validate_historical_content(name, root=self.root)

    def test_changed_between_validation_and_parsing_rejected(self):
        real = replay.validate_historical_content
        def validate(name, **kwargs):
            digest = real(name, **kwargs)
            (self.root/name).write_bytes(b'{}')
            return digest
        with patch.object(replay, 'validate_historical_content', side_effect=validate):
            with self.assertRaisesRegex(integrity.HistoricalIntegrityError, 'during loading'):
                replay.load_historical_measurement()

    def test_fresh_cli_uses_new_scores_and_timing_without_frozen_validation(self):
        fresh, timing = copy.deepcopy(self.data), {**self.timing, 'inference_seconds': 99.0}
        next(iter(fresh['scores'].values()))[0]['score'] += .01
        replay.SCORES.unlink(); replay.TIMING.unlink()
        with patch.object(sys, 'argv', ['phase16r', '--measure']), patch.object(replay, 'measure', return_value=(fresh,timing)), patch.object(replay, 'load_historical_measurement', side_effect=AssertionError('Frozen input')), patch.object(replay, 'compare', return_value=self.saved) as compare, patch('builtins.print'):
            replay.main()
        compare.assert_called_once_with(fresh, timing)
        self.assertEqual(json.loads(replay.SCORES.read_text()), fresh)
        self.assertEqual(json.loads(replay.TIMING.read_text()), timing)

    def test_repeat_cli_preserves_consistency_checks_without_freezing_new_timing(self):
        for change, error in ((None, None), ('candidate', 'Candidate drift'), ('score', 'repeat tolerance')):
            self.prepare('lf')
            repeated = copy.deepcopy(self.data)
            if change == 'candidate': repeated['candidate_fingerprint'] = 'wrong'
            if change == 'score': next(iter(repeated['scores'].values()))[0]['score'] += 1
            timing = {**self.timing, 'inference_seconds': 77.0}
            with self.subTest(change=change), patch.object(sys, 'argv', ['phase16r', '--verify-offline']), patch.object(replay, 'measure', return_value=(repeated,timing)), patch.object(replay, 'compare', return_value=self.saved), patch('builtins.print'):
                if error:
                    with self.assertRaisesRegex(ValueError, error): replay.main()
                else:
                    replay.main()
                    result=json.loads((self.root/'reports/phase16a-reranking-offline-check.json').read_text())
                    self.assertEqual(result['pairs_verified'],225)
                    self.assertEqual(result['max_absolute_score_difference'],0)
                    self.assertEqual(result['repeated_inference_seconds'],77.0)

    def test_fresh_measure_exact_model_bytes_without_model_loading(self):
        model_root = self.root.parent/'fake-model'
        model_root.mkdir()
        expected = {}
        for i,name in enumerate(replay.adapter.FILES):
            content = b'\x00\xff\r\nmodel\n'+str(i).encode()
            (model_root/name).write_bytes(content)
            expected[name] = hashlib.sha256(content).hexdigest()
        model=SimpleNamespace(tokenizer=lambda a,b,**kwargs: {'input_ids': [[1,2]]*len(a)})
        replay.SCORES.unlink(); replay.TIMING.unlink()
        with patch.object(replay.adapter,'load',return_value=model), patch.object(replay.adapter,'score_pairs',side_effect=lambda model,pairs:[.25]*len(pairs)), patch.object(replay.adapter,'snapshot',return_value=model_root), patch.object(replay,'perf_counter',side_effect=[0.,1.,2.,4.]), patch.object(replay,'version',return_value='test'):
            data,timing=replay.measure()
        self.assertEqual(data['snapshot_sha256'],expected)
        self.assertEqual(data['candidate_fingerprint'],self.data['candidate_fingerprint'])
        self.assertEqual(data['pairs_scored'],225)
        self.assertEqual(timing['inference_seconds'],2.)


if __name__ == '__main__':
    unittest.main()
