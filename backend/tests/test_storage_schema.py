import copy
import unittest
from unittest.mock import Mock
from storage_schema import INDEXES, setup_storage_schema, verify_storage_schema, StorageSchemaError


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