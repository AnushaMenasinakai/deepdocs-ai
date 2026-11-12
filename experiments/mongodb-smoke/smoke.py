"""No URI/env loading: credentials and identities come only from the job fixture."""
import json
import re
from pathlib import Path
from pymongo import MongoClient
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from storage_schema import (setup_storage_schema, verify_storage_schema, COLLECTIONS,
                            StorageSchemaError, INDEXES, DRIVER_CATEGORIES, inspect_failed_index)


def failure_diagnostic(error):
    # Whitelist even internal attributes: never serialize arbitrary exception text.
    operations = {'inspection', 'list_collections', 'list_indexes', 'create_collection',
                  'create_index', 'compare_index', 'inspect_manifest', 'verify_schema', 'setup_schema'}
    reasons = {'schema_unconfirmed', 'permission_denied', 'namespace_exists',
               'index_options_conflict', 'index_keys_conflict', 'duplicate_key',
               'deadline_exceeded', 'write_concern_unconfirmed', 'database_operation_unconfirmed',
               'index_keys_mismatch', 'index_uniqueness_mismatch', 'index_options_mismatch',
               'index_name_conflict', 'indexes_missing', 'identity_index_missing',
               'bad_value', 'parse_rejected', 'cannot_create_index', 'invalid_options',
               'invalid_index_specification', 'out_of_disk_space'}
    if isinstance(error, StorageSchemaError):
        operation = error.operation if error.operation in operations else 'unknown'
        reason = error.reason if error.reason in reasons else 'unknown'
        message = f'FAIL: schema validation; operation={operation}; reason={reason}'
        index = next((i for i in INDEXES if i == error.index), None)
        if index is not None:
            category = error.driver_category if error.driver_category in DRIVER_CATEGORIES else 'unknown'
            code = error.server_code if type(error.server_code) is int and 0 <= error.server_code <= 2147483647 else 'unknown'
            outcome = error.outcome if error.outcome in {'unknown', 'command_rejected', 'incompatible_definition'} else 'unknown'
            message += (f'; collection={index.collection}; index={index.name}; driver={category}'
                        f'; server_code={code}; command_outcome={outcome}')
        return message
    return 'FAIL: smoke validation; category=' + type(error).__name__


def guard(config, hello=None):
    if (set(config) != {'database', 'replica', 'password'}
            or not isinstance(config['database'], str)
            or not re.fullmatch(r'dd_smoke_[0-9]+_[0-9]+', config['database'])
            or len(config['database']) > 55 or config['replica'] != config['database']
            or not isinstance(config['password'], str) or len(config['password']) < 32):
        raise ValueError('Isolation identity rejected.')
    if hello is not None and (hello.get('setName') != config['replica']
            or hello.get('hosts') != ['localhost:27017']
            or not hello.get('isWritablePrimary')
            or hello.get('passives') or hello.get('arbiters')):
        raise ValueError('Replica identity rejected.')


def setup_with_diagnostics(database):
    try:
        setup_storage_schema(database)
    except StorageSchemaError as error:
        if error.operation == 'create_index':
            # setup has exited its deadline. Readback gets a fresh bounded budget.
            # Keep the failed job even if the desired definition is now visible.
            state = inspect_failed_index(database, error)
            print('Index readback: ' + state, flush=True)
        raise


def run():
    print('Checking fixture and authenticated replica identity.', flush=True)
    config = json.loads(Path('/run/app.json').read_text())
    guard(config)
    # Direct mode prevents server discovery from connecting to advertised hosts.
    with MongoClient('localhost', 27017, directConnection=True,
                     replicaSet=config['replica'], username='smoke', password=config['password'],
                     authSource=config['database'], tls=True, tlsCAFile='/run/ca.crt',
                     tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
                     retryWrites=False, retryReads=False, timeoutMS=5000,
                     serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                     socketTimeoutMS=5000, waitQueueTimeoutMS=5000) as client:
        guard(config, client.admin.command('hello'))
        status = client.admin.command('connectionStatus', showPrivileges=False)['authInfo']
        if status['authenticatedUserRoles'] != [{'role': 'readWrite', 'db': config['database']}]:
            raise ValueError('Unexpected application privileges.')
        database = client.get_database(config['database'], write_concern=WriteConcern('majority', wtimeout=5000))
        assert database.command('ping')['ok'] == 1
        assert client.admin.command('buildInfo')['version'] == '8.0.38'
        print('Identity verified; checking explicit schema setup.', flush=True)
        setup_with_diagnostics(database)
        print('Initial schema setup completed; verifying manifest.', flush=True)
        verify_storage_schema(database)
        before = {c: list(database[c].list_indexes()) for c in COLLECTIONS}
        print('Initial manifest verified; checking idempotent setup.', flush=True)
        setup_with_diagnostics(database)
        verify_storage_schema(database)
        assert before == {c: list(database[c].list_indexes()) for c in COLLECTIONS}
        print('Schema verified; checking majority write and transactions.', flush=True)
        collection = database['smoke_records']
        assert collection.insert_one({'_id': 'majority'}).acknowledged
        with client.start_session() as session:
            with session.start_transaction(read_concern=ReadConcern('snapshot'),
                                           write_concern=WriteConcern('majority', wtimeout=5000),
                                           max_commit_time_ms=5000):
                collection.insert_one({'_id': 'commit'}, session=session)
            session.start_transaction(write_concern=WriteConcern('majority', wtimeout=5000))
            collection.insert_one({'_id': 'rollback'}, session=session)
            session.abort_transaction()
        assert collection.find_one({'_id': 'commit'}) is not None
        assert collection.find_one({'_id': 'rollback'}) is None
        print('PASS: TLS/auth, replica identity, majority ACK, commit/rollback, schema and idempotency.')


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        # No raw exceptions, payloads, URIs or server responses in job logs.
        print(failure_diagnostic(error))
        raise SystemExit(1) from None
