"""Offline harness guards. Never invokes Docker or a database."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mongodb_smoke', ROOT / 'smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class IsolationTests(unittest.TestCase):
    def test_safe_schema_diagnostic(self):
        error = smoke.StorageSchemaError('secret-provider-response',
                                        operation='create_index', reason='deadline_exceeded')
        self.assertEqual(smoke.failure_diagnostic(error),
                         'FAIL: schema validation; operation=create_index; reason=deadline_exceeded')
        error.operation = 'secret-uri'
        error.reason = 'secret-pdf'
        self.assertEqual(smoke.failure_diagnostic(error),
                         'FAIL: schema validation; operation=unknown; reason=unknown')

    def setUp(self):
        self.config = dict(database='dd_smoke_123_1', replica='dd_smoke_123_1', password='a' * 64)
        self.hello = dict(setName='dd_smoke_123_1', hosts=['localhost:27017'], isWritablePrimary=True)

    def test_valid_fixture(self):
        smoke.guard(self.config, self.hello)

    def test_unexpected_config_rejected(self):
        for change in ({'database': 'deepdocs'}, {'database': '../dd_smoke_1_1'},
                       {'replica': 'production'}, {'password': 'short'},
                       {'uri': 'mongodb://unexpected'}, {'database': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                smoke.guard(self.config | change)

    def test_unexpected_replica_rejected(self):
        for change in ({'setName': 'other'}, {'hosts': ['remote:27017']},
                       {'hosts': ['localhost:27017', 'remote:27017']},
                       {'isWritablePrimary': False}, {'passives': ['remote:27017']},
                       {'arbiters': ['remote:27017']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                smoke.guard(self.config, self.hello | change)

    def test_no_production_configuration_or_host_publishing(self):
        script = (ROOT / 'run.sh').read_text()
        self.assertIn('--network none', script)
        self.assertIn('--network "container:$TEST_ID"', script)
        self.assertIn('--tlsMode requireTLS', script)
        self.assertIn('--read-only --cap-drop ALL', script)
        for forbidden in ('--publish', '--env-file', 'backend/.env', 'docker system prune'):
            self.assertNotIn(forbidden, script)
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertNotIn('COPY . ', dockerfile)
        self.assertIn('pymongo==4.18.1 dnspython==2.8.0', dockerfile)

    def test_pinned_image_and_readonly_runtime_paths(self):
        script = (ROOT / 'run.sh').read_text()
        self.assertIn('mongo:8.0.38-noble@sha256:9b73c38e39783bd21335f603a9f08be60fe385f5c28933ea32bb4a9fa1fd6d23', script)
        self.assertIn('docker pull --platform linux/amd64 "$MONGO_IMAGE"', script)
        self.assertIn('--entrypoint mongod "$MONGO_IMAGE"', script)
        self.assertEqual(script.count('docker exec --env HOME=/tmp'), 2)
        self.assertIn('chmod 644 "$work/build/backend/"*.py', script)
        self.assertIn('-verify_hostname localhost', script)
        self.assertIn('-verify_ip 127.0.0.1', script)

    def test_cleanup_consumer_before_namespace_owner(self):
        workflow = (ROOT.parents[1] / '.github/workflows/mongodb-replica-smoke.yml').read_text()
        self.assertIn('for name in "${TEST_ID}_test" "$TEST_ID"', workflow)
        self.assertIn('if: always()', workflow)
        self.assertIn('index .Config.Labels "deepdocs.smoke"', workflow)
