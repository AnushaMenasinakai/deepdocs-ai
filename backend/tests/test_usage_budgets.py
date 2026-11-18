"""Atomic admission double; does not establish real MongoDB serialization compatibility."""
import asyncio, copy, unittest
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import patch,AsyncMock
from uuid import uuid4
from bson import ObjectId
from config import UsageBudgetSettings
from usage_budgets import Ledger,BudgetError,admit,budget_scope

class Collection:
    def __init__(self): self.rows={}; self.lock=asyncio.Lock(); self.failure=False; self.lost=False
    async def index_information(self):
        if self.failure: raise OSError('PRIVATE')
        return {'gemini_usage_expiry':{'key':[('expires_at',1)],'expireAfterSeconds':0}}
    async def update_one(self,q,u,**kw):
        async with self.lock:
            key=q['_id']
            if '$setOnInsert' in u:
                self.rows.setdefault(key,copy.deepcopy(u['$setOnInsert'])); return
            row=self.rows[key]
            modified=0
            if '$pull' in u:
                match=u['$pull']['entries']; old=len(row['entries'])
                row['entries']=[e for e in row['entries'] if not all(e[k]==v for k,v in match.items())]
                modified=old-len(row['entries'])
            else:
                match=q['entries']['$elemMatch']
                for e in row['entries']:
                    if all(e[k]==v for k,v in match.items()): e['state']='dispatched'; modified=1; break
            return SimpleNamespace(modified_count=modified)
    async def find_one_and_update(self,q,u,**kw):
        async with self.lock:
            row=self.rows[q['_id']]; entry=u['$push']['entries']; policy=q['policy']
            if row['policy']!=policy or any(e['operation']==entry['operation'] for e in row['entries']): return None
            if len(row['entries'])>=policy['global'] or sum(e['owner']==entry['owner'] for e in row['entries'])>=policy['user']: return None
            row['entries'].append(copy.deepcopy(entry))
            if self.lost: raise OSError('lost acknowledgement')
            return copy.deepcopy(row)
    async def find_one(self,q): return copy.deepcopy(self.rows.get(q['_id']))

