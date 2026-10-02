"""Read-only question answering using the existing validated retrieval path."""
import retrieval
from search_schemas import SearchRequest
from rag_context import build_context
from gemini_provider import get_gemini_provider

INSUFFICIENT_ANSWER = "I couldn't find enough relevant information in this Knowledge Base to answer that question."


async def answer_question(database, owner_id, base_id, question, embedding_settings, settings):
    results = await retrieval.search_chunks(database, owner_id, base_id,
        SearchRequest(query=question, top_k=settings.top_k), embedding_settings)
    context = build_context(results, settings)
    if not context.chunks:
        return {"status": "insufficient_context", "answer": INSUFFICIENT_ANSWER, "retrieved_chunk_count": 0}
    answer = await get_gemini_provider().answer(question, context)
    if answer is None:
        return {"status": "insufficient_context", "answer": INSUFFICIENT_ANSWER,
                "retrieved_chunk_count": len(context.chunks)}
    return {"status": "answered", "answer": answer, "retrieved_chunk_count": len(context.chunks)}
