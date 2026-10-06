"""Phase16A audit replay. No inference, downloads, cloud clients, or production writes.

The locally inspected environment has no suitable cross encoder. Candidate
metrics stay null, never populated from unit-test/fabricated relevance scores.
"""
import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import identifier
from evaluation.multi_query import inputs, replay, confusion
from evaluation.phase13 import measured_rankings
from evaluation.retrieval_metrics import covered, expected, retrieval_metrics
from evaluation.reranking import ADOPTION_CRITERIA
from rag_context import build_context

ROOT = Path(__file__).resolve().parent
SETTINGS = SimpleNamespace(top_k=5, min_relevance_score=.50, min_evidence_score=.30, max_context_chars=12000)
HISTORICAL = tuple(f"{name}.{ext}" for name in
    ("fixture", "local-model", "phase13-comparison", "phase15a-multi-query") for ext in ("json", "md"))
PRODUCTION = ("retrieval.py", "vector_store.py", "rag.py", "rag_context.py", "rag_citations.py",
              "rag_routes.py", "gemini_provider.py", "config.py", "requirements.txt")


def cache_inventory():
    """Inspect known cache roots, not arbitrary personal directories or .env.

    A classification head alone is not proof of a suitable relevance model.
    Report potential local candidates for review; never initialize/download one.
    Paths and unrelated environment values are deliberately absent from output.
    """
    hf_home = Path(os.environ.get("HF_HOME", Path(os.environ.get("XDG_CACHE_HOME", Path.home()/".cache"))/"huggingface"))
    roots = {"project_embedding_cache": ROOT.parent/".cache/embeddings",
             "project_model_cache": ROOT.parents[1]/".cache",
             "huggingface_hub": Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HUGGINGFACE_HUB_CACHE", hf_home/"hub")))}
    for key in ("SENTENCE_TRANSFORMERS_HOME", "TRANSFORMERS_CACHE"):
        if os.environ.get(key):
            roots[key.lower()] = Path(os.environ[key])
    output = []
    for label, root in roots.items():
        models = []
        if root.is_dir():
            for model in sorted(root.glob("models--*")):
                snapshots = list((model/"snapshots").glob("*/config.json"))
                architectures, weights, tokenizer = set(), False, False
                for config in snapshots:
                    data = json.loads(config.read_text(encoding="utf-8"))
                    architectures.update(data.get("architectures", []))
                    weights |= any((config.parent/name).exists() for name in ("model.safetensors", "pytorch_model.bin", "model.safetensors.index.json"))
                    tokenizer |= any((config.parent/name).exists() for name in ("tokenizer.json", "vocab.txt", "tokenizer.model"))
                models.append(dict(name=model.name.removeprefix("models--").replace("--", "/"),
                    architectures=sorted(architectures), weights_present=weights, tokenizer_present=tokenizer,
                    potential_sequence_scorer=weights and tokenizer and any("ForSequenceClassification" in a for a in architectures)))
        output.append(dict(location=label, exists=root.is_dir(), models=models))
    return dict(libraries={name:version(name) for name in ("sentence-transformers", "torch")},
        roots=output, suitable_model_verified=False,
        potential_candidates_found=any(model["potential_sequence_scorer"] for root in output for model in root["models"]),
        scope="Known project and configured/default Hugging Face caches only; no whole-disk search")


def snapshot_hashes():
    return {name:hashlib.sha256((ROOT/"reports"/name).read_bytes()).hexdigest() for name in HISTORICAL}


def audit_cases(cases, rankings):
    details = []
    for case in cases:
        hits = rankings[case["id"]]
        context = build_context(hits, SETTINGS)
        unbounded = build_context(hits, SimpleNamespace(**{**vars(SETTINGS), "max_context_chars":1000000}))
        accepted = bool(hits and hits[0]["score"]>=.50)
        details.append(dict(id=case["id"], question=case["question"], category=case["category"],
            answerable=case["expected_answerable"], expected_evidence=case["expected_sources"],
            top_cosine=hits[0]["score"] if hits else None, accepted=accepted,
            required_evidence_present_at_5=bool(expected(case)) and covered(case,hits)==expected(case),
            complete=bool(expected(case)) and covered(case,context.chunks)==expected(case),
            context_count=len(context.chunks), context_characters=len(context.serialized),
            context_sources=[{key:h[key] for key in ("source_filename","page_start","page_end")} for h in context.chunks],
            budget_excluded=len(unbounded.chunks)-len(context.chunks),
            candidates=[dict(original_rank=rank, cosine=h["score"], document=h["source_filename"],
                page_start=h["page_start"],page_end=h["page_end"],chunk_id=h["chunk_id"],
                required=bool(covered(case,[h])),reranker_score=None,reranker_rank=None)
                for rank,h in enumerate(hits,1)],
            reranker_decision=None, reranker_complete_evidence=None,
            diagnosis=("Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure."
                       if case["expected_answerable"] and not accepted else
                       "Accepted topical similarity does not establish support for the requested fact/policy."
                       if not case["expected_answerable"] and accepted else "Baseline decision preserved; second-stage behavior not measured.")))
    accepted_rows=[row for row in details if row["accepted"]]
    return dict(case_count=len(cases), candidate_pairs_available=sum(len(r["candidates"]) for r in details),
        ranking=retrieval_metrics(cases,rankings),decision=confusion(details),
        complete_evidence=sum(r["complete"] for r in details),
        multi_complete=sum(r["complete"] and r["category"]=="multi_chunk" for r in details),
        context=dict(average_chunks_accepted=mean(r["context_count"] for r in accepted_rows),
            maximum_chunks=max(r["context_count"] for r in details),
            average_serialized_characters_accepted=mean(r["context_characters"] for r in accepted_rows),
            maximum_serialized_characters=max(r["context_characters"] for r in details),
            budget_affected=[r["id"] for r in details if r["budget_excluded"]]), cases=details)


def build_report(inventory):
    cases,rankings,_ = measured_rankings()
    baseline = audit_cases(cases,rankings)
    _,_,_,safety=inputs()
    measured = replay(json.loads((ROOT/"phase15a_rankings.json").read_text(encoding="utf-8")))
    safety_rankings = {case["id"]:measured[identifier(case["question"])] for case in safety}
    if (baseline["decision"]["fn"]!=8 or baseline["decision"]["fp"]!=2 or baseline["complete_evidence"]!=14 or baseline["multi_complete"]!=4):
        raise ValueError("Historical baseline mismatch; investigate before evaluation")
    return dict(phase="16A",dataset_version="1.0.0",recommendation="C. FURTHER EVALUATION REQUIRED",
        status="model_based_experiment_not_run",
        blocker=("Potential cached sequence classifier requires suitability review; no model was evaluated." if inventory["potential_candidates_found"] else
                 "No suitable local cross-encoder found in inspected cache locations; model-based experiment stopped without download."),
        environment=inventory,adoption_criteria=ADOPTION_CRITERIA,
        baseline=baseline,safety_baseline=audit_cases(safety,safety_rankings),
        policies_proposed={"A":"Unchanged cosine 0.50 primary / 0.30 evidence; production context builder",
            "B":"Any reranker score >= model-specific threshold; keep all qualifying candidates in reranked order",
            "C":"B AND original strongest cosine >=0.50; conservative control, cannot recover original gate false negatives"},
        thresholds_evaluated=[],reranker_model=None,device_used=None,pairs_scored=0,
        inference_seconds=None,average_reranking_seconds=None,reranked_metrics=None,
        score_distributions=None,candidate_policies=[],
        historical_report_sha256=snapshot_hashes(),
        production_sha256={name:hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest() for name in PRODUCTION},
        unsupported_rationales={"unsupported-refresh":"Corpus describes access-token expiration, not refresh-token retention; access and refresh tokens are distinct.",
            "unsupported-rotation":"Corpus describes signature verification with a configured secret, not signing-key rotation; other session/API-key timings are distractors."},
        limitations=["No measured second-stage scores, ranking changes, threshold calibration, recovered cases, or inference cost.",
            "Unit-test fake scalar scores verify mechanics only and are never benchmark results.",
            "Cross-encoder relevance is not calibrated probability, factual confidence, or proof that every question part is supported.",
            "Non-required candidates are not automatically harmful; ground truth does not label every possible supporting passage.",
            "No Phase16B design without measured adoption evidence; no public endpoint or production integration."])


def markdown(report):
    baseline=report["baseline"]
    lines=["# Phase 16A — second-stage relevance evaluation", "", report["recommendation"], "",report["blocker"], "",
        "No reranker inference occurred. All second-stage results are **not measured**, not zero. Only historical measured MiniLM scores are replayed. No download or external services.","",
        "## Baseline", "",f"Ranking: `{baseline['ranking']}`", "",f"Decision: `{baseline['decision']}`", "",
        f"Complete evidence: {baseline['complete_evidence']}/22; multi-chunk: {baseline['multi_complete']}/5. Context: `{baseline['context']}`.","",
        "## Model availability", "", "Installed sentence-transformers/torch: "+str(report["environment"]["libraries"]), ""]
    for root in report["environment"]["roots"]:
        lines.append(f"- {root['location']}: exists={root['exists']}; models="+str(root["models"]))
    lines += ["", "Inventory is limited to known caches, not a claim about every file on the machine. The cached all-MiniLM-L6-v2 bi-encoder is not a trained cross-encoder; attaching an untrained classifier would not be a valid experiment.","",
        "## Predeclared criteria and policies", "",json.dumps(report["adoption_criteria"],indent=2),"",
        *[f"- {key}: {value}" for key,value in report["policies_proposed"].items()],"",
        "No model-specific threshold was guessed. A future authorized measurement must establish native score semantics and a bounded sweep before selection. Cosine and cross-encoder scores must never be added or treated as comparable. No criteria can be assessed for adoption yet.","",
        "## Eight answerable false negatives and two existing false positives", ""]
    for row in baseline["cases"]:
        if row["answerable"]==row["accepted"]:
            continue
        lines += [f"### {row['id']}","",row["question"],"",f"Expected: {row['expected_evidence']}. Original decision: {row['accepted']}; complete evidence: {row['complete']}.","",row["diagnosis"],"",
            report["unsupported_rationales"].get(row["id"],"All required pages present within top five: "+str(row["required_evidence_present_at_5"])),"",
            "| Original rank | Source | Cosine | Required | Reranker score/rank |", "|---|---|---|---|---|"]
        for hit in row["candidates"]:
            lines.append(f"| {hit['original_rank']} | {hit['document']} p{hit['page_start']}–{hit['page_end']} | {hit['cosine']:.8f} | {hit['required']} | Not measured |")
    lines += ["", "## All thirteen unsupported cases", "", "| Case | Top cosine | Original accepted | Second-stage decision / corrected or new FP |", "|---|---|---|---|"]
    for row in baseline["cases"]:
        if not row["answerable"]:
            lines.append(f"| {row['id']} | {row['top_cosine']:.8f} | {row['accepted']} | Not measured |")
    safety=report["safety_baseline"]
    lines += ["", "## Separate Phase15 safety cases", "",f"Baseline decision: `{safety['decision']}`. Complete evidence {safety['complete_evidence']}/4. No subqueries used. No reranker safety conclusion is possible.","",
        "| Case | Question | Original top score | Original accepted | Second-stage outcome |", "|---|---|---|---|---|"]
    for row in safety["cases"]:
        lines.append(f"| {row['id']} | {row['question']} | {row['top_cosine']:.8f} | {row['accepted']} | Not measured |")
    lines += ["", "## Scope, cost, and limitations", "",
        f"Fixed candidate sets: {baseline['candidate_pairs_available']} original-benchmark pairs, {safety['candidate_pairs_available']} safety pairs; at most five per question. **Zero reranker pairs scored**; device, latency, score distributions, threshold sweep, promoted/demoted evidence, and candidate completeness are not measured.","",
        "Test-only infrastructure checks scalar validation, identity preservation, stable sorting, Hit@5 invariance, multipage retention, decision accounting, and complete-chunk budget packing. Fake scores are never reported as measured relevance. No useful/harmful reranking example can be asserted without a model.","",
        "The eight false negatives are primary-gate losses after successful candidate retrieval. The two existing false positives show topical similarity without ground-truth support. Reranking may or may not fix this; availability currently prevents testing that hypothesis.","",
        "Future citation interaction: only final selected evidence may enter the unchanged Phase14 server-assigned citation mapping. A scorer returns scalars only; it must never invent identities, filenames, pages, or citations. History remains a snapshot, never retrieval context.","",
        "Production .50/.30, top-five, 12000 budget, MiniLM/384, single-query retrieval, citations, and frontend remain unchanged. Historical reports are fingerprinted and never rewritten. No Phase16B plan is proposed under recommendation C. Phase17 is not started.","",
        "Run `python -B backend/evaluation/phase16a.py` for this model-free audit replay. The command blocks socket connections and writes only the two Phase16 reports. Tests never load a model. A reranker must be made available separately under explicit authorization before actual measurement; this command will not download or execute one.","",
        *report["limitations"],""]
    return "\n".join(lines)


def main():
    with patch("socket.socket.connect", side_effect=RuntimeError("Offline evaluation")), patch("socket.socket.connect_ex", side_effect=RuntimeError("Offline evaluation")):
        report=build_report(cache_inventory())
        (ROOT/"reports/phase16a-reranking.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        (ROOT/"reports/phase16a-reranking.md").write_text(markdown(report),encoding="utf-8")
    print(report["recommendation"])
    print(report["blocker"])
    print("Baseline:",report["baseline"]["decision"],"complete",report["baseline"]["complete_evidence"],"multi",report["baseline"]["multi_complete"])


if __name__=="__main__":
    main()
