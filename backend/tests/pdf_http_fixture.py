"""Test-only main:app lifespan replacement; no external service initialization."""
from contextlib import asynccontextmanager
import signal


def create_app():
    from main import app
    import test_processing as fixtures
    fixture = fixtures.ProcessingTests()

    @asynccontextmanager
    async def lifespan(application):
        await fixture.asyncSetUp()
        try:
            application.state.fixture_document = await fixture.make_document(('Real HTTP PDF source.',))
            yield
        finally:
            await fixture.asyncTearDown()

    app.router.lifespan_context = lifespan

    @app.get('/test-fixture')
    async def identifier():
        return app.state.fixture_document

    @app.get('/test-stop')
    async def stop():
        # Uvicorn handles SIGINT as graceful shutdown; no PDF child is killed.
        signal.raise_signal(signal.SIGINT)
        return {'stopping': True}

    return app