class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.c=Collection(); self.db=SimpleNamespace(get_collection=lambda *a,**kw:self.c)
        self.s=UsageBudgetSettings(True,3,2,2,1); self.ledger=Ledger(self.db,self.s); self.owner=ObjectId()
    async def reserve(self,owner=None,kind='embedding',op=None,now=None):
        return await self.ledger.reserve(owner or self.owner,kind,op or uuid4().hex,now)
    async def test_user_global_limits_and_restart(self):
        await self.reserve(); await self.reserve()
        with self.assertRaises(BudgetError) as e: await self.reserve()
        self.assertEqual(e.exception.status,429)
        await self.reserve(ObjectId())
        self.ledger=Ledger(self.db,self.s)
        with self.assertRaises(BudgetError): await self.reserve(ObjectId())
    async def test_concurrency_never_over_admits(self):
        async def attempt():
            try: await self.reserve(ObjectId()); return True
            except BudgetError: return False
        self.assertEqual(sum(await asyncio.gather(*(attempt() for _ in range(12)))),3)
    async def test_duplicate_and_day_rollover(self):
        now=datetime(2026,11,17,23,59,tzinfo=timezone.utc); op=uuid4().hex
        await self.reserve(op=op,now=now)
        with self.assertRaises(BudgetError): await self.reserve(op=op,now=now)
        await self.reserve(now=now+timedelta(minutes=2))
        self.assertEqual(len(self.c.rows),2)
    async def test_release_only_reserved_and_duplicate_dispatch(self):
        ticket=await self.reserve(); await self.ledger.release_before_dispatch(ticket)
        self.assertEqual(len(self.c.rows[ticket[0]]['entries']),0)
        ticket=await self.reserve(); await self.ledger.dispatch(ticket)
        await self.ledger.release_before_dispatch(ticket)
        self.assertEqual(len(self.c.rows[ticket[0]]['entries']),1)
        with self.assertRaises(BudgetError): await self.ledger.dispatch(ticket)
    async def test_unknown_ack_retained_and_unavailability_sanitized(self):
        self.c.lost=True
        with self.assertRaises(BudgetError): await self.reserve()
        self.assertEqual(sum(len(r['entries']) for r in self.c.rows.values()),1)
        self.c.failure=True
        with self.assertRaises(BudgetError) as e: await self.reserve()
        self.assertNotIn('PRIVATE',str(e.exception))
    async def test_missing_scope_disabled_and_cancel_before_dispatch(self):
        import threading
        with patch('usage_budgets.load_usage_budget_settings',return_value=self.s):
            with self.assertRaises(BudgetError): await admit('generation')
            event=threading.Event(); event.set()
            with budget_scope(self.db,self.owner,cancelled=event):
                with self.assertRaises(BudgetError): await admit('embedding')
            self.assertFalse(self.c.rows)
        with patch('usage_budgets.load_usage_budget_settings',return_value=UsageBudgetSettings()):
            await admit('embedding')
    async def test_generation_failure_consumes_and_denial_never_calls_provider(self):
        import test_rag as h
        from gemini_provider import GeminiProvider,GeminiFailure
        from config import GeminiSettings,RAGSettings
        from rag_context import build_context
        from rag_citations import citation_context
        call=AsyncMock(side_effect=TimeoutError('PRIVATE'))
        with patch('google.genai.Client',return_value=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=call)))):
            provider=GeminiProvider(GeminiSettings('offline'))
        context=citation_context(build_context([h.hit()],RAGSettings()))
        with patch('usage_budgets.load_usage_budget_settings',return_value=self.s), budget_scope(self.db,self.owner):
            with self.assertRaises(GeminiFailure): await provider.answer('question',context)
            with self.assertRaises(BudgetError) as e: await provider.answer('question',context)
            self.assertEqual(e.exception.status,429)
        self.assertEqual(call.await_count,1)
        self.assertEqual(next(iter(self.c.rows.values()))['entries'][0]['state'],'dispatched')

    async def test_embedding_failure_consumes_exactly_one_attempt(self):
        from config import EmbeddingSettings,GeminiSettings
        from gemini_embeddings import embed_remote
        from embeddings import EmbeddingFailure
        from unittest.mock import Mock
        client=Mock(); client.__enter__=Mock(return_value=client); client.__exit__=Mock(return_value=False)
        client.models.embed_content.side_effect=TimeoutError('PRIVATE')
        loop=asyncio.get_running_loop()
        def work():
            with budget_scope(self.db,self.owner,loop):
                return embed_remote(['Synthetic text'],EmbeddingSettings(provider='gemini'),'RETRIEVAL_DOCUMENT')
        with patch('usage_budgets.load_usage_budget_settings',return_value=self.s), \
             patch('gemini_embeddings.load_gemini_settings',return_value=GeminiSettings('offline')), \
             patch('google.genai.Client',return_value=client):
            with self.assertRaises(EmbeddingFailure): await asyncio.to_thread(work)
        self.assertEqual(client.models.embed_content.call_count,1)
        self.assertEqual(next(iter(self.c.rows.values()))['entries'][0]['state'],'dispatched')

    async def test_cancellation_after_reservation_releases_without_dispatch(self):
        import threading
        event=threading.Event(); original=self.ledger.reserve
        async def reserved(*args,**kwargs):
            ticket=await original(*args,**kwargs); event.set(); return ticket
        with patch('usage_budgets.load_usage_budget_settings',return_value=self.s), \
             patch('usage_budgets.Ledger',return_value=self.ledger), patch.object(self.ledger,'reserve',side_effect=reserved), \
             budget_scope(self.db,self.owner,cancelled=event):
            with self.assertRaises(BudgetError): await admit('embedding')
        self.assertEqual(sum(len(r['entries']) for r in self.c.rows.values()),0)

    async def test_real_index_and_search_routes_use_budgets_and_return_429(self):
        from test_gemini_rag_integration import IntegrationTests
        fixture=IntegrationTests()
        await fixture.asyncSetUp()
        try:
            body=await fixture.document()
            settings=UsageBudgetSettings(True,1,1,1,1)
            ledger=Ledger(self.db,settings)
            with patch('usage_budgets.load_usage_budget_settings',return_value=settings), patch('usage_budgets.Ledger',return_value=ledger):
                self.assertEqual((await fixture.fixture.embed(body))[0],200)
                status,result,_=await fixture.fixture.ask({'question':'Who coordinates Aurora?'})
                self.assertEqual(status,429)
                self.assertIn('Daily application',result['detail'])
                self.assertEqual(fixture.client.models.embed_content.call_count,1)
                fixture.client.aio.models.generate_content.assert_not_awaited()
        finally:
            await fixture.asyncTearDown()
            fixture.doCleanups()

    async def test_configuration_defaults_and_invalid_limits(self):
        import os
        from config import load_usage_budget_settings,ConfigurationError
        with patch('config.dotenv_values',return_value={}),patch.dict(os.environ,{},clear=True):
            self.assertEqual(load_usage_budget_settings(),UsageBudgetSettings())
        for values in ({'GEMINI_USAGE_BUDGETS_ENABLED':'maybe'}, {'GEMINI_DAILY_GLOBAL_EMBEDDING':'0'},
                       {'GEMINI_DAILY_USER_EMBEDDING':'101'}, {'GEMINI_DAILY_GLOBAL_GENERATION':'5001'}):
            with patch('config.dotenv_values',return_value=values),patch.dict(os.environ,{},clear=True):
                with self.assertRaises(ConfigurationError): load_usage_budget_settings()
