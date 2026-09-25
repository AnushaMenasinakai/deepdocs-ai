from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase

from database import UNAVAILABLE_MESSAGE, database_lifespan, get_database, ping_database
from auth_routes import router as auth_router
from knowledge_base_routes import router as knowledge_base_router
from document_routes import router as document_router

app = FastAPI(title="DeepDocs AI API", lifespan=database_lifespan)
app.include_router(auth_router)
app.include_router(knowledge_base_router)
app.include_router(document_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, error):
    # FastAPI's default errors can include raw input, including passwords or
    # an entire malformed body. Keep field locations/messages without input/context.
    details = [
        {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})

# Only the local Vite development frontend may make cross-origin requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
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
