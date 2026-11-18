"""No Docker, socket listener, provider or database; actual policy route wiring."""
import asyncio
import contextlib
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[2]/'backend'), str(Path(__file__).resolve().parents[2]/'backend/tests')]

class Wiring(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_routes_with_policies(self):
        from gemini_application_probe import workload, STAGES
        with patch('config.dotenv_values', return_value={}):
            from main import app
            import httpx
        class Server:
            def __init__(self, config): self.started = self.should_exit = False
            async def serve(self):
                async with app.router.lifespan_context(app):
                    self.started = True
                    while not self.should_exit: await asyncio.sleep(.001)
        stages, events = [], []
        @contextlib.contextmanager
        def stage(name):
            stages.append(name)
            yield
        await workload(stage, lambda event, **kw: events.append({'event':event, **kw}),
            server_factory=Server, transport=httpx.ASGITransport(app=app), idle_seconds=0)
        self.assertEqual(set(stages), STAGES)
        self.assertEqual(len(stages), len(STAGES))
        self.assertTrue(any(e['event']=='safeguards' for e in events))
        self.assertIsNone(app.state.cpu_workers)
        self.assertIsNone(app.state.gridfs_processing)


class Assessment(unittest.TestCase):
    def record(self):
        from gemini_application_probe import STAGES
        from assessment import LIMIT
        events = [dict(event='end', stage=s, current_bytes=200*2**20, lifetime_peak_bytes=300*2**20,
            seconds=1., cpu_delta=dict(usage_usec=1,nr_throttled=1,throttled_usec=1)) for s in STAGES]
        events += [dict(event='request',status=200) for _ in range(20)]
        events += [dict(event='safeguards',private_access=True,budgets=True,growth=True,gridfs=True,
            no_local_ml=True,embedding_dimension=768),dict(event='application_complete'),dict(event='complete')]
        return dict(events=events,kernel_peak_bytes=300*2**20,verified_limits=dict(memory_bytes=LIMIT,swap_bytes=0,cpus=.1),
                    complete=True,exit_code=0,oom_killed=False,timed_out=False,memory_events='oom 0\noom_kill 0')
    def test_complete_and_missing_evidence(self):
        from gemini_assessment import assess_gemini
        self.assertEqual(assess_gemini(self.record())['screen'],'synthetic_screen_pass')
        for key in ('events','kernel_peak_bytes','memory_events','verified_limits','complete'):
            r=self.record();r.pop(key)
            self.assertNotEqual(assess_gemini(r)['screen'],'synthetic_screen_pass')
    def test_security_failures_headroom_and_growth(self):
        from gemini_assessment import assess_gemini
        for change in ({'kernel_peak_bytes':410*2**20},{'oom_killed':True},{'timed_out':True},
                       {'exit_code':1},{'memory_events':'oom 1\noom_kill 0'}):
            self.assertNotEqual(assess_gemini(self.record()|change)['screen'],'synthetic_screen_pass')
        r=self.record()
        for e in r['events']:
            if e.get('stage','').startswith('idle_'): e['current_bytes'] += int(e['stage'][-1])*2**20
        self.assertEqual(assess_gemini(r)['screen'],'retained_memory_growth')
    def test_build_and_runner_isolation(self):
        root=Path(__file__).resolve().parents[2]
        docker=(root/'experiments/render-free/Dockerfile.gemini').read_text()
        self.assertNotIn('package_model.py',docker)
        self.assertNotIn('COPY . ',docker)
        self.assertIn('USER 10001:10001',docker)
        runner=(root/'experiments/render-free/run.py').read_text()
        for text in ("'--network', 'none'", "'--memory-swap'", "'512m'", "'0.1'", "'memory.peak'", "'memory.current'"):
            self.assertIn(text,runner)
        self.assertIn('assess_gemini',runner)
    def test_local_ml_import_guard(self):
        from gemini_application_probe import NoLocalML
        for module in ('torch','torch.nn','sentence_transformers','transformers'):
            with self.assertRaises(AssertionError): NoLocalML().find_spec(module)
        self.assertIsNone(NoLocalML().find_spec('json'))

if __name__ == '__main__': unittest.main()
