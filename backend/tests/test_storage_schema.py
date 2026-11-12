import copy
import unittest
from unittest.mock import Mock
from pymongo.errors import OperationFailure, ExecutionTimeout, ConnectionFailure, WriteConcernError
from storage_schema import (INDEXES, setup_storage_schema, verify_storage_schema,
                            StorageSchemaError, inspect_failed_index)


class Database:
    def __init__(self):
        self.rows = {}
        self.writes = 0

    def list_collection_names(self):
        return list(self.rows)

    def create_collection(self, name):
        self.writes += 1
        self.rows[name] = {'_id_': {'name': '_id_', 'key': {'_id': 1}}}

    def get_collection(self, name):
        db = self
        class Collection:
            def list_indexes(self):
                return list(db.rows[name].values())

            def create_index(self, keys, *, name, unique):
                db.writes += 1
                db.rows[collection][name] = dict(name=name, key=dict(keys), unique=unique)
        collection = name
        return Collection()


class SchemaTests(unittest.TestCase):
    def test_out_of_disk_space_is_specific_but_does_not_authorize_retry(self):
        db = Database(); setup_storage_schema(db)
        name = 'storage_intent_owner_document'
        del db.rows['storage_operation_intents'][name]
        original = db.get_collection
        calls = []
        def get(collection_name):
            collection = original(collection_name)
            def create(*args, **kwargs):
                calls.append(kwargs['name'])
                raise OperationFailure('private server details', 14031)
            collection.create_index = create
            return collection
        db.get_collection = get
        with self.assertRaises(StorageSchemaError) as caught:
            setup_storage_schema(db)
        error = caught.exception
        self.assertEqual(error.reason, 'out_of_disk_space')
        self.assertEqual(error.server_code, 14031)
        self.assertEqual(error.index.name, name)
        self.assertEqual(error.outcome, 'unknown')  # May also abort an active build.
        self.assertEqual(inspect_failed_index(db, error), 'not_observed')
        self.assertEqual(calls, [name])
        self.assertNotIn('private', str(error))

    def test_index_failure_identity_code_and_outcome(self):
        for failure, outcome in ((OperationFailure('private', 197), 'command_rejected'),
                                 (OperationFailure('private', 99999), 'unknown'),
                                 (WriteConcernError('private', 64), 'unknown'),
                                 (ConnectionFailure('private'), 'unknown')):
            with self.subTest(outcome=outcome, error=type(failure).__name__):
                db = Database(); setup_storage_schema(db)
                name = 'files_id_1_n_1'
                del db.rows['pdf_blobs.chunks'][name]
                original = db.get_collection
                calls = []
                def get(collection_name):
                    collection = original(collection_name)
                    def create(*args, **kwargs):
                        calls.append(kwargs['name'])
                        raise failure
                    collection.create_index = create
                    return collection
                db.get_collection = get
                with self.assertRaises(StorageSchemaError) as caught:
                    setup_storage_schema(db)
                error = caught.exception
                self.assertEqual(error.index.name, name)
                self.assertEqual(error.index.collection, 'pdf_blobs.chunks')
                self.assertEqual(error.outcome, outcome)
                self.assertEqual(error.server_code, getattr(failure, 'code', None))
                self.assertEqual(calls, [name])
                self.assertEqual(inspect_failed_index(db, error), 'not_observed')
                self.assertEqual(calls, [name])  # readback never retries createIndexes

    def test_readback_distinguishes_confirmed_missing_incompatible_and_unavailable(self):
        db = Database(); setup_storage_schema(db)
        index = next(i for i in INDEXES if i.name == 'files_id_1_n_1')
        error = StorageSchemaError('private', operation='create_index', index=index)
        writes = db.writes
        self.assertEqual(inspect_failed_index(db, error), 'confirmed_definition')
        db.rows[index.collection][index.name]['unique'] = False
        self.assertEqual(inspect_failed_index(db, error), 'incompatible_definition')
        del db.rows[index.collection][index.name]
        self.assertEqual(inspect_failed_index(db, error), 'not_observed')
        db.get_collection = Mock(side_effect=ConnectionFailure('private'))
        self.assertEqual(inspect_failed_index(db, error), 'unavailable')
        self.assertEqual(db.writes, writes)

    def test_invalid_readback_identity_performs_no_io(self):
        db = Mock()
        error = StorageSchemaError('private', operation='create_index', index='untrusted')
        self.assertEqual(inspect_failed_index(db, error), 'unavailable')
        db.get_collection.assert_not_called()

    def test_specific_failure_survives_oserror_inheritance(self):
        db = Database(); setup_storage_schema(db)
        db.rows['pdf_blobs.chunks']['files_id_1_n_1']['unique'] = False
        for function in (setup_storage_schema, verify_storage_schema):
            with self.subTest(function=function.__name__), self.assertRaises(StorageSchemaError) as caught:
                function(db)
            self.assertEqual(caught.exception.operation, 'compare_index')
            self.assertEqual(caught.exception.reason, 'index_uniqueness_mismatch')
            self.assertEqual(str(caught.exception), 'Incompatible optional storage index definition.')

    def test_provider_failure_is_classified_and_sanitized(self):
        for error, expected in (
                (OperationFailure('secret-uri-and-pdf', 13), 'permission_denied'),
                (OperationFailure('secret-uri-and-pdf', 85), 'index_options_conflict'),
                (ExecutionTimeout('secret-uri-and-pdf', 50), 'deadline_exceeded'),
                (ConnectionFailure('secret-uri-and-pdf'), 'database_operation_unconfirmed')):
            with self.subTest(reason=expected):
                db = Database()
                db.list_collection_names = Mock(side_effect=error)
                with self.assertRaises(StorageSchemaError) as caught:
                    setup_storage_schema(db)
                self.assertEqual(caught.exception.operation, 'list_collections')
                self.assertEqual(caught.exception.reason, expected)
                self.assertNotIn('secret', str(caught.exception))
                self.assertEqual(db.writes, 0)

    def test_create_collection_error_keeps_operation(self):
        db = Database()
        db.create_collection = Mock(side_effect=OperationFailure('private', 13))
        with self.assertRaises(StorageSchemaError) as caught:
            setup_storage_schema(db)
        self.assertEqual(caught.exception.operation, 'create_collection')
        self.assertEqual(caught.exception.reason, 'permission_denied')

    def test_create_index_error_keeps_operation(self):
        db = Database(); setup_storage_schema(db)
        del db.rows['pdf_blobs.chunks']['files_id_1_n_1']
        original = db.get_collection
        def get(name):
            collection = original(name)
            collection.create_index = Mock(side_effect=OperationFailure('private', 86))
            return collection
        db.get_collection = get
        with self.assertRaises(StorageSchemaError) as caught:
            setup_storage_schema(db)
        self.assertEqual(caught.exception.operation, 'create_index')
        self.assertEqual(caught.exception.reason, 'index_keys_conflict')

    def test_empty_verify_is_read_only(self):
        db = Database()
        with self.assertRaises(StorageSchemaError): verify_storage_schema(db)
        self.assertEqual(db.writes, 0)

    def test_setup_verify_and_repeat(self):
        db = Database()
        setup_storage_schema(db)
        writes = db.writes
        verify_storage_schema(db)
        setup_storage_schema(db)
        self.assertEqual(db.writes, writes)
        for index in INDEXES:
            self.assertIn(index.name, db.rows[index.collection])

    def test_incompatible_definitions_never_repaired(self):
        for change in ({'unique': False}, {'key': {'n': 1, 'files_id': 1}},
                       {'partialFilterExpression': {'n': 0}}, {'sparse': True},
                       {'hidden': True}, {'expireAfterSeconds': 30}):
            with self.subTest(change=change):
                db = Database(); setup_storage_schema(db)
                db.rows['pdf_blobs.chunks']['files_id_1_n_1'].update(change)
                before, writes = copy.deepcopy(db.rows), db.writes
                with self.assertRaises(StorageSchemaError): setup_storage_schema(db)
                self.assertEqual(db.rows, before)
                self.assertEqual(db.writes, writes)

    def test_missing_index_and_wrong_name(self):
        db = Database(); setup_storage_schema(db)
        indexes = db.rows['pdf_blobs.chunks']
        index = indexes.pop('files_id_1_n_1')
        with self.assertRaises(StorageSchemaError): verify_storage_schema(db)
        indexes['wrong'] = dict(index, name='wrong')
        with self.assertRaises(StorageSchemaError): setup_storage_schema(db)

    def test_constructor_performs_no_schema_io(self):
        from storage_intents import StorageIntents
        db = Mock()
        StorageIntents(db)
        db.list_collection_names.assert_not_called()
        db.create_collection.assert_not_called()
