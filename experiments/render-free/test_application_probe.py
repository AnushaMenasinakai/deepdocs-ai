"""Offline wiring test: no listening socket, real model, Docker or cloud client."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]


class Wiring(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_routes_gridfs_and_cleanup(self):
        import config
        with patch('config.dotenv_values', return_value={}):
            import uvicorn
            from main import app
            from application_probe import run_application
            from probe import FACTS, QUESTIONS

            class Server:
                def __init__(self, configuration):
                    self.started = self.should_exit = False
                async def serve(self):
                    async with app.router.lifespan_context(app):
                        self.started = True
                        while not self.should_exit:
                            await asyncio.sleep(.001)

            class Model:
                def get_sentence_embedding_dimension(self):
                    return 384
                def encode(self, texts, **kwargs):
                    return [[1.0] + [0.0] * 383 for _ in texts]

            stages, events = [], []
            @contextmanager
            def stage(name):
                stages.append(name)
                yield
            with patch.object(uvicorn, 'Server', Server), patch.dict('os.environ', {
                    'GRIDFS_UPLOAD_ENABLED': 'false', 'RAG_VERIFIED_EXPERIMENT_ENABLED': 'false'}):
                await run_application('fake', stage, lambda name, **kw: events.append(name), Model, FACTS, QUESTIONS)
            self.assertIn('application_complete', events)
            self.assertEqual(len(stages), len(set(stages)))
            for name in ('application_startup', 'model_initialization', 'rag_request_3',
                         'gridfs_materialization_and_reprocessing', 'post_requests_idle', 'application_shutdown'):
                self.assertIn(name, stages)
            self.assertIsNone(app.state.cpu_workers)
            self.assertIsNone(app.state.gridfs_processing)


if __name__ == '__main__':
    unittest.main()
