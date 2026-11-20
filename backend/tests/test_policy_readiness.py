"""Offline optional-schema and bulk-policy contracts; no provider/database calls."""
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from bson import ObjectId
import policy_readiness as policy
from readiness import Readiness, INDEX_GROUPS
from storage_schema import INDEXES
from growth_limits import GrowthError
from usage_budgets import BudgetError
import document_bulk


class BulkPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_results_and_no_retry(self):
        cases = [(BudgetError(True), 'budget_exhausted'), (GrowthError(True), 'growth_limit_reached'),
                 (BudgetError(), 'accounting_unavailable'), (GrowthError(), 'accounting_unavailable')]
        ids = [str(ObjectId()) for _ in range(3)]
        for error, code in cases:
            for operation in ('reindex', 'delete'):
                with self.subTest(code=code, operation=operation):
                    target = 'document_bulk.document_embeddings.generate_embeddings' if operation == 'reindex' else 'document_bulk.documents.delete_document'
                    with patch(target, new_callable=AsyncMock, side_effect=[True, error]) as call:
                        if operation == 'reindex':
                            result = await document_bulk._reindex_documents(None, ObjectId(), ObjectId(), ids, None, None)
                        else:
                            result = await document_bulk.delete_documents(None, None, ObjectId(), ObjectId(), ids)
                    self.assertEqual(call.await_count, 2)
                    self.assertEqual([r.outcome for r in result.results], ['succeeded', 'failed', 'not_attempted'])
                    self.assertEqual(result.results[1].code, code)
                    self.assertEqual((result.succeeded, result.failed, result.not_attempted), (1, 1, 1))


class PolicyReadinessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.budget = self.stack.enter_context(patch.object(policy.config, 'load_usage_budget_settings', return_value=SimpleNamespace(enabled=False)))
        self.growth = self.stack.enter_context(patch.object(policy.config, 'load_growth_settings', return_value=SimpleNamespace(enabled=False)))
        self.gridfs = self.stack.enter_context(patch.object(policy.config, 'load_gridfs_upload_enabled', return_value=False))
        self.indexes = {}
        for i in INDEXES:
            self.indexes.setdefault(i.collection, {})[i.name] = {'key': list(i.keys), 'unique': i.unique}
        self.indexes.update(resource_usage={'_id_': {'key': [('_id', 1)]}},
            ask_history={'ask_history_owner_created': {'key': [('owner_id', 1), ('created_at', -1), ('_id', -1)]}},
            documents={'documents_owner_created': {'key': [('owner_id', 1), ('created_at', -1), ('_id', -1)]}},
            knowledge_bases={'knowledge_bases_owner_updated': {'key': [('owner_id', 1), ('updated_at', -1), ('_id', -1)]}},
            gemini_daily_usage={'_id_': {'key': [('_id', 1)]}, 'gemini_usage_expiry': {'key': [('expires_at', 1)], 'expireAfterSeconds': 0}})
        self.session = AsyncMock()
        self.session.__aenter__.return_value = self.session
        self.read = AsyncMock(return_value=None)
        def collection(name):
            return SimpleNamespace(index_information=AsyncMock(return_value=self.indexes.get(name, {})), find_one=self.read)
        self.db = SimpleNamespace(get_collection=Mock(side_effect=collection), create_collection=AsyncMock(),
            client=SimpleNamespace(start_session=Mock(return_value=self.session)), command=AsyncMock(return_value={
                'ok': 1, 'setName': 'synthetic', 'isWritablePrimary': True, 'logicalSessionTimeoutMinutes': 30, 'maxWireVersion': 25}))

    async def ready(self):
        with patch('readiness.configuration_ready', return_value=True): state = Readiness()
        state.initialized = True; state.indexes = dict.fromkeys(INDEX_GROUPS, True)
        try: return await state.check(self.db)
        finally: await state.close()

    async def test_disabled_skips_optional_access_and_runtime(self):
        self.assertTrue(await policy.verify(self.db))
        self.db.get_collection.assert_not_called(); self.db.command.assert_not_awaited()
        await policy.setup_growth_identity(self.db)
        self.db.create_collection.assert_not_awaited()
        self.assertTrue(policy.runtime_ready(SimpleNamespace()))

    async def test_budget_indexes_missing_or_incompatible_fail_closed(self):
        self.budget.return_value.enabled = True
        self.assertTrue(await self.ready())
        expiry = self.indexes['gemini_daily_usage']['gemini_usage_expiry']
        for key, bad in [('expireAfterSeconds', 10), ('sparse', True), ('hidden', True), ('partialFilterExpression', {})]:
            with patch.dict(expiry, {key: bad}): self.assertFalse(await self.ready())
        self.indexes['gemini_daily_usage'].clear()
        self.assertFalse(await self.ready())

    async def test_combined_manifest_and_snapshot_read(self):
        self.growth.return_value.enabled = True; self.gridfs.return_value = True
        self.assertTrue(await self.ready())
        self.session.start_transaction.assert_awaited_once()
        self.session.abort_transaction.assert_awaited_once()
        self.read.assert_awaited_once()
        self.assertFalse(policy.runtime_ready(SimpleNamespace()))
        self.assertTrue(policy.runtime_ready(SimpleNamespace(gridfs_uploads=object(), gridfs_processing=object(), gridfs_deletion=object())))
        self.indexes['pdf_blobs.chunks']['files_id_1_n_1']['unique'] = False
        self.assertFalse(await self.ready())

    async def test_transaction_prerequisites_and_read_failure(self):
        self.growth.return_value.enabled = True
        good = self.db.command.return_value.copy()
        for bad in ({'setName': None}, {'logicalSessionTimeoutMinutes': None}, {'isWritablePrimary': False}, {'maxWireVersion': 6}):
            self.db.command.return_value = good | bad
            self.assertFalse(await self.ready())
        self.db.command.return_value = good
        self.read.side_effect = OSError('private')
        self.assertFalse(await self.ready())
        self.session.abort_transaction.assert_awaited_once()

    async def test_failed_growth_initialization_and_missing_identity(self):
        self.growth.return_value.enabled = True
        self.db.create_collection.side_effect = OSError('private')
        with self.assertRaises(OSError): await policy.setup_growth_identity(self.db)
        self.indexes['resource_usage'].clear()
        self.assertFalse(await self.ready())
