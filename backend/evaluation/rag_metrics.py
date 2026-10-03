"""Relevance acceptance is not answer confidence or semantic entailment."""
from statistics import mean, median
import math
from .retrieval_metrics import valid_hits


def top_score(hits):
    values = valid_hits(hits)
    return max((h["score"] for h in values), default=None)


def decision_metrics(cases, rankings, threshold):
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid threshold")
    counts = {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    for case in cases:
        score = top_score(rankings[case["id"]])
        accepted = score is not None and score >= threshold
        key = ("tp" if accepted else "fn") if case["expected_answerable"] else ("fp" if accepted else "tn")
        counts[key] += 1
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision = tp/(tp+fp) if tp+fp else 0.0
    recall = tp/(tp+fn) if tp+fn else 0.0
    return {"value": threshold, **counts, "precision": precision, "recall": recall,
            "f1": 2*precision*recall/(precision+recall) if precision+recall else 0.0}


def threshold_sweep(cases, rankings):
    return [decision_metrics(cases, rankings, step/100) for step in range(20, 81, 5)]


def score_distribution(cases, rankings, answerable):
    scores = [top_score(rankings[c["id"]]) for c in cases if c["expected_answerable"] == answerable]
    present = [s for s in scores if s is not None]
    return {"count": len(scores), "missing": len(scores)-len(present),
            "minimum": min(present) if present else None, "maximum": max(present) if present else None,
            "mean": mean(present) if present else None, "median": median(present) if present else None}
