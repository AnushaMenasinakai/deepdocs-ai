"""Build-time offline conversion; no quantization or model substitution."""
import json
from pathlib import Path
import torch
from embeddings import load_model
from config import EmbeddingSettings
from pooling_contract import require_mean_pooling

model = load_model(EmbeddingSettings.model_name)
assert model.get_sentence_embedding_dimension() == 384
assert [type(module).__name__ for module in model] == ['Transformer', 'Pooling', 'Normalize'], 'Unsupported model module pipeline'
require_mean_pooling(model[1])
assert not getattr(model, 'default_prompt_name', None), 'Default prompts are not supported'
assert not getattr(model[0], 'do_lower_case', False), 'Additional input lowercasing is not supported'
assert model.tokenizer.padding_side == model.tokenizer.truncation_side == 'right', 'Unsupported tokenizer direction'
out = Path('/experiment/model')
out.mkdir(parents=True)
model.tokenizer.backend_tokenizer.save(str(out / 'tokenizer.json'))
(out / 'settings.json').write_text(json.dumps({'max_length': model.max_seq_length, 'dimension': 384,
    'pooling': 'mean', 'pad_id': model.tokenizer.pad_token_id, 'pad_token': model.tokenizer.pad_token}))

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

# Run only in the GitHub image build, offline, with the real loaded reference.
# Covers padding, Unicode, unequal lengths and truncation; not corpus-wide equivalence.
import numpy as np
from probe import ONNXModel
candidate = ONNXModel()
texts = ['Short sentence.', 'A longer synthetic sentence about a river observatory.',
         'Unicode café Δ rocket 🚀.', 'synthetic token ' * 300]
reference_tokens = model.tokenize(texts)
encoded = candidate.tokenizer.encode_batch(texts)
for key, attribute in [('input_ids', 'ids'), ('attention_mask', 'attention_mask'), ('token_type_ids', 'type_ids')]:
    np.testing.assert_array_equal(reference_tokens[key].numpy(), [getattr(e, attribute) for e in encoded],
                                  err_msg='Tokenizer mismatch: ' + key)
reference = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
actual = candidate.encode(texts)
assert reference.shape == actual.shape == (4, 384)
assert np.isfinite(actual).all()
np.testing.assert_allclose(np.linalg.norm(actual, axis=1), 1., atol=1e-6)
np.testing.assert_allclose(actual, reference, atol=1e-4, rtol=1e-4,
                           err_msg='ONNX output differs from reference pooling/normalization')
print('Offline tokenizer, pooling, normalization and 384-dimensional parity checks passed')
