"""Offline harness guards. Never invokes Docker or a database."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout
from io import StringIO

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mongodb_smoke', ROOT / 'smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
budget_spec = importlib.util.spec_from_file_location('smoke_budgets', ROOT / 'budgets.py')
budget_checks = importlib.util.module_from_spec(budget_spec)
budget_spec.loader.exec_module(budget_checks)
combined_spec = importlib.util.spec_from_file_location('smoke_combined', ROOT / 'combined.py')
combined_checks = importlib.util.module_from_spec(combined_spec)
combined_spec.loader.exec_module(combined_checks)


class CombinedHarnessTests(unittest.TestCase):
    def test_lost_ack_occurs_after_commit_without_replay(self):
        from contextlib import contextmanager
        events = []
        @contextmanager
        def transaction():
            events.append('begin')
            yield 'session'
            events.append('commit')
        store = Mock(transaction=transaction)
        with combined_checks.lost_commit(store):
            with self.assertRaises(combined_checks.StorageTransactionError) as caught:
                with store.transaction() as session:
                    self.assertEqual(session, 'session')
                    events.append('write')
        self.assertEqual(events, ['begin', 'write', 'commit'])
        self.assertEqual(caught.exception.category, 'unknown_commit_result')
        self.assertIs(store.transaction, transaction)

    def test_body_failure_is_not_mislabeled_as_lost_commit(self):
        from contextlib import contextmanager
        events = []
        @contextmanager
        def transaction():
            yield None
            events.append('commit')
        store = Mock(transaction=transaction)
        with combined_checks.lost_commit(store), self.assertRaises(ValueError):
            with store.transaction():
                raise ValueError('synthetic')
        self.assertEqual(events, [])

    def test_rejection_helper_does_not_swallow_other_failures(self):
        def wrong(): raise RuntimeError('synthetic')
        with self.assertRaises(RuntimeError): combined_checks.rejected(wrong)
        with self.assertRaises(AssertionError): combined_checks.rejected(lambda: None)

    def test_combined_sources_and_execution_are_allowlisted(self):
        docker = (ROOT / 'Dockerfile').read_text()
        script = (ROOT / 'run.sh').read_text()
        for name in ('combined.py', 'growth_limits.py', 'gridfs_growth.py',
                     'gridfs_growth_recovery.py', 'policy_readiness.py'):
            self.assertIn(name, docker)
            self.assertIn(name, script)
        smoke_source = (ROOT / 'smoke.py').read_text()
        self.assertLess(smoke_source.index('lifecycle(database, reconnect, pdfs)'), smoke_source.index('validate_sync(reconnect, pdfs)'))
        self.assertEqual(smoke_source.count('pdfs = fixtures()'), 1)
        budget_source = (ROOT / 'budgets.py').read_text()
        self.assertLess(budget_source.index('await validate(databases)'), budget_source.index('await validate_async(databases)'))
        self.assertIn('180s docker run', script)
        source = (ROOT / 'combined.py').read_text()
        for forbidden in ('load_dotenv', 'load_settings(', 'MongoClient(', 'mongodb+srv',
                          'google.genai', 'qdrant_client', 'drop_database', 'drop_collection'):
            self.assertNotIn(forbidden, source)
        for required in ('barrier.wait(timeout=5)', 'store.finalize_deletion',
                         'session.abort_transaction()', 'not result.mutation_authorized',
                         'assert await policy_readiness.verify(db)', 'assert held(interrupted) == (True, True)'):
            self.assertIn(required, source)


class BudgetHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ack_loss_follows_exactly_one_completed_write(self):
        from unittest.mock import AsyncMock
        for method in ('find_one_and_update', 'update_one'):
            collection = Mock()
            mutation = AsyncMock(return_value={'synthetic': True})
            setattr(collection, method, mutation)
            wrapped = budget_checks.LostAcknowledgement(collection, method)
            with self.assertRaises(budget_checks.AutoReconnect):
                await getattr(wrapped, method)({'synthetic': True})
            mutation.assert_awaited_once()
            self.assertTrue(wrapped.injected)
            self.assertEqual(await getattr(wrapped, method)({}), {'synthetic': True})
            self.assertEqual(mutation.await_count, 2)

    async def test_rejected_identity_never_opens_client(self):
        with patch.object(budget_checks, 'AsyncMongoClient') as client:
            with self.assertRaises(ValueError):
                await budget_checks.run({'database': 'production'}, smoke.guard)
            client.assert_not_called()

    async def test_unexpected_replica_closes_client_before_writes(self):
        from unittest.mock import AsyncMock
        client = Mock()
        client.admin.command = AsyncMock(return_value={'setName': 'wrong'})
        client.close = AsyncMock()
        config = dict(database='dd_smoke_123_1', replica='dd_smoke_123_1', password='a' * 64)
        with patch.object(budget_checks, 'AsyncMongoClient', return_value=client), \
             patch.object(budget_checks, 'validate', new_callable=AsyncMock) as validate:
            with self.assertRaises(ValueError):
                await budget_checks.run(config, smoke.guard)
            validate.assert_not_awaited()
            client.get_database.assert_not_called()
            client.close.assert_awaited_once()

    def test_budget_sources_and_invocation_are_allowlisted(self):
        docker = (ROOT / 'Dockerfile').read_text()
        script = (ROOT / 'run.sh').read_text()
        source = (ROOT / 'budgets.py').read_text()
        for name in ('budgets.py', 'usage_budgets.py', 'config.py'):
            self.assertIn(name, docker)
            self.assertIn(name, script)
        self.assertIn('asyncio.run(run_budgets(config, guard))', (ROOT / 'smoke.py').read_text())
        self.assertIn('python-dotenv==1.2.3', docker)
        self.assertIn('retryWrites=False, retryReads=False', source)
        self.assertIn('asyncio.timeout(60)', source)
        for forbidden in ('load_dotenv', 'load_settings(', 'google.genai', 'qdrant_client', 'mongodb+srv'):
            self.assertNotIn(forbidden, source)


class IsolationTests(unittest.TestCase):
    def test_lifecycle_synthetic_pdf_and_parser_contract(self):
        import tempfile
        from types import SimpleNamespace
        spec = importlib.util.spec_from_file_location('smoke_lifecycle', ROOT / 'lifecycle.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixtures = module.fixtures()
        self.assertEqual(len(fixtures), 2)
        for data, expected in fixtures:
            self.assertLess(len(data), 10000)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'synthetic.pdf'
                path.write_bytes(data)
                pages, chunks = module.extract_chunks(path, SimpleNamespace(max_source_bytes=10000,
                    max_pages=2, max_text_characters=10000, chunk_target=1000, chunk_overlap=150, max_chunks=10))
                self.assertEqual(pages, 1)
                self.assertIn(expected, chunks[0]['text'])

    def test_lifecycle_is_wired_without_production_dependencies(self):
        script = (ROOT / 'run.sh').read_text()
        docker = (ROOT / 'Dockerfile').read_text()
        lifecycle = (ROOT / 'lifecycle.py').read_text()
        self.assertIn('lifecycle.py', script)
        self.assertIn('lifecycle.py', docker)
        self.assertIn('lifecycle(database, reconnect, pdfs)', (ROOT / 'smoke.py').read_text())
        for forbidden in ('load_settings', 'load_dotenv', 'qdrant_client', 'google.genai'):
            self.assertNotIn(forbidden, lifecycle)
        self.assertIn('ledger.finalize_deletion', lifecycle)
        self.assertIn('threading.Barrier(2)', lifecycle)
        self.assertIn('not inspection.mutation_authorized', lifecycle)

    def test_dbpath_has_headroom_without_lowering_server_safeguard(self):
        script = (ROOT / 'run.sh').read_text()
        self.assertIn('--tmpfs /data/db:rw,nosuid,noexec,size=1g,uid=999,gid=999,mode=700', script)
        self.assertIn('--memory 2g --memory-swap 2g', script)
        self.assertNotIn('--setParameter', script)
        self.assertIn('df -Pk /data/db', script)
        self.assertIn('available_kib < 614400', script)
        self.assertLess(script.index('df -Pk /data/db'), script.index('docker run --name "${TEST_ID}_test"'))

    def test_disk_error_diagnostic_is_sanitized(self):
        index = next(i for i in smoke.INDEXES if i.name == 'storage_intent_owner_document')
        error = smoke.StorageSchemaError('private', operation='create_index',
            index=index, reason='out_of_disk_space', server_code=14031,
            driver_category='OperationFailure')
        diagnostic = smoke.failure_diagnostic(error)
        self.assertIn('reason=out_of_disk_space', diagnostic)
        self.assertIn('server_code=14031', diagnostic)
        self.assertNotIn('private', diagnostic)

    def test_failed_mutation_readback_does_not_resume_setup(self):
        index = next(i for i in smoke.INDEXES if i.name == 'files_id_1_n_1')
        error = smoke.StorageSchemaError('secret', operation='create_index', index=index,
                                        server_code=197, driver_category='OperationFailure',
                                        outcome='command_rejected')
        for state in ('confirmed_definition', 'incompatible_definition', 'not_observed', 'unavailable'):
            with patch.object(smoke, 'setup_storage_schema', side_effect=error) as setup, \
                 patch.object(smoke, 'inspect_failed_index', return_value=state) as inspect, \
                 redirect_stdout(StringIO()) as output:
                with self.assertRaises(smoke.StorageSchemaError): smoke.setup_with_diagnostics(Mock())
                setup.assert_called_once()
                inspect.assert_called_once()
                self.assertEqual(output.getvalue().strip(), 'Index readback: ' + state)
        message = smoke.failure_diagnostic(error)
        self.assertIn('index=files_id_1_n_1', message)
        self.assertIn('server_code=197', message)
        self.assertIn('command_outcome=command_rejected', message)
        self.assertNotIn('secret', message)

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
