"""Verified-RAG selection, question planning and deterministic evidence checks.

Candidate inputs must come from retrieval.search_chunks after its ownership,
generation, point-ID and authoritative-payload checks. ``generations`` must be
server-owned metadata keyed by chunk ID from the same validated snapshot. This
module does not replace those checks or establish snapshot freshness.

Question parts are a caller-owned plan, fixed independently of verifier output.
Checking all supplied parts cannot prove that the plan captured the full question.
Exact quotation and reference consistency cannot prove semantic entailment.
"""
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Annotated, Literal, Mapping

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rag_citations import CitationContext, citation_context, validate_claims
from rag_context import Context, RAGFailure

MAX_CANDIDATES = 5
MAX_CONTEXT_CHARS = 12000


def plan_question(question):
    """Conservative surface-syntax plan, independent of drafts and providers.

    Each part retains its entire interrogative clause. Only explicit conjunctions
    followed by another interrogative are split. Unresolved coordination, lists,
    embedded questions and supplementary requests fail closed. This deliberately
    rejects some valid natural-language questions; it is not a semantic parser.
    A verifier still has to assess coverage of the full original question.
    """
    if not isinstance(question, str) or not 1 <= len(question) <= 1000:
        return None
    text = question.strip()
    if not text.endswith("?") or text.count("?") != 1:
        return None
    interrogative = r"(?:(?:on|at|in)\s+)?(?:who|what|where|when|which|how)\b"
    clauses = re.split(r",?\s+and\s+(?=" + interrogative + ")", text[:-1], flags=re.I)
    if not 1 <= len(clauses) <= 12:
        return None
    for clause in clauses:
        if not re.match(r"^" + interrogative, clause, re.I):
            return None
        if len(re.findall(interrogative, clause, re.I)) != 1:
            return None
        if re.search(r"\b(and|or|with|including|include|also|plus|explain|describe|list|compare|except|unless|if|then|provide|give|show|tell|return|summarize|add|mention|along|together|besides|respectively)\b",
                     clause, re.I):
            return None
        if re.match(r"^how\b", clause, re.I) and not re.match(r"^how\s+(many|much|long)\b", clause, re.I):
            return None
        if any(not (c.isalnum() or c in " '-") for c in clause) or len(clause.split()) < 3:
            return None
    return tuple(QuestionPart(part_id=i, text=clause.strip() + "?")
                 for i, clause in enumerate(clauses, 1))


class VerificationMalformed(ValueError):
    """Unusable data with a safe internal reason, never evidence abstention."""

    def __init__(self, message="Verification response is unusable.", *, reason="invalid_structure"):
        super().__init__(message)
        self.reason = reason


class _InvalidVerification(ValueError):
    pass


def _invalid(reason):
    raise _InvalidVerification(reason)



@dataclass(frozen=True)
class Passage:
    passage_id: str
    chunk_id: str
    generation: str
    document_id: str
    page_start: int
    page_end: int
    citation_id: int
    text: str = field(repr=False)


@dataclass(frozen=True)
class CandidateContext:
    serialized: str = field(repr=False)
    passages: tuple[Passage, ...] = field(repr=False)
    citations: CitationContext = field(repr=False)


def _record(value, generations):
    if not isinstance(value, dict):
        raise ValueError
    score = value.get("score")
    if type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1:
        raise ValueError
    for key in ("document_id", "chunk_id"):
        if not isinstance(value.get(key), str) or not ObjectId.is_valid(value[key]):
            raise ValueError
    generation = generations.get(value["chunk_id"])
    if not isinstance(generation, str) or not ObjectId.is_valid(generation):
        raise ValueError
    if (type(value.get("page_start")) is not int or type(value.get("page_end")) is not int
            or not 1 <= value["page_start"] <= value["page_end"]):
        raise ValueError
    if any(not isinstance(value.get(key), str) or not value[key].strip()
           for key in ("text", "source_filename")):
        raise ValueError
    filename = value["source_filename"]
    if any(c in filename for c in "/\\:") or any(ord(c) < 32 for c in filename):
        raise ValueError
    record = {key: value[key] for key in
              ("document_id", "chunk_id", "source_filename", "page_start", "page_end", "text")}
    json.dumps(record, ensure_ascii=False).encode("utf-8")
    return score, record, generation


