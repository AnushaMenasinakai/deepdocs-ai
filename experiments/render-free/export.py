"""Build-time offline conversion; no quantization or model substitution."""
import json
from pathlib import Path
import torch
from embeddings import load_model
from config import EmbeddingSettings

model = load_model(EmbeddingSettings.model_name)
assert model.get_sentence_embedding_dimension() == 384
pool = model[1].get_config_dict()
assert pool['pooling_mode_mean_tokens']
assert not any(v for k, v in pool.items() if k.startswith('pooling_mode_') and k != 'pooling_mode_mean_tokens')
out = Path('/experiment/model')
out.mkdir(parents=True)
model.tokenizer.backend_tokenizer.save(str(out / 'tokenizer.json'))
(out / 'settings.json').write_text(json.dumps({'max_length': model.max_seq_length, 'dimension': 384}))

class Encoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = model[0].auto_model
    def forward(self, input_ids, attention_mask, token_type_ids):
        return self.encoder(input_ids=input_ids, attention_mask=attention_mask,
                            token_type_ids=token_type_ids).last_hidden_state

encoder = Encoder().eval()
inputs = model.tokenizer(['Synthetic export sentence.'], return_tensors='pt')
names = ['input_ids', 'attention_mask', 'token_type_ids']
with torch.no_grad():
    torch.onnx.export(encoder, tuple(inputs[n] for n in names), str(out / 'model.onnx'),
                      input_names=names, output_names=['hidden'], opset_version=17,
                      dynamo=False, dynamic_axes={n: {0: 'batch', 1: 'sequence'} for n in names + ['hidden']})
import onnx
onnx.checker.check_model(str(out / 'model.onnx'))
