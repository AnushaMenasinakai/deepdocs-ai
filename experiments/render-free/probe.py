"""Synthetic, sequential probe. Network isolation is enforced by Docker."""
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

CG = Path('/sys/fs/cgroup')

def emit(event, **values):
    print(json.dumps({'event': event, 'time': time.monotonic(), **values}), flush=True)

@contextlib.contextmanager
def stage(name):
    emit('start', stage=name)
    started = time.monotonic()
    cpu_start = dict(line.split() for line in (CG / 'cpu.stat').read_text().splitlines())
    try:
        yield
    finally:
        cpu_end = dict(line.split() for line in (CG / 'cpu.stat').read_text().splitlines())
        emit('end', stage=name, seconds=time.monotonic() - started,
             cpu_delta={key: int(cpu_end[key]) - int(cpu_start[key]) for key in cpu_start},
             current_bytes=int((CG / 'memory.current').read_text()),
             lifetime_peak_bytes=int((CG / 'memory.peak').read_text()))

class ONNXModel:
    def __init__(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        settings = json.loads(Path('/experiment/model/settings.json').read_text())
        if settings.get('pooling') != 'mean' or settings.get('dimension') != 384:
            raise ValueError('Unsupported exported pooling contract')
        self.tokenizer = Tokenizer.from_file('/experiment/model/tokenizer.json')
        self.tokenizer.enable_truncation(max_length=settings['max_length'])
        self.tokenizer.enable_padding(pad_id=settings['pad_id'], pad_token=settings['pad_token'])
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession('/experiment/model/model.onnx', options,
                                           providers=['CPUExecutionProvider'])
    def get_sentence_embedding_dimension(self):
        return 384
    def encode(self, texts, **kwargs):
        import numpy as np
        enc = self.tokenizer.encode_batch(texts)
        inputs = {k: np.array([getattr(e, attr) for e in enc], dtype=np.int64)
                  for k, attr in [('input_ids', 'ids'), ('attention_mask', 'attention_mask'),
                                  ('token_type_ids', 'type_ids')]}
        hidden = self.session.run(None, inputs)[0]
        mask = inputs['attention_mask'][..., None].astype(np.float32)
        vectors = (hidden * mask).sum(1) / np.maximum(mask.sum(1), 1e-9)
        return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)

FACTS = [
    'Project Aurora is coordinated by Alex Morgan. Its review is on 18 December 2026.',
    'The River Observatory measures water temperature every fifteen minutes.',
    'The library opens at nine in the morning and closes at six in the evening.',
    'The solar experiment uses twelve panels and a cobalt storage cabinet.',
    'The support email address and project budget are not documented.',
    'Unicode sample: café, naïve, Δ, and a rocket 🚀. ' + 'A long synthetic sentence. ' * 100,
]
QUESTIONS = ['Who coordinates Project Aurora?', 'Who is responsible for the pilot?',
             'When is the Aurora review?', 'How often is water temperature measured?',
             'How many solar panels are used?', 'What is the coordinator email address?',
             'What is the project budget?', 'Who coordinates Aurora and when is its review?']

def main():
    runtime = sys.argv[1]
    from importlib.metadata import version
    emit('versions', packages={name:version(name) for name in
         ['torch','sentence-transformers','tokenizers','onnxruntime','PyMuPDF','numpy']})
    with stage('application_import_idle'):
        import main as application
        import embeddings
        from config import EmbeddingSettings
        time.sleep(2)
    from application_probe import run_application
    from unittest.mock import patch
    import os
    with patch('config.dotenv_values', return_value={}), patch.dict(os.environ, {
        'GRIDFS_UPLOAD_ENABLED': 'false', 'RAG_VERIFIED_EXPERIMENT_ENABLED': 'false',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}):
        asyncio.run(run_application(runtime, stage, emit,
            lambda: embeddings.load_model(EmbeddingSettings.model_name) if runtime == 'torch' else ONNXModel(),
            FACTS, QUESTIONS))
    if runtime == 'onnx':
        assert 'torch' not in sys.modules, 'ONNX probe unexpectedly imported torch'
    emit('complete', runtime=runtime)

if __name__ == '__main__':
    main()
