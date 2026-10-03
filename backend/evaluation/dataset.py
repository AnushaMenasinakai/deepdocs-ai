"""Versioned synthetic ground truth; no application configuration or user data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("direct", "paraphrase", "multi_chunk", "related_unsupported", "unrelated", "ambiguous", "disambiguation")


def identifier(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def load_dataset():
    corpus = json.loads((ROOT / "corpus.json").read_text(encoding="utf-8"))
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    fixtures = json.loads((ROOT / "fixture_rankings.json").read_text(encoding="utf-8"))
    assert corpus["version"] == cases["version"] == fixtures["version"]
    chunks = corpus["chunks"]
    assert len({c["id"] for c in chunks}) == len(chunks)
    known = {(c["document"], c["page"]) for c in chunks}
    assert all(type(c["page"]) is int and c["page"] > 0 and c["text"].strip() for c in chunks)
    assert len({c["id"] for c in cases["cases"]}) == len(cases["cases"])
    for case in cases["cases"]:
        assert case["category"] in CATEGORIES and 0 < len(case["question"].strip()) <= 1000
        assert type(case["expected_answerable"]) is bool
        assert bool(case["expected_sources"]) == case["expected_answerable"]
        assert all((s["document"], s["page"]) in known for s in case["expected_sources"])
    assert set(fixtures["rankings"]) == {c["id"] for c in cases["cases"]}
    assert all(h["chunk"] in {c["id"] for c in chunks} for hits in fixtures["rankings"].values() for h in hits)
    return corpus["version"], chunks, cases["cases"], fixtures["rankings"]


def public_chunk(chunk, index=0):
    return {"document_id": identifier(chunk["document"]), "chunk_id": identifier(chunk["id"]),
            "chunk_index": index, "source_filename": chunk["document"], "text": chunk["text"],
            "page_start": chunk["page"], "page_end": chunk["page"]}
