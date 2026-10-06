"""Approved Phase16A-R model only. Download is explicit; inference is local-only."""
import json
from pathlib import Path

from evaluation.reranking import finite_score

REQUESTED_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
# Hugging Face resolves the approved legacy spelling to this canonical repository.
MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
CACHE = Path(__file__).resolve().parents[1]/".cache/reranking"
FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "vocab.txt", "README.md")
BATCH_SIZE, MAX_LENGTH, MAX_PAIRS = 16, 512, 225


def download():
    """Only explicitly authorized model/metadata; never called by loading or tests."""
    import os
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Keep any auxiliary cache in the repository's already ignored cache root.
    os.environ["HF_HOME"] = str(CACHE)
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(MODEL, revision=REVISION, cache_dir=str(CACHE),
        allow_patterns=list(FILES), token=False, max_workers=2))


def snapshot():
    path = CACHE / ("models--"+MODEL.replace("/","--")) / "snapshots" / REVISION
    if not all((path/name).is_file() for name in FILES):
        raise ValueError("Approved local reranker snapshot is incomplete")
    config = json.loads((path/"config.json").read_text(encoding="utf-8"))
    if "BertForSequenceClassification" not in config.get("architectures",[]):
        raise ValueError("Expected trained sequence-classification architecture")
    return path


def construct_model(path, factory, identity):
    """Small injectable constructor boundary for offline adapter tests."""
    return factory(str(path), device="cpu", local_files_only=True, trust_remote_code=False,
        max_length=MAX_LENGTH, activation_fn=identity, model_kwargs={"use_safetensors":True})


def load():
    import os
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import torch
    from sentence_transformers import CrossEncoder
    torch.set_num_threads(4)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    model = construct_model(snapshot(), CrossEncoder, torch.nn.Identity())
    model.model.eval()
    if model.model.config.num_labels != 1:
        raise ValueError("Expected one scalar relevance logit")
    return model


def score_pairs(model, pairs):
    if not isinstance(pairs,list) or not 1 <= len(pairs) <= MAX_PAIRS:
        raise ValueError("Bounded pair list required")
    if any(not isinstance(p,(tuple,list)) or len(p)!=2 or any(not isinstance(t,str) or not t.strip() for t in p) for p in pairs):
        raise ValueError("Expected nonempty question/passage pairs")
    values = model.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False,
                           convert_to_numpy=True)
    values = values.tolist() if hasattr(values,"tolist") else values
    if not isinstance(values,list) or len(values)!=len(pairs):
        raise ValueError("Invalid scalar prediction shape")
    return [finite_score(value) for value in values]


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-approved-model",action="store_true",required=True)
    parser.parse_args()
    try:
        path=download()
        print("Approved model cached:",MODEL,"revision",REVISION)
        print("Required files bytes:",sum((path/name).stat().st_size for name in FILES))
    except Exception as exc:
        print("Approved model download failed:",type(exc).__name__)
        raise SystemExit(1)
