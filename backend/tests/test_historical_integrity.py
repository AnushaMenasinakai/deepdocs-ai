"""Offline tests; historical expectations are not read from artifact claims."""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import historical_integrity as integrity


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(integrity.MANIFEST_PATH.read_text(encoding='utf-8'))
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def test_manifest_review_pin_completeness_and_provenance(self):
        raw = integrity.MANIFEST_PATH.read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '6366eea3c7ea3337d461a1f2a5dba3928688dbf2823339069077370c81333591')
        self.assertEqual(self.manifest['schema_version'], 1)
        entries = self.manifest['artifacts']
        self.assertEqual(len(entries), 21)
        self.assertEqual(sum(e['legacy_sha256'] is not None for e in entries.values()), 14)
        for name, entry in entries.items():
            with self.subTest(name=name):
                self.assertFalse(Path(name).is_absolute())
                self.assertNotIn('..', Path(name).parts)
                self.assertRegex(entry['revision'], r'^[0-9a-f]{40}$')
                self.assertRegex(entry['content_sha256_lf'], r'^[0-9a-f]{64}$')
                self.assertEqual(entry['legacy_sha256'] is None, entry['legacy_source'] is None)
                if entry['legacy_source']:
                    source = entry['legacy_source']
                    self.assertRegex(source['revision'], r'^[0-9a-f]{40}$')
                    recorded = json.loads((integrity.ROOT/source['path']).read_text())
                    self.assertEqual(recorded[source['field']][source['key']], entry['legacy_sha256'])

    def test_original_and_all_newline_representations(self):
        for name, entry in self.manifest['artifacts'].items():
            with self.subTest(name=name):
                self.assertEqual(integrity.validate_historical_content(name), entry['content_sha256_lf'])
                lf = (integrity.ROOT/name).read_bytes().replace(b'\r\n', b'\n')
                lines = lf.split(b'\n')
                mixed = b''.join(line + (b'\r\n' if i % 2 else b'\n') for i, line in enumerate(lines[:-1])) + lines[-1]
                for data in (lf, lf.replace(b'\n', b'\r\n'), mixed):
                    self.write(name, data)
                    self.assertEqual(integrity.validate_historical_content(name, root=self.root), entry['content_sha256_lf'])

    def test_whitespace_truncation_lone_cr_and_values_rejected(self):
        for name in self.manifest['artifacts']:
            raw = (integrity.ROOT/name).read_bytes().replace(b'\r\n', b'\n')
            for data in (raw + b' ', raw[:-1], raw.replace(b'\n', b'\r', 1), raw.replace(b'\n', b' \n', 1)):
                with self.subTest(name=name):
                    self.write(name, data)
                    with self.assertRaises(integrity.HistoricalIntegrityError):
                        integrity.validate_historical_content(name, root=self.root)
        name = 'reports/phase16a-reranking.json'
        raw = (integrity.ROOT/name).read_bytes()
        changed = raw.replace(b'"pairs_scored": 0', b'"pairs_scored": 1')
        self.assertNotEqual(raw, changed)
        self.write(name, changed)
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.validate_historical_content(name, root=self.root)

    def test_json_order_and_self_declared_hash_changes_rejected(self):
        name = 'reports/phase16a-reranking.json'
        report = json.loads((integrity.ROOT/name).read_text())
        for altered in (dict(reversed(list(report.items()))), {**report, 'production_sha256': {}}):
            self.write(name, (json.dumps(altered, indent=2)+'\n').encode())
            with self.assertRaises(integrity.HistoricalIntegrityError):
                integrity.validate_historical_content(name, root=self.root)

    def test_missing_unknown_and_aliases_rejected(self):
        for name in ('unknown.json', '../config.py', './corpus.json', 'reports\\fixture.json', str(integrity.ROOT/'corpus.json'), None):
            with self.subTest(name=name), self.assertRaises(integrity.HistoricalIntegrityError):
                integrity.validate_historical_content(name)
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.validate_historical_content('corpus.json', root=self.root)

    def test_incorrect_pin_fails_even_for_original_content(self):
        altered = copy.deepcopy(self.manifest)
        altered['artifacts']['corpus.json']['content_sha256_lf'] = '0'*64
        with patch.object(integrity, '_manifest', return_value=altered):
            with self.assertRaises(integrity.HistoricalIntegrityError):
                integrity.validate_historical_content('corpus.json')

    def test_legacy_available_or_explicitly_unavailable(self):
        for name, entry in self.manifest['artifacts'].items():
            with self.subTest(name=name):
                if entry['legacy_sha256'] is None:
                    with self.assertRaises(integrity.LegacyFingerprintUnavailable):
                        integrity.recorded_legacy_fingerprint(name)
                else:
                    self.assertEqual(integrity.recorded_legacy_fingerprint(name), entry['legacy_sha256'])
                self.write(name, b'tampered')
                with self.assertRaises(integrity.HistoricalIntegrityError) as error:
                    integrity.recorded_legacy_fingerprint(name, root=self.root)
                self.assertNotIsInstance(error.exception, integrity.LegacyFingerprintUnavailable)

    def test_dataset_membership_order_and_each_member_required(self):
        entry = self.manifest['datasets']['original']
        self.assertEqual(entry['members'], ['corpus.json', 'cases.json', 'fixture_rankings.json'])
        source = json.loads((integrity.ROOT/'reports/local-model.json').read_text())
        self.assertEqual(entry['legacy_sha256'], source['dataset_sha256'])
        with patch.object(integrity, '_validated_entry', wraps=integrity._validated_entry) as validate:
            self.assertEqual(integrity.recorded_dataset_fingerprint(), entry['legacy_sha256'])
            self.assertEqual([call.args[0] for call in validate.call_args_list], entry['members'])
        for name in entry['members']:
            self.write(name, (integrity.ROOT/name).read_bytes())
        for name in entry['members']:
            self.write(name, b'tampered')
            with self.assertRaises(integrity.HistoricalIntegrityError):
                integrity.recorded_dataset_fingerprint(root=self.root)
            self.write(name, (integrity.ROOT/name).read_bytes())
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.recorded_dataset_fingerprint('unknown')

    def test_current_bytes_are_distinct_and_binary_is_not_normalized(self):
        path = self.root/'model.bin'
        hashes = []
        for data in (b'current\nsource', b'current\r\nsource', b'\x00\xff\r\n\rmodel'):
            path.write_bytes(data)
            digest = integrity.current_file_byte_fingerprint(path)
            self.assertEqual(digest, hashlib.sha256(data).hexdigest())
            hashes.append(digest)
        self.assertEqual(len(set(hashes)), 3)
        with self.assertRaises(integrity.HistoricalIntegrityError):
            integrity.validate_historical_content('model.bin', root=self.root)


if __name__ == '__main__':
    unittest.main()
