"""Real async ledger checks; synthetic identities, no provider SDK or environment loading."""
import asyncio
from collections import Counter
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from unittest.mock import patch

from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import AutoReconnect
from pymongo.write_concern import WriteConcern
import usage_budgets as budgets
from config import UsageBudgetSettings


class LostAcknowledgement:
    """Complete one real write, then hide its acknowledgement from the ledger."""
    def __init__(self, collection, method):
        self.collection, self.method, self.injected = collection, method, False

    def __getattr__(self, name):
        original = getattr(self.collection, name)
        if name != self.method:
            return original
        async def call(*args, **kwargs):
            result = await original(*args, **kwargs)
            if not self.injected:
                self.injected = True
                raise AutoReconnect('Synthetic acknowledgement loss')
            return result
        return call


async def rejected(awaitable, status):
    try:
        await awaitable
    except budgets.BudgetError as error:
        assert error.status == status
    else:
        raise AssertionError('Expected budget rejection')


async def validate(databases):
    settings = UsageBudgetSettings(True, 3, 2, 3, 2)
    ledgers = [budgets.Ledger(db, settings) for db in databases]
    collection = ledgers[0].collection
    assert await collection.count_documents({}) == 0, 'Expected fresh disposable ledger'
    with patch.object(budgets, 'load_usage_budget_settings', return_value=settings):
        await budgets.setup_budget_indexes(databases[0])
        await budgets.setup_budget_indexes(databases[1])
    indexes = await collection.index_information()
    assert indexes['_id_']['key'] == [('_id', 1)]
    assert indexes['gemini_usage_expiry']['key'] == [('expires_at', 1)]
    assert indexes['gemini_usage_expiry']['expireAfterSeconds'] == 0
    assert not indexes['gemini_usage_expiry'].get('partialFilterExpression')
    print('PASS: budget identity and TTL index definitions; idempotent setup.', flush=True)
    now = datetime.now(timezone.utc)
    owner, other = ObjectId(), ObjectId()
    # Future synthetic UTC days avoid asynchronous TTL removal of test records.
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for kind in ('embedding', 'generation'):
        first = await ledgers[0].reserve(owner, kind, uuid4().hex, day)
        await ledgers[1].reserve(owner, kind, uuid4().hex, day)
        await rejected(ledgers[1].reserve(owner, kind, uuid4().hex, day), 429)
        await ledgers[0].reserve(other, kind, uuid4().hex, day)
        await rejected(ledgers[1].reserve(ObjectId(), kind, uuid4().hex, day), 429)
        await rejected(ledgers[1].reserve(owner, kind, first[1], day), 503)
        row = await collection.find_one({'_id': first[0]})
        assert len(row['entries']) == 3
        assert row['expires_at'].replace(tzinfo=timezone.utc) == day + timedelta(days=32)
        before = day + timedelta(days=1) - timedelta(microseconds=1)
        after = before + timedelta(microseconds=1)
        await rejected(ledgers[0].reserve(owner, kind, uuid4().hex, before), 429)
        rollover = await ledgers[1].reserve(owner, kind, first[1], after)
        assert rollover[0] != first[0]
    print('PASS: both request types; user/global exhaustion, duplicate IDs and UTC rollover.', flush=True)

    # Separate network clients, synchronized admission; cold ledger initialization.
    for offset, owners in ((3, [owner] * 8), (4, [owner] * 4 + [other] * 4)):
        start = asyncio.Event()
        async def contender(i, who):
            await start.wait()
            try:
                return await ledgers[i % 2].reserve(who, 'embedding', uuid4().hex, day + timedelta(days=offset))
            except budgets.BudgetError as error:
                assert error.status in (429, 503)  # cold-upsert conflict may fail closed
                return None
        tasks = [asyncio.create_task(contender(i, who)) for i, who in enumerate(owners)]
        start.set()
        results = await asyncio.gather(*tasks)
        admitted = [ticket for ticket in results if ticket is not None]
        assert admitted and len(admitted) <= 3
        assert max(Counter(ticket[2] for ticket in admitted).values()) <= 2
        row = await collection.find_one({'_id': admitted[0][0]})
        assert len(row['entries']) == len(admitted)
        assert len({entry['operation'] for entry in row['entries']}) == len(admitted)
    print('PASS: separate-client concurrent global and per-user admission bounded.', flush=True)

    ticket = await ledgers[0].reserve(owner, 'embedding', uuid4().hex)
    await ledgers[1].release_before_dispatch(ticket)
    await ledgers[1].release_before_dispatch(ticket)
    assert (await collection.find_one({'_id': ticket[0]}))['entries'] == []
    ticket = await ledgers[0].reserve(owner, 'embedding', uuid4().hex)
    await ledgers[0].dispatch(ticket)
    # Provider completion is unknown. Neither cancellation nor release may refund it.
    await ledgers[1].release_before_dispatch(ticket)
    await rejected(ledgers[1].dispatch(ticket), 503)
    assert (await collection.find_one({'_id': ticket[0]}))['entries'][0]['state'] == 'dispatched'
    print('PASS: confirmed pre-dispatch release; uncertain dispatched consumption retained.', flush=True)

    # Exercise actual admit() ordering; the following counter is a fake provider.
    dispatches = 0
    for method in ('find_one_and_update', 'update_one'):
        lost = budgets.Ledger(databases[0], settings)
        wrapper = LostAcknowledgement(lost.collection, method)
        lost.collection = wrapper
        operation = uuid4()
        if method == 'update_one':
            # Reserve normally first; inject loss only at the dispatch CAS.
            original_reserve = lost.reserve
            async def reserve_then_arm(*args, **kwargs):
                lost.collection = wrapper.collection
                try:
                    return await original_reserve(*args, **kwargs)
                finally:
                    lost.collection = wrapper
            lost.reserve = reserve_then_arm
        async def attempt():
            nonlocal dispatches
            await budgets.admit('generation', (databases[0], owner, None, None))
            dispatches += 1
        with patch.object(budgets, 'load_usage_budget_settings', return_value=settings), \
             patch.object(budgets, 'uuid4', return_value=operation), \
             patch.object(budgets, 'Ledger', return_value=lost):
            await rejected(attempt(), 503)
            assert wrapper.injected
            await rejected(attempt(), 503)
        row = await collection.find_one({'_id': now.strftime('%Y-%m-%d') + ':generation'})
        matches = [e for e in row['entries'] if e['operation'] == operation.hex]
        assert len(matches) == 1
        assert matches[0]['state'] == ('reserved' if method == 'find_one_and_update' else 'dispatched')
    assert dispatches == 0
    print('PASS: simulated lost reservation/dispatch ACK; no duplicate simulated provider dispatch.', flush=True)


async def run(config, guard):
    guard(config)
    clients = []
    try:
        databases = []
        for _ in range(2):
            client = AsyncMongoClient('localhost', 27017, directConnection=True,
                replicaSet=config['replica'], username='smoke', password=config['password'],
                authSource=config['database'], tls=True, tlsCAFile='/run/ca.crt',
                tlsAllowInvalidCertificates=False, tlsAllowInvalidHostnames=False,
                retryWrites=False, retryReads=False, timeoutMS=5000,
                serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                socketTimeoutMS=5000, waitQueueTimeoutMS=5000, maxPoolSize=4)
            clients.append(client)
            guard(config, await client.admin.command('hello'))
            status = await client.admin.command('connectionStatus', showPrivileges=False)
            assert status['authInfo']['authenticatedUserRoles'] == [{'role': 'readWrite', 'db': config['database']}]
            databases.append(client.get_database(config['database'], write_concern=WriteConcern('majority', wtimeout=5000)))
        async with asyncio.timeout(60):
            await validate(databases)
    finally:
        for client in clients:
            await client.close()
