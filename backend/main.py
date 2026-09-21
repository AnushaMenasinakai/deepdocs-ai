from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo.asynchronous.database import AsyncDatabase

from database import UNAVAILABLE_MESSAGE, database_lifespan, get_database, ping_database

app = FastAPI(title="DeepDocs AI API", lifespan=database_lifespan)

# Only the local Vite development frontend may make cross-origin requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET"],
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
