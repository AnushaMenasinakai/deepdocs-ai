"""Build-only public model acquisition; never reads the developer's cache."""
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
CACHE = Path("/app/backend/.cache/embeddings")

snapshot = Path(snapshot_download(
    repo_id=MODEL,
    revision=REVISION,
    cache_dir=str(CACHE),
    token=False,
    allow_patterns=["*.json", "*.txt", "model.safetensors", "README.md", "LICENSE*"],
))
assert snapshot.name == REVISION
assert (snapshot / "model.safetensors").is_file()
# The existing application requests the repository's default revision. Pin its
# offline main reference to this verified commit, without changing the provider.
refs = snapshot.parent.parent / "refs"
refs.mkdir(exist_ok=True)
(refs / "main").write_text(REVISION, encoding="utf-8")
