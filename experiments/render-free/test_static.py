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

class ResourceScreen(unittest.TestCase):
    def record(self, peak=400 * 1024**2):
        from assessment import REQUIRED_STAGES
        return {'complete': True, 'exit_code': 0, 'oom_killed': False, 'timed_out': False,
                'verified_limits': {'memory_bytes': 512 * 1024**2, 'swap_bytes': 0, 'cpus': .1},
                'memory_events': 'oom 0\noom_kill 0',
                'cpu_stat': 'usage_usec 1\nnr_throttled 1\nthrottled_usec 1',
                'kernel_peak_bytes': peak, 'events': [
            {'event': 'end', 'stage': name, 'seconds': 1, 'current_bytes': peak,
             'lifetime_peak_bytes': peak, 'cpu_delta': {'usage_usec': 1, 'nr_throttled': 1, 'throttled_usec': 1}}
            for name in REQUIRED_STAGES] + [{'event': 'application_complete'}, {'event': 'complete'}]}


    def test_complete_screen_is_not_deployment_approval(self):
        from assessment import assess
        self.assertIn('live_full_backend_validation_still_required', assess(self.record())['screen'])

    def test_insufficient_margin(self):
        from assessment import assess
        self.assertEqual(assess(self.record(450 * 1024**2))['screen'], 'insufficient_20_percent_headroom')

    def test_incomplete_oom_timeout_missing_stage(self):
        from assessment import assess
        for change in ({'complete': False}, {'oom_killed': True}, {'timed_out': True}, {'events': []}):
            self.assertIn('unsuitable_for_approval', assess({**self.record(), **change})['screen'])

    def test_calibration_cannot_approve(self):
        from assessment import assess
        self.assertEqual(assess({**self.record(), 'calibration': True})['screen'], 'calibration_only')

    def test_slow_request(self):
        from assessment import assess
        record = self.record()
        for event in record['events']:
            if event.get('stage') == 'rag_request_2':
                event['seconds'] = 60
        self.assertEqual(assess(record)['screen'], 'request_latency_exceeds_frontend_budget')

    def test_missing_measurements_fail_closed(self):
        from assessment import assess
        for key in ('oom_killed', 'timed_out', 'exit_code', 'kernel_peak_bytes',
                    'verified_limits', 'memory_events', 'cpu_stat'):
            record = self.record()
            del record[key]
            self.assertIn('unsuitable_for_approval', assess(record)['screen'], key)
        for key in ('current_bytes', 'lifetime_peak_bytes', 'seconds', 'cpu_delta'):
            record = self.record()
            del record['events'][0][key]
            self.assertIn('unsuitable_for_approval', assess(record)['screen'], key)

    def test_invalid_measurements_and_limits_fail_closed(self):
        from assessment import assess
        for change in ({'seconds': float('nan')}, {'seconds': -1}, {'current_bytes': 0},
                       {'lifetime_peak_bytes': None}, {'cpu_delta': {}}):
            record = self.record()
            record['events'][0].update(change)
            self.assertIn('unsuitable_for_approval', assess(record)['screen'])
        for change in ({'memory_events': 'oom 1\noom_kill 0'}, {'cpu_stat': ''},
                       {'verified_limits': {'memory_bytes': 512 * 1024**2, 'swap_bytes': 1, 'cpus': .1}}):
            self.assertIn('unsuitable_for_approval', assess({**self.record(), **change})['screen'])

    def test_duplicate_stage_or_missing_completion_fails(self):
        from assessment import assess
        record = self.record()
        record['events'].append(record['events'][0])
        self.assertIn('unsuitable_for_approval', assess(record)['screen'])
        record = self.record()
        record['events'] = [e for e in record['events'] if e['event'] != 'application_complete']
        self.assertIn('unsuitable_for_approval', assess(record)['screen'])

    def test_full_probe_execution_chain(self):
        self.assertIn('from assessment import assess', SOURCE)
        self.assertIn("raise SystemExit('Constrained resource screening", SOURCE)
        self.assertIn('application_probe.py', (HERE / 'Dockerfile').read_text())
        self.assertIn('asyncio.run(run_application(', (HERE / 'probe.py').read_text())
        self.assertIn('workers=1, proxy_headers=False', (HERE / 'application_probe.py').read_text())
        workflow = (HERE.parents[1] / '.github/workflows/render-free-resource-experiment.yml').read_text()
        self.assertIn('python3 experiments/render-free/run.py', workflow)
        self.assertNotIn('secrets.', workflow)

    def test_experiment_copy_sources_are_allowlisted(self):
        allowed = {line[1:] for line in (HERE / '.dockerignore').read_text().splitlines()
                   if line.startswith('!')}
        for line in (HERE / 'Dockerfile').read_text().splitlines():
            if line.startswith('COPY '):
                for source in line.split()[1:-1]:
                    self.assertTrue((HERE / source).is_file(), source)
                    self.assertIn(source, allowed)

    def test_kernel_endpoint_peak_is_retained(self):
        from assessment import assess
        record = self.record(450 * 1024**2)
        record['kernel_peak_bytes'] = 1
        self.assertEqual(assess(record)['peak_bytes'], 450 * 1024**2)

if __name__ == '__main__':
    unittest.main()
