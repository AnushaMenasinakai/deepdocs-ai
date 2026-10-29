"""Actual python -m uvicorn launch, loopback HTTP and real PDF spawn."""
import json
import os
from pathlib import Path
from queue import Queue
import re
import subprocess
import sys
from threading import Thread
import unittest
from urllib.request import Request, urlopen


class PDFHTTPTests(unittest.TestCase):
    def test_uvicorn_module_launch_real_pdf_processing(self):
        backend = Path(__file__).resolve().parents[1]
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        env.update(PYTHONPATH=os.pathsep.join((str(backend), str(backend / 'tests'))),
                   PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        command = [sys.executable, '-B', '-m', 'uvicorn', 'pdf_http_fixture:create_app',
                   '--factory', '--host', '127.0.0.1', '--port', '0', '--workers', '1',
                   '--no-proxy-headers', '--no-access-log']
        process = subprocess.Popen(command, cwd=backend, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        ready = Queue(maxsize=1)
        logs = []
        def read():
            published = False
            for line in process.stdout:
                if len(logs) < 100:
                    logs.append(line)
                match = re.search(r'http://127\.0\.0\.1:(\d+)', line)
                if match and not published:
                    ready.put(match.group(0))
                    published = True
            if not published:
                ready.put(None)
        reader = Thread(target=read)
        reader.start()
        base = None
        try:
            base = ready.get(timeout=30)
            self.assertIsNotNone(base, ''.join(logs))
            with urlopen(base + '/test-fixture', timeout=10) as response:
                identifier = json.load(response)['id']
            with urlopen(Request(base + '/api/documents/' + identifier + '/process',
                                 data=b'', method='POST'), timeout=30) as response:
                result = json.load(response)
                self.assertEqual(response.status, 200)
                self.assertEqual(result['status'], 'processed')
                self.assertEqual(result['page_count'], 1)
            with urlopen(base + '/api/documents/' + identifier + '/chunks', timeout=10) as response:
                self.assertEqual(json.load(response)[0]['text'], 'Real HTTP PDF source.')
        finally:
            if base is not None and process.poll() is None:
                with urlopen(base + '/test-stop', timeout=10):
                    pass
            process.wait(timeout=30)
            reader.join(timeout=5)
            process.stdout.close()
        self.assertFalse(reader.is_alive())
        self.assertIn('Application shutdown complete.', ''.join(logs))
