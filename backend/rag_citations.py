"""Request-local references resolved only from final validated context."""
import json
from dataclasses import dataclass, field
from pydantic import TypeAdapter
from rag_context import RAGFailure
from rag_schemas import AnswerClaim, AskResponse, SupportingSource, plain_answer


@dataclass(frozen=True)
class CitationContext:
    serialized: str = field(repr=False)
    chunks: tuple = field(repr=False)
    sources: tuple = field(repr=False)
    included_chunk_ids: tuple = field(repr=False)


def citation_context(context):
    sources, chunk_ids, passages, identifiers = [], [], [], {}
    for chunk in context.chunks:
        # Revalidate the public mapping at this boundary; never repair provenance.
        source = SupportingSource.model_validate({key: chunk[key] for key in
            ("document_id", "source_filename", "page_start", "page_end")})
        key = (source.document_id, source.page_start, source.page_end)
        if key not in identifiers:
            identifiers[key] = len(sources) + 1
            sources.append(source.model_copy(update={"citation_id": identifiers[key]}))
            chunk_ids.append([])
        identifier = identifiers[key]
        if sources[identifier-1].source_filename != source.source_filename:
            raise RAGFailure("Source metadata is unavailable.")
        chunk_ids[identifier-1].append(chunk["chunk_id"])
        passages.append({"citation_id": identifier, "source_filename": source.source_filename,
                         "page_start": source.page_start, "page_end": source.page_end, "text": chunk["text"]})
    serialized = json.dumps(passages, ensure_ascii=False, separators=(",", ":"))
    serialized.encode("utf-8")
    # Replace two internal ObjectIds with one small integer: no extra evidence
    # budget and no re-selection. The original serialized context was budgeted.
    if len(serialized) > len(context.serialized) or len(sources) > 5:
        raise RAGFailure("Citation context is unavailable.")
    return CitationContext(serialized, context.chunks, tuple(sources), tuple(tuple(ids) for ids in chunk_ids))


def validate_claims(values, context):
    claims = TypeAdapter(list[AnswerClaim]).validate_python(values, strict=True)
    allowed = {source.citation_id for source in context.sources}
    if not 1 <= len(claims) <= 12 or len(plain_answer(claims)) > 4000:
        raise RAGFailure("Invalid generated claims.")
    if any(not set(claim.citation_ids) <= allowed for claim in claims):
        raise RAGFailure("Invalid generated references.")
    return claims


def cited_result(values, context):
    claims = validate_claims(values, context)
    used = {identifier for claim in claims for identifier in claim.citation_ids}
    result = AskResponse(status="answered", answer=plain_answer(claims), citation_version=1,
        claims=claims, retrieved_chunk_count=len(context.chunks),
        sources=[source for source in context.sources if source.citation_id in used])
    return result.model_dump()
