"""Actual single-process server/lifespan; remote services are explicit test doubles."""
import asyncio
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


async def run_application(runtime, stage, emit, model_factory, facts, questions):
    import uvicorn
    import embeddings
    from bson import ObjectId
    from config import Settings, ProcessingSettings, EmbeddingSettings
    from main import app
    from document_routes import processing_settings, embedding_settings
    from test_rag import AskTests
    from test_knowledge_bases import request
    from citation_helpers import claims_for
    from gridfs_storage import GridFSPDFStorage
    from gridfs_processing import GridFSProcessing
    from storage_workers import StorageWorkers
    from test_gridfs_storage import Bucket

    fixture = AskTests()
    await fixture.asyncSetUp()
    # Discard the fixture's idle lane: requests must use the lifespan-owned lane.
    await fixture.admission.aclose()
    await fixture.workers.aclose()
    startup_db = SimpleNamespace(command=AsyncMock(return_value={'ok': 1}),
        get_collection=lambda name: SimpleNamespace(create_index=AsyncMock()))
    client = SimpleNamespace(get_database=lambda name: startup_db, close=AsyncMock())
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8000,
        workers=1, proxy_headers=False, access_log=False, log_level='error', lifespan='on'))
    task = None
    storage_workers = None
    remote = None
    try:
        with (patch('database.load_settings', return_value=Settings('mongodb://127.0.0.1:1', 'synthetic')),
              patch('database.AsyncMongoClient', return_value=client)):
            with stage('application_startup'):
                task = asyncio.create_task(server.serve())
                async with asyncio.timeout(60):
                    while not server.started:
                        if task.done():
                            await task
                            raise RuntimeError('Server exited before startup')
                        await asyncio.sleep(.02)
                fixture.workers = app.state.cpu_workers
                fixture.admission = app.state.expensive_admission
                assert app.state.gridfs_uploads is None
                assert (await request('GET', '/api/health'))[0] == 200
            with stage('startup_idle'):
                await asyncio.sleep(2)
            with stage('model_initialization'):
                model = await asyncio.to_thread(model_factory)
            with patch('embeddings.load_model', return_value=model):
                with stage('inference'):
                    vectors = await asyncio.to_thread(lambda: [v for _, batch in
                        embeddings.embed_batches(facts + questions, EmbeddingSettings()) for v in batch])
                    emit('vectors', corpus_count=len(facts), values=vectors)
                with stage('upload'):
                    body = await fixture.make_document([facts[i % 5] * 8 for i in range(12)])
                app.dependency_overrides[processing_settings] = lambda: ProcessingSettings()
                app.dependency_overrides[embedding_settings] = lambda: EmbeddingSettings()
                with stage('pdf_extraction_and_chunking'):
                    assert (await fixture.process(body))[0] == 200
                with stage('indexing'):
                    status, result, _ = await fixture.embed(body)
                    assert status == 200 and result['embedding_dimension'] == 384
                    emit('indexed', chunks=result['chunk_count'])
                await fixture.setup_ask()
                provider = SimpleNamespace(answer=AsyncMock(side_effect=claims_for(facts[0])))
                with patch('rag.get_gemini_provider', return_value=provider):
                    for index in range(3):
                        with stage(f'rag_request_{index + 1}'):
                            status, answer, _ = await fixture.ask({'question': questions[0]})
                            assert status == 200 and answer['status'] == 'answered', answer.get('status')
                            assert answer['sources'] and answer['answer'] == facts[0]
                    assert provider.answer.await_count == 3
                    assert len(fixture.history.documents) == 3
                with stage('post_local_requests_idle'):
                    await asyncio.sleep(2)

                # Exercise the real adapter, bounded thread and trusted parser lease.
                # Bucket bytes/metadata are in memory, not a MongoDB client or transaction.
                with stage('gridfs_adapter_upload'):
                    storage_workers = StorageWorkers(Mock())
                    bucket = Bucket()
                    factory = lambda db, **kw: GridFSPDFStorage(db, bucket=bucket, **kw)
                    remote = GridFSProcessing(storage_workers, None, fixture.workers, storage_factory=factory)
                    row = fixture.docs.documents[ObjectId(body['id'])]
                    data = (fixture.root / (body['id'] + '.pdf')).read_bytes()
                    def save():
                        adapter = factory(None, owner_id=fixture.owner, knowledge_base_id=fixture.base_id, temp_root=str(fixture.root))
                        upload = SimpleNamespace(filename='synthetic.pdf', content_type='application/pdf', file=io.BytesIO(data))
                        return adapter.save(upload, row['_id'], 5 * 1024**2)
                    saved = await storage_workers.run(save)
                    row.update(saved, storage_backend='gridfs', storage_file_id=row['_id'],
                               storage_checksum=hashlib.sha256(data).hexdigest())
                    app.state.gridfs_processing = remote
                with stage('gridfs_materialization_and_reprocessing'):
                    assert (await fixture.process(body))[0] == 200
                with stage('gridfs_lease_drain'):
                    await remote.leases.aclose()
                    await storage_workers.aclose()
                    app.state.gridfs_processing = None
                with stage('post_requests_idle'):
                    await asyncio.sleep(2)
            with stage('application_shutdown'):
                server.should_exit = True
                await asyncio.wait_for(task, 30)
            emit('application_complete', runtime=runtime, rag_requests=3,
                 simulated=['authentication', 'MongoDB', 'Qdrant scores/storage', 'Gemini', 'GridFS bucket'],
                 real=['Uvicorn', 'FastAPI lifespan/routes', 'PDF child', 'MiniLM', 'RAG orchestration', 'parser leases'])
    finally:
        if remote is not None:
            await remote.leases.aclose()
        if storage_workers is not None:
            await storage_workers.aclose()
        if task is not None and not task.done():
            server.should_exit = True
            await asyncio.wait_for(task, 30)
        await fixture.asyncTearDown()
        fixture.doCleanups()
