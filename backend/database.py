"""One async client per application lifespan, reused by request dependencies."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from config import ConfigurationError, load_settings
from users import ensure_user_indexes

logger = logging.getLogger(__name__)
UNAVAILABLE_MESSAGE = "MongoDB is unavailable. Check configuration and Atlas connectivity."
CONFIGURATION_MESSAGE = "MongoDB configuration is invalid. Check backend environment settings."


async def ping_database(database: AsyncDatabase) -> bool:
    """Bound health checks and never return or log raw driver exceptions."""
    try:
        async with asyncio.timeout(5):
            await database.command("ping")
        return True
    except (PyMongoError, TimeoutError):
        return False


@asynccontextmanager
async def database_lifespan(app: FastAPI):
    client = None
    app.state.database = None
    app.state.database_configuration_error = None
    app.state.users_index_ready = False
    try:
        try:
            settings = load_settings()
            client = AsyncMongoClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=5000,
                timeoutMS=5000,
                tls=True,
                tlsAllowInvalidCertificates=False,
                tlsAllowInvalidHostnames=False,
            )
            app.state.database = client.get_database(settings.mongodb_db_name)
        except ConfigurationError as error:
            app.state.database_configuration_error = str(error)
        except (PyMongoError, ValueError):
            app.state.database_configuration_error = CONFIGURATION_MESSAGE

        if app.state.database_configuration_error:
            logger.error("%s", app.state.database_configuration_error)
        elif not await ping_database(app.state.database):
            # Keep the client: later health requests can detect recovery.
            logger.warning("%s", UNAVAILABLE_MESSAGE)
        else:
            try:
                async with asyncio.timeout(5):
                    await ensure_user_indexes(app.state.database)
                app.state.users_index_ready = True
            except (PyMongoError, TimeoutError):
                # Never permit inserts without confirmed uniqueness enforcement.
                # No destructive repair of existing records or indexes is attempted.
                logger.error("Users index initialization failed. Registration is unavailable.")

        # Liveness remains available even if database readiness fails.
        yield
    finally:
        app.state.users_index_ready = False
        app.state.database = None
        if client is not None:
            try:
                await client.close()
            except PyMongoError:
                logger.warning("MongoDB client shutdown failed.")


def get_database(request: Request) -> AsyncDatabase:
    """Use with Depends(get_database) in future modules; never allocate a client."""
    database = getattr(request.app.state, "database", None)
    if database is None:
        message = getattr(request.app.state, "database_configuration_error", None)
        raise HTTPException(status_code=503, detail=message or UNAVAILABLE_MESSAGE)
    return database
