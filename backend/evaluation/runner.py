"""Run explicitly: python -B backend/evaluation/runner.py [--mode local-model]."""
import argparse
import asyncio
import json
import sys
import hashlib
from importlib.metadata import version as package_version
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import load_dataset, CATEGORIES
from evaluation.adapters import retrieve_dataset
from evaluation.retrieval_metrics import retrieval_metrics, covered, expected, valid_hits
from evaluation.rag_metrics import decision_metrics, threshold_sweep, score_distribution, top_score
from evaluation.context_checks import context_probes
from config import RAGSettings, EmbeddingSettings
from rag_context import build_context
from rag import answer_question, supporting_sources


async def evaluate(mode="fixture"):
    version, chunks, cases, fixtures = load_dataset()
    rankings, dimension = await retrieve_dataset(chunks, cases, fixtures, mode)
    settings = RAGSettings(min_relevance_score=0.50, min_evidence_score=0.50,
                           max_context_chars=12000, top_k=5)  # Historical Phase 11 policy; no .env.
    details, call_counts = [], {"tp": 0, "tn": 0, "fp": 0, "fn": 0}
    for case in cases:
        hits = rankings[case["id"]]
        context = build_context(hits, settings)
        provider = SimpleNamespace(answer=AsyncMock(return_value="Synthetic provider response for decision testing only."))
        with patch("retrieval.search_chunks", AsyncMock(return_value=hits)), patch("rag.get_gemini_provider", return_value=provider):
            result = await answer_question(None, None, None, case["question"], EmbeddingSettings(), settings)
        called = provider.answer.await_count == 1
        key = ("tp" if called else "fn") if case["expected_answerable"] else ("fp" if called else "tn")
        call_counts[key] += 1
        sources = supporting_sources(context)
        details.append({"id": case["id"], "category": case["category"], "expected_answerable": case["expected_answerable"],
            "top_score": top_score(hits), "ranked_sources": [{"document": h["source_filename"], "page_start": h["page_start"],
                "page_end": h["page_end"], "score": h["score"]} for h in hits],
            "expected_sources": case["expected_sources"], "provider_called": called, "mock_status": result["status"],
            "context_characters": len(context.serialized), "context_chunk_count": len(context.chunks),
            "context_evidence_fraction": len(covered(case, context.chunks))/len(expected(case)) if expected(case) else None,
            "sources_match_context": all(any(all(c[k] == v for k,v in s.items()) for c in context.chunks) for s in sources)})
    probes = context_probes()
    dataset_digest = hashlib.sha256(b"".join((Path(__file__).parent/name).read_bytes()
        for name in ("corpus.json", "cases.json", "fixture_rankings.json"))).hexdigest()
    revision_file = Path(__file__).resolve().parents[1]/".cache"/"embeddings"/"models--sentence-transformers--all-MiniLM-L6-v2"/"refs"/"main"
    revision = revision_file.read_text(encoding="utf-8").strip() if mode == "local-model" and revision_file.is_file() else None
    if revision is not None and (len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision)):
        revision = None
    report = {"dataset_version": version, "dataset_sha256": dataset_digest, "mode": mode,
        "runtime": {"python": sys.version.split()[0], "qdrant-client": package_version("qdrant-client"),
                    "sentence-transformers": package_version("sentence-transformers"), "model_revision": revision},
        "measurement": "Authored fixture scores; engineering regression ONLY" if mode == "fixture" else "Cached MiniLM CPU inference + real in-memory Qdrant + production retrieval validation; NOT Cloud/Gemini evaluation",
        "case_count": len(cases), "corpus_chunk_count": len(chunks), "category_counts": dict(Counter(c["category"] for c in cases)),
        "embedding_model": EmbeddingSettings.model_name if mode == "local-model" else "mock (not real inference)",
        "dimension": dimension, "top_k": settings.top_k, "max_context_chars": settings.max_context_chars,
        "retrieval": retrieval_metrics(cases, rankings), "baseline_threshold": decision_metrics(cases, rankings, settings.min_relevance_score),
        "threshold_sweep": threshold_sweep(cases, rankings),
        "score_distributions": {"answerable": score_distribution(cases, rankings, True), "unsupported": score_distribution(cases, rankings, False)},
        "categories": {category: {"retrieval": retrieval_metrics([c for c in cases if c["category"] == category], rankings),
            "decision": decision_metrics([c for c in cases if c["category"] == category], rankings, settings.min_relevance_score)} for category in CATEGORIES},
        "context_probes": probes, "provider_call_confusion": call_counts,
        "context_all_evidence_count": sum(d["context_evidence_fraction"] == 1 for d in details),
        "failures": {"false_acceptance": [d["id"] for d in details if not d["expected_answerable"] and d["provider_called"]],
            "false_rejection": [d["id"] for d in details if d["expected_answerable"] and not d["provider_called"]],
            "incomplete_context_evidence": [d["id"] for d in details if d["expected_answerable"] and d["context_evidence_fraction"] < 1]},
        "cases": details,
        "tuning_decision": "Keep production 0.50. Synthetic results are diagnostic, not sufficient evidence for a global threshold change; evaluate independent held-out real-domain cases."}
    # Round reporting only, after decisions; avoid brittle inference float snapshots.
    return json.loads(json.dumps(report), parse_float=lambda value: round(float(value), 8))