def _context(records):
    chunks = tuple(record for _, record, _ in records)
    raw = json.dumps(chunks, ensure_ascii=False, separators=(",", ":"))
    citations = citation_context(Context(raw, chunks))
    passages, envelope = [], []
    for index, ((_, record, generation), public) in enumerate(
            zip(records, json.loads(citations.serialized)), start=1):
        identifier = f"p{index}"
        passages.append(Passage(identifier, record["chunk_id"], generation,
                                record["document_id"], record["page_start"],
                                record["page_end"], public["citation_id"], record["text"]))
        envelope.append({"passage_id": identifier, **public})
    serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return CandidateContext(serialized, tuple(passages), citations)


def build_candidate_context(results, *, generations: Mapping[str, str]) -> CandidateContext:
    """Select complete top-five candidates within the final 12,000-char envelope.

    Scores order candidates, including negative cosine scores. No answerability
    thresholds apply. Oversized chunks are skipped, never sliced. Unusable input
    or inconsistent provenance fails closed rather than manufacturing metadata.
    """
    try:
        if not isinstance(results, list) or not isinstance(generations, Mapping):
            raise ValueError
        records = [_record(value, generations) for value in results[:MAX_CANDIDATES]]
        records.sort(key=lambda item: -item[0])
        selected, seen = [], {}
        context = _context([])
        for item in records:
            identifier = item[1]["chunk_id"]
            if identifier in seen:
                if item[1:] != seen[identifier][1:]:
                    raise ValueError
                continue
            seen[identifier] = item
            candidate = _context([*selected, item])
            if len(candidate.serialized) <= MAX_CONTEXT_CHARS:
                selected.append(item)
                context = candidate
        return context
    except (ValueError, TypeError, KeyError, OverflowError):
        raise RAGFailure("Experimental candidate context is unavailable.") from None


Identifier = Annotated[int, Field(strict=True, ge=1, le=12)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class QuestionPart(StrictRecord):
    part_id: Identifier
    text: str = Field(min_length=1, max_length=1000)


class EvidenceSpan(StrictRecord):
    passage_id: str = Field(pattern=r"^p[1-5]$")
    start: int = Field(ge=0, le=MAX_CONTEXT_CHARS)
    end: int = Field(ge=1, le=MAX_CONTEXT_CHARS)
    quote: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start or not self.quote.strip():
            raise ValueError("Invalid evidence span")
        return self


class ClaimVerdict(StrictRecord):
    claim_id: Identifier
    verdict: Literal["supported", "unsupported", "contradicted"]
    evidence: list[EvidenceSpan] = Field(max_length=5)


class PartCoverage(StrictRecord):
    part_id: Identifier
    status: Literal["covered", "missing", "explicitly_absent", "contradicted"]
    claim_ids: list[Identifier] = Field(max_length=12)
    evidence: list[EvidenceSpan] = Field(max_length=5)


class VerificationPayload(StrictRecord):
    claims: list[ClaimVerdict] = Field(min_length=1, max_length=12)
    coverage: list[PartCoverage] = Field(min_length=1, max_length=12)
    outcome: Literal["verified", "insufficient_evidence", "contradicted"]


class ProviderEvidence(StrictRecord):
    passage_id: str = Field(pattern=r"^p[1-5]$")
    quote: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARS)


class ProviderClaimVerdict(ClaimVerdict):
    evidence: list[ProviderEvidence] = Field(max_length=5)


class ProviderPartCoverage(PartCoverage):
    evidence: list[ProviderEvidence] = Field(max_length=5)


class ProviderVerificationPayload(VerificationPayload):
    claims: list[ProviderClaimVerdict] = Field(min_length=1, max_length=12)
    coverage: list[ProviderPartCoverage] = Field(min_length=1, max_length=12)


def resolve_verification_quotes(payload, context):
    """Resolve unique exact quotes only within their server-owned passage."""
    try:
        if not isinstance(context, CandidateContext):
            _invalid("invalid_evidence_reference")
        value = ProviderVerificationPayload.model_validate(payload).model_dump()
        passages = {p.passage_id: p for p in context.passages}
        for record in [*value["claims"], *value["coverage"]]:
            for evidence in record["evidence"]:
                passage = passages.get(evidence["passage_id"])
                if passage is None:
                    _invalid("unknown_passage_id")
                quote = evidence["quote"]
                if not quote.strip():
                    _invalid("missing_quote")
                start = passage.text.find(quote)
                if start < 0:
                    _invalid("missing_quote")
                # Advance by one code point to detect overlapping occurrences.
                if passage.text.find(quote, start + 1) >= 0:
                    _invalid("ambiguous_quote")
                evidence.update(start=start, end=start + len(quote))
        return VerificationPayload.model_validate(value).model_dump()
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        reason = str(error) if isinstance(error, _InvalidVerification) else "invalid_evidence_reference"
        if isinstance(error, ValidationError) and any(
                e["loc"][-1:] == ("quote",) and e["type"] in {"missing", "string_too_short"}
                for e in error.errors(include_input=False)):
            reason = "missing_quote"
        logging.getLogger(__name__).warning("Verification rejected: %s", reason)
        raise VerificationMalformed(reason=reason) from None


