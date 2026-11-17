"""Opt-in UTC-day single-document admission. No provider retries or refunds after dispatch."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.write_concern import WriteConcern
from config import ConfigurationError, load_usage_budget_settings

class BudgetError(Exception):
    def __init__(self, exhausted=False):
        self.status = 429 if exhausted else 503
        super().__init__('Daily application Gemini budget exhausted. Try again after UTC midnight.' if exhausted else 'Usage admission is temporarily unavailable.')

_SCOPE=ContextVar('gemini_budget_scope',default=None)

@contextmanager
def budget_scope(database, owner, loop=None, cancelled=None):
    token=_SCOPE.set((database,owner,loop,cancelled))
    try: yield
    finally: _SCOPE.reset(token)

async def setup_budget_indexes(database):
    settings=load_usage_budget_settings()
    if not settings.enabled: return
    async with asyncio.timeout(5):
        collection=database.get_collection('gemini_daily_usage')
        await collection.create_index('expires_at',name='gemini_usage_expiry',expireAfterSeconds=0)

class Ledger:
    def __init__(self,database,settings):
        self.collection=database.get_collection('gemini_daily_usage',write_concern=WriteConcern('majority',wtimeout=5000))
        self.settings=settings

    async def reserve(self,owner,kind,operation,now=None):
        if not isinstance(owner,ObjectId) or kind not in ('embedding','generation') or not isinstance(operation,str) or len(operation)!=32:
            raise BudgetError()
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None: raise BudgetError()
        day=now.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        key=day.strftime('%Y-%m-%d')+':'+kind
        global_limit=getattr(self.settings,'global_'+kind)
        user_limit=getattr(self.settings,'user_'+kind)
        policy={'global':global_limit,'user':user_limit}
        try:
            async with asyncio.timeout(5):
                indexes=await self.collection.index_information()
                expiry=indexes.get('gemini_usage_expiry',{})
                if expiry.get('key') != [('expires_at',1)] or expiry.get('expireAfterSeconds') != 0:
                    raise BudgetError()
                # _id is the intrinsic unique key. First-writer races fail closed.
                await self.collection.update_one({'_id':key},{'$setOnInsert':{'policy':policy,'entries':[],
                    'expires_at':day+timedelta(days=32)}},upsert=True)
                row=await self.collection.find_one_and_update({'_id':key,'policy':policy,'entries.operation':{'$ne':operation},
                    '$expr':{'$and':[
                        {'$lt':[{'$size':'$entries'},global_limit]},
                        {'$lt':[{'$size':{'$filter':{'input':'$entries','as':'e','cond':{'$eq':['$$e.owner',str(owner)]}}}},user_limit]}]}},
                    {'$push':{'entries':{'operation':operation,'owner':str(owner),'state':'reserved'}}},
                    return_document=ReturnDocument.AFTER)
                if row is None:
                    current=await self.collection.find_one({'_id':key})
                    if not current or current.get('policy')!=policy: raise BudgetError()
                    if any(e['operation']==operation for e in current['entries']): raise BudgetError()
                    raise BudgetError(True)
                return key,operation,str(owner)
        except BudgetError: raise
        except Exception: raise BudgetError() from None

    async def release_before_dispatch(self,ticket):
        key,operation,owner=ticket
        try:
            async with asyncio.timeout(5):
                await self.collection.update_one({'_id':key},{'$pull':{'entries':{'operation':operation,'owner':owner,'state':'reserved'}}})
        except Exception: raise BudgetError() from None

    async def dispatch(self,ticket):
        key,operation,owner=ticket
        # A queued call cannot spend yesterday's allowance after UTC rollover.
        if not key.startswith(datetime.now(timezone.utc).strftime('%Y-%m-%d')+':'):
            raise BudgetError()
        try:
            async with asyncio.timeout(5):
                result=await self.collection.update_one({'_id':key,'entries':{'$elemMatch':{'operation':operation,'owner':owner,'state':'reserved'}}},
                    {'$set':{'entries.$.state':'dispatched'}})
                if result.modified_count!=1: raise BudgetError()
        except BudgetError: raise
        except Exception: raise BudgetError() from None

async def admit(kind,scope=None):
    try: settings=load_usage_budget_settings()
    except ConfigurationError: raise BudgetError() from None
    if not settings.enabled: return
    scope=scope or _SCOPE.get()
    if scope is None: raise BudgetError()
    database,owner,_,cancelled=scope
    if cancelled is not None and cancelled.is_set(): raise BudgetError()
    ledger=Ledger(database,settings)
    ticket=await ledger.reserve(owner,kind,uuid4().hex)
    if cancelled is not None and cancelled.is_set():
        await ledger.release_before_dispatch(ticket)
        raise BudgetError()
    # After this CAS, no refund: cancellation/timeout can race provider dispatch.
    await ledger.dispatch(ticket)


def admit_sync(kind):
    try: settings=load_usage_budget_settings()
    except ConfigurationError: raise BudgetError() from None
    if not settings.enabled: return
    scope=_SCOPE.get()
    if scope is None or scope[2] is None: raise BudgetError()
    future=asyncio.run_coroutine_threadsafe(admit(kind,scope),scope[2])
    try: future.result(timeout=16)
    except BudgetError: raise
    except Exception:
        # Do not cancel/retry/refund: database acknowledgement may be lost.
        raise BudgetError() from None
