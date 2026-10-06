"""Evaluation-only second-stage mechanics; no model loader, network, or retrieval.

The injected scorer accepts bounded (question, passage) pairs and returns one
finite scalar per pair. Unit-test scores are not model measurements.
"""
import json
import math
from numbers import Real
from statistics import mean, median

from evaluation.retrieval_metrics import covered, expected, valid_hits, retrieval_metrics
from evaluation.multi_query import confusion
from rag_context import Context

TOP_K, BUDGET = 5, 12000
FIELDS = ("document_id", "chunk_id", "source_filename", "page_start", "page_end", "text")
ADOPTION_CRITERIA = {
    "minimum_recovered_false_negatives": 4,
    "minimum_corrected_false_positives": 1,
    "maximum_new_false_positives": 1,
    "minimum_complete_evidence": 18,
    "minimum_multi_chunk_complete": 4,
    "minimum_precision": .875,
    "recall_must_exceed": 14 / 22,
    "maximum_context_characters": BUDGET,
    "deterministic_offline": True,
    "compute_cost": "Must be measured and reviewed; not assessable without a local model",
}


def finite_score(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("Expected a finite scalar relevance score")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError("Expected a finite scalar relevance score") from None
    if not math.isfinite(result):
        raise ValueError("Expected a finite scalar relevance score")
    return result


def rerank(question, candidates, scorer):
    """Preserve every trusted identity and cosine; assign separate second-stage rank.

    Reject malformed/duplicate/unbounded input instead of silently changing the
    candidate set. Ties retain original retrieval order. No unseen candidate can
    be introduced by the scorer, which returns scalars only.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question is required")
    if not isinstance(candidates, list) or len(candidates) > TOP_K:
        raise ValueError("At most five candidates are allowed")
    if len(valid_hits(candidates)) != len(candidates):
        raise ValueError("Invalid or duplicate candidate")
    if not candidates:
        return []
    scores = scorer([(question, hit["text"]) for hit in candidates])
    if not isinstance(scores, (list, tuple)) or len(scores) != len(candidates):
        raise ValueError("Scorer must return one scalar per candidate")
    rows = [{**hit, "original_rank": rank, "reranker_score": finite_score(score)}
            for rank, (hit, score) in enumerate(zip(candidates, scores), 1)]
    rows.sort(key=lambda hit: (-hit["reranker_score"], hit["original_rank"]))
    return [{**hit, "reranker_rank": rank} for rank, hit in enumerate(rows, 1)]


def select_context(rows, threshold, policy="replace", budget=BUDGET):
    """B: any reranker-qualified passage; C: B AND original cosine gate.

    All passages meeting the reranker threshold can enter, not only the winner.
    C is a conservative control that cannot recover primary false negatives.
    Threshold is in the scorer's native space, never added to cosine. Budget
    packing mirrors production complete-chunk JSON fields, preserving new order.
    """
    threshold = finite_score(threshold)
    if policy not in ("replace", "intersection") or type(budget) is not int or budget < 2:
        raise ValueError("Invalid experimental policy or budget")
    if len(rows) > TOP_K or len(valid_hits(rows)) != len(rows):
        raise ValueError("Invalid reranked candidates")
    scores = [finite_score(hit["reranker_score"]) for hit in rows]
    accepted = bool(scores and max(scores) >= threshold)
    if policy == "intersection":
        accepted = accepted and max((hit["score"] for hit in rows), default=-1) >= .50
    if not accepted:
        return False, Context("[]", ())
    chunks, serialized = [], "[]"
    for hit in rows:
        if hit["reranker_score"] < threshold:
            continue
        record = {key: hit[key] for key in FIELDS}
        trial = json.dumps([*chunks, record], ensure_ascii=False, separators=(",", ":"))
        trial.encode("utf-8")
        if len(trial) <= budget:
            chunks.append(record)
            serialized = trial
    return True, Context(serialized, tuple(chunks))


def distribution(values):
    values = sorted(finite_score(value) for value in values)
    if not values:
        return dict(count=0, minimum=None, maximum=None, mean=None, median=None, p10=None, p90=None)
    def percentile(fraction):
        position = (len(values) - 1) * fraction
        lo, hi = math.floor(position), math.ceil(position)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)
    return dict(count=len(values), minimum=values[0], maximum=values[-1],
                mean=mean(values), median=median(values), p10=percentile(.1), p90=percentile(.9))


def score_distributions(cases, rankings):
    groups = {key: [] for key in ("required_answerable", "non_required_answerable", "unsupported")}
    for case in cases:
        for hit in rankings[case["id"]]:
            group = ("required_answerable" if covered(case, [hit]) else "non_required_answerable") if case["expected_answerable"] else "unsupported"
            groups[group].append(hit["reranker_score"])
    return {key: distribution(values) for key, values in groups.items()}


def movement(cases, rankings):
    counts = dict(required_promoted=0, required_demoted=0, non_required_promoted=0, non_required_demoted=0)
    for case in cases:
        for hit in rankings[case["id"]]:
            difference = hit["original_rank"] - hit["reranker_rank"]
            if difference:
                prefix = "required" if covered(case, [hit]) else "non_required"
                counts[prefix + ("_promoted" if difference > 0 else "_demoted")] += 1
    return counts


def threshold_sweep(cases, rankings, baseline_contexts, thresholds, policy="replace", budget=BUDGET):
    """Pure mechanics. Thresholds must be declared for a specific measured model.

    No default threshold is guessed while no reranker is available. A passed
    relevance gate and usable bounded context are reported separately.
    """
    thresholds = sorted(set(finite_score(value) for value in thresholds))
    if not 1 <= len(thresholds) <= 41:
        raise ValueError("Supply between one and 41 thresholds")
    output = []
    for threshold in thresholds:
        details = []
        for case in cases:
            hits = rankings[case["id"]]
            accepted, context = select_context(hits, threshold, policy, budget)
            _, unlimited = select_context(hits, threshold, policy, 1000000)
            old = baseline_contexts[case["id"]]
            old_ids = {hit["chunk_id"] for hit in old.chunks}
            included_ids = {hit["chunk_id"] for hit in context.chunks}
            details.append(dict(id=case["id"], answerable=case["expected_answerable"], accepted=accepted,
                provider_eligible=bool(context.chunks), complete=bool(expected(case)) and covered(case, context.chunks)==expected(case),
                multi=case["category"]=="multi_chunk", chunks=len(context.chunks), characters=len(context.serialized),
                budget_excluded=len(unlimited.chunks)-len(context.chunks),
                added_non_required=sum(hit["chunk_id"] not in old_ids and not covered(case, [hit]) for hit in context.chunks),
                removed_required=sum(hit["chunk_id"] not in included_ids and bool(covered(case, [hit])) for hit in old.chunks)))
        accepted = [row for row in details if row["accepted"]]
        output.append(dict(threshold=threshold, policy=policy, decision=confusion(details),
            provider_eligible_decision=confusion([{**row, "accepted":row["provider_eligible"]} for row in details]),
            complete_evidence=sum(row["complete"] for row in details), multi_complete=sum(row["complete"] and row["multi"] for row in details),
            added_non_required=sum(row["added_non_required"] for row in details), removed_required=sum(row["removed_required"] for row in details),
            average_chunks_accepted=mean(row["chunks"] for row in accepted) if accepted else 0,
            maximum_chunks=max((row["chunks"] for row in details), default=0),
            average_characters_accepted=mean(row["characters"] for row in accepted) if accepted else 0,
            maximum_characters=max((row["characters"] for row in details), default=0),
            budget_affected=[row["id"] for row in details if row["budget_excluded"]], cases=details))
    return output