@dataclass(frozen=True)
class VerificationDecision:
    accepted: bool
    outcome: str


def _unique(values, reason="duplicate_reference"):
    if len(values) != len(set(values)):
        _invalid(reason)
    return set(values)


def _evidence(spans, passages):
    _unique([(s.passage_id, s.start, s.end) for s in spans])
    references = set()
    for span in spans:
        passage = passages.get(span.passage_id)
        if passage is None:
            _invalid("unknown_passage_id")
        if span.end > len(passage.text):
            _invalid("quote_span_out_of_bounds")
        if passage.text[span.start:span.end] != span.quote:
            _invalid("quote_mismatch")
        references.add(passage.citation_id)
    return references


def validate_verification(payload, *, draft_claims, question_parts, context):
    """Return a valid acceptance/abstention; raise on malformed verification.

    Claim IDs are the server's one-based draft order. Span offsets count Python
    Unicode code points, with an exclusive end. Models cannot supply provenance.
    """
    try:
        if not isinstance(payload, dict) or not isinstance(context, CandidateContext):
            _invalid("invalid_structure")
        result = VerificationPayload.model_validate(payload)
        claims = validate_claims(draft_claims, context.citations)
        if not isinstance(question_parts, (list, tuple)) or not 1 <= len(question_parts) <= 12:
            _invalid("invalid_structure")
        parts = [QuestionPart.model_validate(p) for p in question_parts]
        if any(not part.text.strip() for part in parts):
            _invalid("invalid_structure")
        expected_parts = _unique([p.part_id for p in parts])
        expected_claims = set(range(1, len(claims) + 1))
        if _unique([v.claim_id for v in result.claims], "duplicate_verdict") != expected_claims:
            _invalid("claim_verdict_set_mismatch")
        if _unique([p.part_id for p in result.coverage], "duplicate_question_part") != expected_parts:
            _invalid("question_part_set_mismatch")
        passages = {p.passage_id: p for p in context.passages}
        verdicts = {v.claim_id: v for v in result.claims}
        for verdict in result.claims:
            references = _evidence(verdict.evidence, passages)
            declared = set(claims[verdict.claim_id - 1].citation_ids)
            if verdict.verdict == "supported" and (not references or references != declared):
                _invalid("citation_mismatch")
            if verdict.verdict == "contradicted" and not references:
                _invalid("citation_mismatch")
            if not references <= declared:
                _invalid("citation_mismatch")
        covered_claims = set()
        for coverage in result.coverage:
            identifiers = _unique(coverage.claim_ids)
            if not identifiers <= expected_claims:
                _invalid("invalid_claim_coverage")
            references = _evidence(coverage.evidence, passages)
            if coverage.status == "covered":
                if not identifiers or any(verdicts[i].verdict != "supported" for i in identifiers):
                    _invalid("invalid_claim_coverage")
                allowed = {c for i in identifiers for c in claims[i - 1].citation_ids}
                if not references or not references <= allowed:
                    _invalid("citation_mismatch")
                covered_claims.update(identifiers)
            elif identifiers or (coverage.status == "missing" and references):
                _invalid("invalid_claim_coverage")
            elif coverage.status in {"explicitly_absent", "contradicted"} and not references:
                _invalid("invalid_claim_coverage")
        contradiction = (any(v.verdict == "contradicted" for v in result.claims)
                         or any(p.status == "contradicted" for p in result.coverage))
        accepted = (all(v.verdict == "supported" for v in result.claims)
                    and all(p.status == "covered" for p in result.coverage))
        outcome = "contradicted" if contradiction else "verified" if accepted else "insufficient_evidence"
        if result.outcome != outcome or (accepted and covered_claims != expected_claims):
            _invalid("inconsistent_outcome_or_coverage")
        return VerificationDecision(accepted, outcome)
    except (ValueError, TypeError, KeyError, AttributeError, ValidationError) as error:
        reason = str(error) if isinstance(error, _InvalidVerification) else "invalid_structure"
        logging.getLogger(__name__).warning("Verification rejected: %s", reason)
        raise VerificationMalformed(reason=reason) from None