def markdown(report):
    lines = ["# DeepDocs evaluation " + report["dataset_version"], "", report["measurement"], "",
             f"Cases: {report['case_count']}; chunks: {report['corpus_chunk_count']}; top_k: {report['top_k']}; context budget: {report['max_context_chars']}.",
             "", "## Retrieval", "", str(report["retrieval"]), "", "## Baseline", "", str(report["baseline_threshold"]),
             "", "## Threshold sweep", "", "| Threshold | TP | TN | FP | FN | Precision | Recall | F1 |", "|---|---|---|---|---|---|---|---|"]
    for row in report["threshold_sweep"]:
        lines.append("| " + " | ".join(str(row[k]) for k in ("value", "tp", "tn", "fp", "fn", "precision", "recall", "f1")) + " |")
    lines += ["", "## Score distributions", "", str(report["score_distributions"]), "", "## Categories", "",
              "| Category | Cases | Hit@1 | Hit@3 | Hit@5 | MRR | FP | FN |", "|---|---|---|---|---|---|---|---|"]
    for name, values in report["categories"].items():
        r,d = values["retrieval"], values["decision"]
        lines.append("| " + " | ".join(str(v) if v is not None else "N/A" for v in (name, report["category_counts"][name], r["hit_at_1"],r["hit_at_3"],r["hit_at_5"],r["mrr"],d["fp"],d["fn"])) + " |")
    lines += ["", "## Context and decisions", "", str(report["context_probes"]), "",
              "All expected evidence survives context for " + str(report["context_all_evidence_count"]) + " answerable cases.",
              "Provider-call confusion: " + str(report["provider_call_confusion"]), "", "## Observed failures", "", str(report["failures"]),
              "", "## Recommendation and limits", "", report["tuning_decision"], "",
              "Scores are cosine similarity, not confidence. Mock provider calls measure eligibility, not answer correctness. Related-but-unsupported questions can pass the guard; no threshold proves entailment. Multi-chunk Hit@k counts any expected page; all-evidence coverage is reported separately. The small synthetic benchmark is not universal accuracy, a PDF extraction benchmark, a production Cloud test, or an LLM answer-quality judge.", ""]
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "local-model"), default="fixture")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2]/".verification"/"evaluation")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if not output.is_relative_to(project):
        parser.error("Evaluation output must stay inside this project.")
    report = await evaluate(args.mode)
    output.mkdir(parents=True, exist_ok=True)
    (output/(args.mode+".json")).write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    (output/(args.mode+".md")).write_text(markdown(report), encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("mode", "case_count", "retrieval", "baseline_threshold", "score_distributions", "failures")}, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("Evaluation failed safely. Check installed dependencies/local model cache; no remote fallback was attempted.", file=sys.stderr)
        raise SystemExit(1) from None
