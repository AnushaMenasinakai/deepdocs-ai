from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from config import load_cors_origins, load_expensive_admission_settings
from admission_lifecycle import AdmissionService

from database import UNAVAILABLE_MESSAGE, database_lifespan, get_database, ping_database
from auth_routes import router as auth_router
from knowledge_base_routes import router as knowledge_base_router
from document_routes import router as document_router
from search_routes import router as search_router
from rag_routes import router as rag_router
from history_routes import router as history_router
from dashboard_routes import router as dashboard_router
import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from auth_protection import auth_protection_lifespan
from json_body_boundary import RegisteredJSONBodyBoundary


@asynccontextmanager
async def application_lifespan(app):
    # Validate before external startup; each lifespan owns fresh local state.
    app.state.expensive_admission = None
    service = AdmissionService(load_expensive_admission_settings())
    dependencies = AsyncExitStack()
    try:
        await dependencies.enter_async_context(database_lifespan(app))
        await dependencies.enter_async_context(auth_protection_lifespan(app))
        app.state.expensive_admission = service
        yield
    finally:
        service.begin_shutdown()
        exception = sys.exc_info()

        async def cleanup():
            # Keep clients alive even if the lifespan waiter is cancelled again.
            await service.aclose()
            await dependencies.__aexit__(*exception)

        # One owned cleanup task per lifespan, not a work/admission queue.
        closing = asyncio.create_task(cleanup())
        cancelled = False
        try:
            while True:
                try:
                    await asyncio.shield(closing)
                    break
                except asyncio.CancelledError:
                    if closing.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
        finally:
            app.state.expensive_admission = None


app = FastAPI(title="DeepDocs AI API", lifespan=application_lifespan)
app.include_router(auth_router)
app.include_router(knowledge_base_router)
app.include_router(document_router)
app.include_router(search_router)
app.include_router(rag_router)
app.include_router(history_router)
app.include_router(dashboard_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, error):
    # FastAPI's default errors can include raw input, including passwords or
    # an entire malformed body. Keep field locations/messages without input/context.
    details = [
        {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})

# Local Vite origins plus explicitly validated frontend origins.
# Starlette inserts later middleware outside earlier middleware: CORS wraps
# the boundary, including responses emitted directly by its receive guard.
app.add_middleware(RegisteredJSONBodyBoundary, router=app.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=load_cors_origins(),
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "message": "DeepDocs AI API is running"}


@app.get("/api/health/database")
async def database_health(database: AsyncDatabase = Depends(get_database)):
    if not await ping_database(database):
        raise HTTPException(status_code=503, detail=UNAVAILABLE_MESSAGE)
    return {"status": "ok", "message": "MongoDB is reachable"}


@app.get("/api/ready")
async def ready(request: Request):
    readiness = getattr(request.app.state, "readiness", None)
    database = getattr(request.app.state, "database", None)
    available = readiness is not None and await readiness.check(database)
    return JSONResponse(
        {"status": "ready" if available else "not_ready"},
        status_code=200 if available else 503,
        headers={"Cache-Control": "no-store"},
    )
