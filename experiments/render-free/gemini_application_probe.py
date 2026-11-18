"""Gemini-mode full lifespan/routes with real policies and simulated service boundaries."""
import asyncio
import contextlib
import importlib.abc
import json
import os
import secrets
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

STAGES = {'application_import_idle', 'fixture_setup', 'application_startup', 'startup_idle',
          'login', 'concurrent_passwords', 'upload', 'pdf_extraction_and_chunking', 'indexing',
          'search', 'ask', 'concurrent_login_pdf', 'repeat_1', 'repeat_2', 'repeat_3',
          'idle_1', 'idle_2', 'idle_3', 'application_shutdown'}

class NoLocalML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'sentence_transformers', 'transformers'}:
            raise AssertionError('Local ML import forbidden in Gemini probe')


def environment():
    return dict(EMBEDDING_PROVIDER='gemini', GEMINI_EMBEDDING_MODEL='gemini-embedding-001',
        EMBEDDING_DIMENSION='768', PRIVATE_ACCESS_ENABLED='true', GEMINI_USAGE_BUDGETS_ENABLED='true',
        GROWTH_LIMITS_ENABLED='true', GRIDFS_UPLOAD_ENABLED='true', RAG_VERIFIED_EXPERIMENT_ENABLED='false',
        MONGODB_URI='mongodb://127.0.0.1:1', MONGODB_DB_NAME='synthetic_resource',
        QDRANT_URL='http://127.0.0.1:1', QDRANT_COLLECTION_NAME='synthetic_gemini_768',
        GEMINI_API_KEY='offline-placeholder-never-dispatched', JWT_SECRET_KEY=secrets.token_urlsafe(48),
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')


async def workload(stage, emit, *, server_factory=None, transport=None, idle_seconds=2):
    import config
    with contextlib.ExitStack() as patches:
        patches.enter_context(patch('config.dotenv_values', return_value={}))
        patches.enter_context(patch.dict(os.environ, environment()))
        settings = config.load_embedding_settings()
        assert settings.provider == 'gemini' and settings.dimension == 768
        assert config.load_private_access_enabled() and config.load_usage_budget_settings().enabled
        assert config.load_growth_settings().enabled and config.load_gridfs_upload_enabled()
        with stage('application_import_idle'):
            from main import app
            import uvicorn
            import httpx
            import pymupdf
            from gemini_fixture import Database
            from qdrant_helpers import FakeQdrant
            from vector_store import VectorStore
            from config import load_qdrant_settings
            from storage_workers import optional_storage_lifespan
            from gemini_provider import GeminiProvider
            from config import load_gemini_settings
            from test_gemini_embeddings import response
            from test_rag import response as answer_response
            from security import hash_password, verify_password
            from bson import ObjectId
            from datetime import datetime, timezone
        assert not app.dependency_overrides, 'Authentication/dependency overrides forbidden'
        with stage('fixture_setup'):
            db = Database()
            owner = ObjectId()
            password = secrets.token_urlsafe(24)
            hashed = await hash_password(password)
            db.sync.rows['users'] = {owner: {'_id': owner, 'name': 'Synthetic', 'email': 'demo@example.com',
                'password_hash': hashed, 'approved': True, 'enabled': True, 'created_at': datetime.now(timezone.utc)}}
            async_client = SimpleNamespace(get_database=lambda *a, **k: db, close=AsyncMock())
            sync_client = SimpleNamespace(get_database=lambda *a, **k: db.sync, close=lambda: None)
            sdk = Mock()
            sdk.__enter__ = Mock(return_value=sdk); sdk.__exit__ = Mock(return_value=False)
            sdk.models.embed_content.side_effect = lambda **kw: response(kw['contents'])
            sdk.aio.models.generate_content = AsyncMock(return_value=answer_response(json.dumps({
                'supported': True, 'claims': [{'text': 'Alex Morgan coordinates Aurora.', 'citation_ids': [1]}]})))
            patches.enter_context(patch('database.AsyncMongoClient', return_value=async_client))
            @contextlib.asynccontextmanager
            async def storage_lifespan(**kwargs):
                async with optional_storage_lifespan(**kwargs, client_factory=lambda *a, **k: sync_client) as workers:
                    yield workers
            patches.enter_context(patch('gridfs_uploads.optional_storage_lifespan', storage_lifespan))
            patches.enter_context(patch('gridfs_storage.GridFSBucket', return_value=db.bucket))
            patches.enter_context(patch('google.genai.Client', return_value=sdk))
            patches.enter_context(patch('vector_store._store', VectorStore(load_qdrant_settings(), FakeQdrant())))
            password_active = password_peak = 0
            async def observed_verify(*args, **kwargs):
                nonlocal password_active, password_peak
                password_active += 1
                password_peak = max(password_peak, password_active)
                try: return await verify_password(*args, **kwargs)
                finally: password_active -= 1
            patches.enter_context(patch('auth_routes.verify_password', observed_verify))
            provider = GeminiProvider(load_gemini_settings())
            patches.enter_context(patch('rag.get_gemini_provider', return_value=provider))
            server = (server_factory or uvicorn.Server)(uvicorn.Config(app, host='127.0.0.1', port=8000,
                workers=1, proxy_headers=False, access_log=False, log_level='error', lifespan='on'))
            with pymupdf.open() as pdf:
                for _ in range(3):
                    pdf.new_page().insert_text((40, 40), 'Alex Morgan coordinates Aurora. Synthetic resource test.')
                pdf_bytes = pdf.tobytes()
            assert len(pdf_bytes) < 32768
        task = None
        try:
            with stage('application_startup'):
                task = asyncio.create_task(server.serve())
                async with asyncio.timeout(60):
                    while not server.started:
                        if task.done(): await task; raise AssertionError('Startup failed')
                        await asyncio.sleep(.02)
            async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', trust_env=False,
                    timeout=60, transport=transport) as client:
                async def req(method, path, expected=200, **kw):
                    async with asyncio.timeout(60):
                        result = await client.request(method, path, **kw)
                    emit('request', route=path.split('/')[2], status=result.status_code)
                    assert result.status_code == expected, f'Request status {result.status_code}'
                    return result.json() if result.content else None
                with stage('startup_idle'):
                    from policy_readiness import verify
                    assert await verify(db)
                    assert app.state.readiness.configuration_valid, 'Fixture configuration rejected'
                    await req('GET', '/api/health'); await req('GET', '/api/ready')
                    await asyncio.sleep(idle_seconds)
                async def login():
                    return await req('POST', '/api/auth/login', json={'email': 'demo@example.com', 'password': password})
                with stage('login'):
                    token = (await login())['access_token']
                    client.headers['Authorization'] = 'Bearer ' + token
                    await req('GET', '/api/auth/me')
                with stage('concurrent_passwords'):
                    await asyncio.gather(login(), login())
                    assert password_peak == 2
                base = (await req('POST', '/api/knowledge-bases', expected=201, json={'name': 'Synthetic Aurora'}))['id']
                with stage('upload'):
                    doc = await req('POST', f'/api/knowledge-bases/{base}/documents', expected=201,
                        files={'file': ('synthetic.pdf', pdf_bytes, 'application/pdf')})
                async def process(): return await req('POST', f'/api/documents/{doc["id"]}/process')
                with stage('pdf_extraction_and_chunking'): await process()
                async def index():
                    value = await req('POST', f'/api/documents/{doc["id"]}/embeddings')
                    assert value['embedding_dimension'] == 768
                with stage('indexing'): await index()
                with stage('search'):
                    result = await req('POST', f'/api/knowledge-bases/{base}/search', json={'query': 'Who coordinates Aurora?'})
                    assert result['results']
                async def ask():
                    answer = await req('POST', f'/api/knowledge-bases/{base}/ask', json={'question': 'Who coordinates Aurora?'})
                    assert answer['status'] == 'answered' and answer['sources']
                with stage('ask'): await ask()
                with stage('concurrent_login_pdf'):
                    await asyncio.gather(login(), process())
                await index()
                for n in range(1, 4):
                    with stage(f'repeat_{n}'): await ask(); await ask()
                    with stage(f'idle_{n}'): await asyncio.sleep(idle_seconds)
                assert len(db.sync.rows['ask_history']) == 7
                assert len(db.sync.rows['resource_usage'][owner]['documents']) == 1
                assert len(db.sync.rows['storage_quota_ledger']['pdf_payload_v1']['entries']) == 1
                calls = [e for row in db.budget.rows.values() for e in row['entries']]
                assert all(e['state'] == 'dispatched' for e in calls)
                assert len(calls) == sdk.models.embed_content.call_count + sdk.aio.models.generate_content.await_count
                assert not any(n in sys.modules for n in ('torch', 'sentence_transformers', 'transformers'))
                emit('safeguards', private_access=True, budgets=True, growth=True, gridfs=True,
                    embedding_dimension=768, no_local_ml=True, accounted_calls=len(calls), password_peak=password_peak)
            with stage('application_shutdown'):
                server.should_exit = True
                await asyncio.wait_for(task, 30)
            emit('application_complete', runtime='gemini', simulated=['MongoDB collections/transactions',
                'GridFS bucket', 'Qdrant', 'Gemini SDK'], real=['lifespan', 'HTTP routes', 'JWT',
                'Argon2', 'PDF child', 'storage workers', 'policies', 'RAG', 'history'])
        finally:
            if task is not None and not task.done():
                server.should_exit = True
                await asyncio.wait_for(task, 30)


def main():
    from probe import stage, emit
    guard = NoLocalML()
    sys.meta_path.insert(0, guard)
    try:
        asyncio.run(workload(stage, emit))
        emit('complete', runtime='gemini')
    except Exception as error:
        emit('failed', category=type(error).__name__)
        raise SystemExit(1) from None
    finally: sys.meta_path.remove(guard)

if __name__ == '__main__': main()
