"""Read-only question answering using the existing validated retrieval path."""
import retrieval
from search_schemas import SearchRequest
from rag_context import build_context
from rag_citations import citation_context, cited_result
from gemini_provider import get_gemini_provider, GeminiFailure
from verified_rag import (plan_question, build_candidate_context, validate_verification,
                          VerificationMalformed)

INSUFFICIENT_ANSWER = "I couldn't find enough relevant information in this Knowledge Base to answer that question."


async def answer_question(database, owner_id, base_id, question, embedding_settings, settings, *,
                          workers=None, workflow=None, generation_unneeded=None,
                          verified=False, deadline=None):
    if verified:
        return await _verified_answer(database, owner_id, base_id, question, embedding_settings,
                                      workers=workers, workflow=workflow,
                                      generation_unneeded=generation_unneeded, deadline=deadline)
    results = await retrieval.search_chunks(database, owner_id, base_id,
        SearchRequest(query=question, top_k=settings.top_k), embedding_settings,
        workers=workers, workflow=workflow)
    context = build_context(results, settings)
    if not context.chunks:
        if generation_unneeded is not None:
            generation_unneeded()
        return {"status": "insufficient_context", "answer": INSUFFICIENT_ANSWER, "retrieved_chunk_count": 0, "sources": [], "citation_version": 1, "claims": []}
    cited_context = citation_context(context)
    answer = await get_gemini_provider().answer(question, cited_context)
    if answer is None:
        return {"status": "insufficient_context", "answer": INSUFFICIENT_ANSWER,
                "retrieved_chunk_count": len(context.chunks), "sources": [], "citation_version": 1, "claims": []}
    return cited_result(answer, cited_context)


def _abstention(count=0):
    return {"status": "insufficient_context", "answer": INSUFFICIENT_ANSWER,
            "retrieved_chunk_count": count, "sources": [], "citation_version": 1, "claims": []}


async def _verified_answer(database, owner_id, base_id, question, embedding_settings, *,
                           workers, workflow, generation_unneeded, deadline):
    parts = plan_question(question)
    if parts is None:
        if generation_unneeded is not None:
            generation_unneeded()
        return _abstention()
    generations = {}
    results = await retrieval.search_chunks(database, owner_id, base_id,
        SearchRequest(query=question, top_k=5), embedding_settings,
        workers=workers, workflow=workflow, generation_map=generations)
    context = build_candidate_context(results, generations=generations)
    if not context.passages:
        if generation_unneeded is not None:
            generation_unneeded()
        return _abstention()
    provider = get_gemini_provider()
    draft = await provider.answer(question, context.citations, deadline=deadline)
    if draft is None:
        return _abstention(len(context.passages))
    payload = await provider.verify(question, draft, context, parts, deadline=deadline)
    try:
        decision = validate_verification(payload, draft_claims=draft,
                                         question_parts=parts, context=context)
    except VerificationMalformed:
        raise GeminiFailure("Answer verification is temporarily unavailable.") from None
    if not decision.accepted:
        return _abstention(len(context.passages))
    return cited_result(draft, context.citations)


def supporting_sources(context):
    """Context membership, not model-generated or sentence-level attribution."""
    sources, seen = [], set()
    for chunk in context.chunks:
        key = (chunk["document_id"], chunk["page_start"], chunk["page_end"])
        if key not in seen:
            sources.append({field: chunk[field] for field in
                            ("document_id", "source_filename", "page_start", "page_end")})
            seen.add(key)
    return sources
