"""Offline harness contract tests; never import models or invoke Docker."""
import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from pooling_contract import require_mean_pooling

HERE = Path(__file__).resolve().parent
SOURCE = (HERE / 'run.py').read_text()
node = next(n for n in ast.parse(SOURCE).body if isinstance(n, ast.FunctionDef) and n.name == 'compare')
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), '<comparison>', 'exec'), namespace)
compare = namespace['compare']

def result(values):
    return {'events':[{'event':'vectors', 'corpus_count':2, 'values':values}]}

class Contracts(unittest.TestCase):
    def pooler(self, **changes):
        config = dict(embedding_dimension=384, pooling_mode='mean', include_prompt=True)
        config.update(changes)
        return SimpleNamespace(**config, get_config_dict=lambda: config)

    def test_missing_legacy_key_regression(self):
        pooler = self.pooler()
        self.assertNotIn('pooling_mode_mean_tokens', pooler.get_config_dict())
        self.assertEqual(require_mean_pooling(pooler), 'mean')

    def test_single_mean_sequence_formats(self):
        for mode in (['mean'], ('mean',)):
            self.assertEqual(require_mean_pooling(self.pooler(pooling_mode=mode)), 'mean')

    def test_unsupported_pooling_fails_explicitly(self):
        for mode in (None, True, [], 'max', 'cls', ['mean', 'max'], ['mean', 'mean']):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'explicit mean-only'):
                require_mean_pooling(self.pooler(pooling_mode=mode))

    def test_effective_mode_disagreement_rejected(self):
        pooler = self.pooler()
        pooler.pooling_mode = 'max'
        with self.assertRaisesRegex(ValueError, 'explicit mean-only'):
            require_mean_pooling(pooler)

    def test_unsupported_dimensions_and_prompt_exclusion(self):
        for change in ({'embedding_dimension':768}, {'include_prompt':False},
                       {'pooling_mode_mean_tokens':True}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'Unsupported pooling'):
                require_mean_pooling(self.pooler(**change))

    def test_no_default_for_missing_mode(self):
        pooler = self.pooler()
        del pooler.get_config_dict()['pooling_mode']
        with self.assertRaisesRegex(ValueError, 'explicit mean-only'):
            require_mean_pooling(pooler)

    def test_identical_vectors(self):
        a, b = [1.] + [0.]*383, [0.,1.] + [0.]*382
        out = compare(result([a,b,a]), result([a,b,a]))
        self.assertEqual(out['minimum_paired_cosine'], 1.)
        self.assertTrue(out['queries'][0]['top5_equal'])

    def test_ranking_difference(self):
        a, b = [1.] + [0.]*383, [0.,1.] + [0.]*382
        out = compare(result([a,b,a]), result([a,b,b]))
        self.assertFalse(out['queries'][0]['top5_equal'])

    def test_invalid_dimensions_rejected(self):
        with self.assertRaises(AssertionError):
            compare(result([[1.]]*3), result([[1.]]*3))

    def test_nonfinite_rejected(self):
        with self.assertRaises(AssertionError):
            compare(result([[float('nan')]*384]*3), result([[1.]*384]*3))

    def test_threshold_changes_visible(self):
        import math
        a, b = [1.] + [0.]*383, [-1.] + [0.]*383
        query = lambda v: [v, math.sqrt(1-v*v)] + [0.]*382
        out = compare(result([a,b,query(.499)]), result([a,b,query(.501)]))
        self.assertFalse(out['queries'][0]['primary_gate_equal'])

    def test_container_safeguards(self):
        for token in ['--network', "'none'", '--read-only', '--memory-swap', "'512m'",
                      "'0.1'", '--pids-limit', 'no-new-privileges', '--cap-drop',
                      'readonly', 'memory.swap.max', 'cpu.max', 'GITHUB_ACTIONS']:
            self.assertIn(token, SOURCE)
        self.assertNotIn('docker cp', SOURCE)

    def test_model_paths_and_no_production_edits(self):
        dockerfile = (HERE / 'Dockerfile').read_text()
        self.assertIn('RUN --network=none', dockerfile)
        self.assertIn('USER 10001:10001', dockerfile)
        self.assertIn('dynamo=False', (HERE / 'export.py').read_text())
        self.assertIn("'torch' not in sys.modules", (HERE / 'probe.py').read_text())

if __name__ == '__main__':
    unittest.main()
