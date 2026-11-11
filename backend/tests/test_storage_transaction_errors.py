import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from pymongo.errors import OperationFailure, ConnectionFailure, DuplicateKeyError
from storage_intents import StorageIntents, StorageTransactionError, transaction_error_category
from storage_contract import StorageUncertain
from test_storage_intents import Database


class TransactionErrorTests(unittest.TestCase):
    def test_concurrent_first_ledger_unique_identity_conflict(self):
        # Focused race double: both clients observe absence before either upsert.
        # It models the unique-index loser, not MongoDB transaction isolation.
        barrier, lock, rows = threading.Barrier(2), threading.Lock(), {}
        class Ledger:
            def find_one(self, query, **kwargs):
                barrier.wait(timeout=5)
                return None
            def replace_one(self, query, value, **kwargs):
                with lock:
                    if rows:
                        raise DuplicateKeyError('private', 11000)
                    rows[value['_id']] = value
        db = SimpleNamespace(get_collection=lambda name: Ledger())
        def initialize(_):
            store = StorageIntents(db)
            try:
                store._write_ledger(store._ledger(None), None)
                return 'created'
            except DuplicateKeyError as error:
                return transaction_error_category(error, 'body')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(initialize, range(2)))
        self.assertCountEqual(results, ['created', 'duplicate_identity_conflict'])
        self.assertEqual(list(rows), ['pdf_payload_v1'])

    def test_body_conflict_and_duplicate_initialization(self):
        for error, category in ((OperationFailure('private', 112), 'transaction_write_conflict'),
                                (DuplicateKeyError('private', 11000), 'duplicate_identity_conflict')):
            db = Database()
            calls = 0
            with self.assertRaises(StorageTransactionError) as caught:
                with StorageIntents(db).transaction():
                    calls += 1
                    raise error
            self.assertEqual(calls, 1)
            self.assertEqual(caught.exception.category, category)
            self.assertIsInstance(caught.exception, StorageUncertain)
            self.assertNotIn('private', str(caught.exception))

    def test_commit_labels_and_priority(self):
        for labels, category in ((['RetryableWriteError'], 'retryable_commit_acknowledgement'),
                                (['UnknownTransactionCommitResult'], 'unknown_commit_result'),
                                (['UnknownTransactionCommitResult', 'RetryableWriteError'], 'unknown_commit_result')):
            error = OperationFailure('private', details={'errorLabels': labels})
            self.assertEqual(transaction_error_category(error, 'commit'), category)

    def test_lost_commit_acknowledgement_is_retained(self):
        db = Database(); db.failure = 'after_commit'
        with self.assertRaises(StorageTransactionError) as caught:
            with StorageIntents(db).transaction():
                db.rows['committed'] = {}
        self.assertIn('committed', db.rows)
        self.assertEqual(caught.exception.category, 'unknown_commit_result')
        self.assertEqual(caught.exception.phase, 'commit')

    def test_session_failure_precedes_body(self):
        db = Database()
        with patch.object(db.client, 'start_session', side_effect=ConnectionFailure('private')):
            with self.assertRaises(StorageTransactionError) as caught:
                with StorageIntents(db).transaction():
                    self.fail('body must not run')
        self.assertEqual(caught.exception.category, 'pre_dispatch_failure')

    def test_unlabeled_body_failure_is_uncertain(self):
        self.assertEqual(transaction_error_category(ConnectionFailure('private'), 'body'),
                         'transaction_outcome_uncertain')
