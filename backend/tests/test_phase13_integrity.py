"""Phase 13 historical/fresh paths using isolated text fixtures; no services."""
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
from evaluation import dataset, historical_integrity as integrity, phase13

MEMBERS = ['corpus.json', 'cases.json', 'fixture_rankings.json']


class Phase13IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.baseline = phase13.measured_rankings()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original = {name: (phase13.ROOT/name).read_bytes()
                         for name in MEMBERS + ['reports/local-model.json']}
        self.prepare('lf')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(phase13, 'ROOT', self.root))
        self.stack.enter_context(patch.object(dataset, 'ROOT', self.root))

    def prepare(self, ending):
        for name, raw in self.original.items():
            raw = raw.replace(b'\r\n', b'\n')
            if ending == 'crlf':
                raw = raw.replace(b'\n', b'\r\n')
            elif ending == 'mixed':
                lines = raw.split(b'\n')
                raw = b''.join(line + (b'\r\n' if i % 2 else b'\n')
                               for i, line in enumerate(lines[:-1])) + lines[-1]
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def fresh(self):
        report = copy.deepcopy(self.baseline[2])
        report['dataset_sha256'] = hashlib.sha256(b''.join((self.root/n).read_bytes() for n in MEMBERS)).hexdigest()
        report['fresh_marker'] = 'new evaluation, not frozen evidence'
        path = self.root/'fresh.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path, report

    def test_historical_newlines_preserve_full_results_and_metrics(self):
        expected_metrics = phase13.compare(*self.baseline[:2])
        for ending in ('lf', 'crlf', 'mixed'):
            with self.subTest(ending=ending):
                self.prepare(ending)
                first = phase13.measured_rankings()
                self.assertEqual(first, self.baseline)
                self.assertEqual(first, phase13.measured_rankings())
                self.assertEqual(phase13.compare(*first[:2]), expected_metrics)
                self.assertEqual(first[2]['dataset_sha256'], integrity.recorded_dataset_fingerprint(root=self.root))

    def test_each_tampered_historical_member_rejected(self):
        for name in MEMBERS + ['reports/local-model.json']:
            self.prepare('lf')
            with self.subTest(name=name):
                with (self.root/name).open('ab') as stream:
                    stream.write(b' ')
                with self.assertRaises(integrity.HistoricalIntegrityError):
                    phase13.measured_rankings()

    def test_missing_historical_evidence_rejected(self):
        for name in MEMBERS + ['reports/local-model.json']:
            self.prepare('lf')
            (self.root/name).unlink()
            with self.subTest(name=name), self.assertRaises(integrity.HistoricalIntegrityError):
                phase13.measured_rankings()

    def test_malformed_dataset_membership_rejected_before_reads(self):
        manifest = integrity._manifest()
        for members in ([], list(reversed(MEMBERS)), MEMBERS + [MEMBERS[0]],
                        MEMBERS[:-1], ['unknown.json'] + MEMBERS[1:], None):
            altered = copy.deepcopy(manifest)
            altered['datasets']['original']['members'] = members
            with self.subTest(members=members), patch.object(integrity, '_manifest', return_value=altered), patch.object(integrity, '_validated_entry') as validate:
                with self.assertRaises(integrity.HistoricalIntegrityError):
                    phase13.measured_rankings()
                validate.assert_not_called()

    def test_fresh_reports_use_exact_current_bytes_for_each_newline_form(self):
        for ending in ('lf', 'crlf', 'mixed'):
            self.prepare(ending)
            path, report = self.fresh()
            with self.subTest(ending=ending), patch.object(phase13, 'recorded_dataset_fingerprint', side_effect=AssertionError('historical path')), patch.object(phase13, 'validate_historical_content', side_effect=AssertionError('historical path')):
                result = phase13.measured_rankings(path)
                self.assertEqual(result[:2], self.baseline[:2])
                self.assertEqual(result[2], report)
                # Even harmless formatting changes invalidate a fresh byte hash.
                with (self.root/'corpus.json').open('ab') as stream:
                    stream.write(b' ')
                with self.assertRaisesRegex(ValueError, 'Baseline dataset mismatch'):
                    phase13.measured_rankings(path)

    def test_fresh_reports_do_not_require_historical_content(self):
        # Valid current dataset differs in formatting; its newly recorded hash wins.
        with (self.root/'cases.json').open('ab') as stream:
            stream.write(b' ')
        path, report = self.fresh()
        self.assertEqual(phase13.measured_rankings(path)[2], report)
        with self.assertRaises(integrity.HistoricalIntegrityError):
            phase13.measured_rankings()

    def test_fresh_validation_and_no_legacy_hash_substitution(self):
        path, report = self.fresh()
        for field, value, error in (
            ('dataset_sha256', self.baseline[2]['dataset_sha256'], 'Baseline dataset mismatch'),
            ('dataset_version', 'wrong', 'Baseline dataset mismatch'),
        ):
            altered = {**report, field: value}
            path.write_text(json.dumps(altered), encoding='utf-8')
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                phase13.measured_rankings(path)
        altered = copy.deepcopy(report)
        altered['cases'][0]['ranked_sources'][0]['page_end'] += 1
        path.write_text(json.dumps(altered), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unsupported baseline page range'):
            phase13.measured_rankings(path)


if __name__ == '__main__':
    unittest.main()
