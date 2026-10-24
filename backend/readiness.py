"""Per-lifespan, read-only readiness; never repairs startup or operation state."""
import asyncio
import time

import config
from vector_store import get_vector_store

DEPENDENCY_TIMEOUT = 2.0
PROBE_TIMEOUT = 3.0
CACHE_SECONDS = 2.0
INDEX_GROUPS = ("users", "knowledge_bases", "documents", "chunks", "history")
CONFIG_LOADERS = (
    "load_settings", "load_jwt_settings", "load_qdrant_settings",
    "load_gemini_settings", "load_document_settings", "load_processing_settings",
    "load_embedding_settings", "load_rag_settings", "load_cors_origins",
)


def configuration_ready():
    try:
        for name in CONFIG_LOADERS:
            getattr(config, name)()
        return True
    except Exception:
        # Never retain or expose configuration values or exception details.
        return False


class Readiness:
    def __init__(self):
        self.configuration_valid = configuration_ready()
        self.initialized = False
        self.shutting_down = False
        self.indexes = dict.fromkeys(INDEX_GROUPS, False)
        self._task = None
        self._result = False
        self._expires = 0.0

    def prerequisites(self, database):
        return (self.initialized and not self.shutting_down
                and self.configuration_valid and database is not None
                and all(self.indexes.get(name) is True for name in INDEX_GROUPS))

    async def _mongo(self, database):
        async with asyncio.timeout(DEPENDENCY_TIMEOUT):
            result = await database.command("ping")
            return result.get("ok") == 1

    async def _qdrant(self):
        async with asyncio.timeout(DEPENDENCY_TIMEOUT):
            # Lazy client construction is allowed; health only lists collections.
            await get_vector_store().health()
            return True

    async def _probe(self, database):
        result = False
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                async with asyncio.TaskGroup() as group:
                    mongo = group.create_task(self._mongo(database))
                    qdrant = group.create_task(self._qdrant())
                result = mongo.result() is True and qdrant.result() is True
        except Exception:
            result = False
        # Cancellation propagates, so a shutdown cannot publish a cached success.
        if self.prerequisites(database):
            self._result = result
            self._expires = time.monotonic() + CACHE_SECONDS
        return result and self.prerequisites(database)

    async def check(self, database):
        if not self.prerequisites(database):
            return False
        if time.monotonic() < self._expires:
            return self._result
        # No await between inspecting and assigning: one cycle per event loop.
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._probe(database))
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                result = await asyncio.shield(self._task)
            return result and self.prerequisites(database)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            if self.shutting_down:
                return False
            raise

    async def close(self):
        self.shutting_down = True
        self.initialized = False
        self._result = False
        self._expires = 0.0
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
