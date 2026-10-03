"""Exercise the unchanged production context/source boundary with synthetic probes."""
from config import RAGSettings
from rag_context import build_context
from rag import supporting_sources
from .dataset import identifier


def chunk(name, text, score=.8, page=1):
    return {"document_id": identifier("probe.pdf"), "chunk_id": identifier(name), "source_filename": "probe.pdf",
            "page_start": page, "page_end": page, "text": text, "score": score, "chunk_index": 0}


def context_probes():
    first, second = chunk("first", "Complete evidence "*30, .9), chunk("second", "Another evidence passage "*30, .8, 2)
    full = build_context([first, second], RAGSettings())
    exact = len(full.serialized)
    at_boundary = build_context([first, second], RAGSettings(max_context_chars=exact))
    below = build_context([first, second], RAGSettings(max_context_chars=exact-1))
    duplicate_page = chunk("same-page", "Additional evidence on the first page", .7)
    oversized = chunk("oversized", "x"*13000, .95)
    low = chunk("low", "Excluded weak evidence", .49, 3)
    context = build_context([oversized, first, duplicate_page, low, second], RAGSettings())
    sources = supporting_sources(context)
    invalid = {**first, "page_start": 0}
    return {
        "exact_budget_keeps_evidence": len(at_boundary.chunks) == 2,
        "one_character_below_skips_whole_chunk": [c["chunk_id"] for c in below.chunks] == [first["chunk_id"]],
        "bounded": len(context.serialized) <= 12000,
        "oversized_and_low_excluded": all(c["chunk_id"] not in (oversized["chunk_id"], low["chunk_id"]) for c in context.chunks),
        "relevance_order_preserved": [c["chunk_id"] for c in context.chunks] == [first["chunk_id"], second["chunk_id"], duplicate_page["chunk_id"]],
        "complete_text_preserved": context.chunks[0]["text"] == first["text"],
        "duplicate_page_deduplicated": len(sources) == 2,
        "sources_only_included_pages": [s["page_start"] for s in sources] == [1, 2],
        "invalid_provenance_excluded": not build_context([invalid], RAGSettings()).chunks,
        "duplicate_chunk_excluded": len(build_context([first, first], RAGSettings()).chunks) == 1,
        "empty_context": not build_context([], RAGSettings()).chunks,
    }
