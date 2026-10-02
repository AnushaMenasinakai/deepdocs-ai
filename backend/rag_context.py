"""Bounded reference data. Validated provenance accompanies exactly the included chunks."""
import json
import math
from dataclasses import dataclass, field
from bson import ObjectId


class RAGFailure(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    serialized: str = field(repr=False)
    chunks: tuple = field(repr=False)


def build_context(results, settings):
    if not isinstance(results, list):
        raise RAGFailure("Retrieval data is unavailable.")
    valid = []
    for value in results[:settings.top_k]:
        if not isinstance(value, dict):
            continue
        score = value.get("score")
        if type(score) not in (int, float):
            continue
        try:
            if not math.isfinite(score) or not settings.min_relevance_score <= score <= 1:
                continue
        except OverflowError:
            continue
        if any(not isinstance(value.get(key), str) or not ObjectId.is_valid(value[key])
               for key in ("document_id", "chunk_id")):
            continue
        if (type(value.get("page_start")) is not int or type(value.get("page_end")) is not int
                or not 1 <= value["page_start"] <= value["page_end"]):
            continue
        if any(not isinstance(value.get(key), str) or not value[key].strip()
               for key in ("text", "source_filename")):
            continue
        filename = value["source_filename"]
        if any(character in filename for character in ("/", "\\", ":")) or any(ord(c) < 32 for c in filename):
            continue  # Public references must be filenames, never filesystem paths.
        record = {key: value[key] for key in
                  ("document_id", "chunk_id", "source_filename", "page_start", "page_end", "text")}
        valid.append((score, record))
    valid.sort(key=lambda pair: -pair[0])  # Stable ties preserve retrieval ordering.
    selected, seen = [], set()
    serialized = "[]"
    for _, record in valid:
        if record["chunk_id"] in seen:
            continue
        candidate = json.dumps([*selected, record], ensure_ascii=False, separators=(",", ":"))
        try:
            candidate.encode("utf-8")  # Reject malformed Unicode without rewriting source text.
        except UnicodeError:
            continue
        if len(candidate) > settings.max_context_chars:
            continue  # Keep complete chunks; smaller later chunks may still fit.
        selected.append(record)
        seen.add(record["chunk_id"])
        serialized = candidate
    return Context(serialized, tuple(selected))
