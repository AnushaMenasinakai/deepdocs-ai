"""Pure metrics on ranked validated hits. Unsupported cases are not ranking denominators."""
import math


def valid_hits(hits):
    """Defensive metric input boundary; production retrieval still verifies authority."""
    result, seen = [], set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        score = hit.get("score")
        if type(score) not in (float, int) or not math.isfinite(score) or not -1 <= score <= 1:
            continue
        if (type(hit.get("page_start")) is not int or type(hit.get("page_end")) is not int
                or not 1 <= hit["page_start"] <= hit["page_end"]):
            continue
        if any(not isinstance(hit.get(k), str) or not hit[k].strip() for k in ("document_id", "chunk_id", "source_filename", "text")):
            continue
        if hit["chunk_id"] in seen:
            continue
        seen.add(hit["chunk_id"])
        result.append(hit)
    return result  # Input rank is authoritative; never reorder to improve metrics.


def expected(case):
    return {(s["document"], s["page"]) for s in case["expected_sources"]}


def covered(case, hits):
    return {source for source in expected(case) if any(
        h["source_filename"] == source[0] and h["page_start"] <= source[1] <= h["page_end"] for h in hits)}


def retrieval_metrics(cases, rankings):
    answerable = [case for case in cases if case["expected_answerable"]]
    n = len(answerable)
    totals = {"hit_at_1": 0, "hit_at_3": 0, "hit_at_5": 0, "mrr": 0, "all_evidence_at_5": 0}
    for case in answerable:
        hits = valid_hits(rankings[case["id"]])[:5]
        for k in (1, 3, 5):
            totals[f"hit_at_{k}"] += bool(covered(case, hits[:k]))
        first = next((i for i, h in enumerate(hits, 1) if covered(case, [h])), None)
        totals["mrr"] += 1 / first if first else 0
        totals["all_evidence_at_5"] += covered(case, hits) == expected(case)
    return {"answerable_count": n, "unsupported_count": len(cases)-n,
            "no_valid_hit_count": sum(not valid_hits(rankings[c["id"]]) for c in cases),
            **{key: value/n if n else None for key, value in totals.items()}}
