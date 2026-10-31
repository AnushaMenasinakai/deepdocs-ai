"""One in-flight public probe per dependency and application; no result cache."""
import asyncio
from fastapi import Request
from vector_store import VectorFailure


class DependencyDiagnostics:
    def __init__(self):
        self._tasks = {}
        self._closing = False

    def begin_shutdown(self):
        self._closing = True

    async def run(self, name, probe):
        if name not in ('database', 'qdrant'):
            raise ValueError('Unknown diagnostic.')
        if self._closing:
            if name == 'database':
                return False
            raise VectorFailure()
        task = self._tasks.get(name)
        if task is None or task.done():
            task = asyncio.create_task(probe())
            self._tasks[name] = task
            task.add_done_callback(self._observed)
        return await asyncio.shield(task)

    @staticmethod
    def _observed(task):
        if not task.cancelled():
            task.exception()

    async def close(self):
        self.begin_shutdown()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def get_diagnostics(request: Request):
    # Lifespan installs fresh state. Lazy creation supports isolated ASGI apps.
    # Stay on the event loop: no await between lookup and publication.
    service = getattr(request.app.state, 'dependency_diagnostics', None)
    if service is None:
        service = DependencyDiagnostics()
        request.app.state.dependency_diagnostics = service
    return service
