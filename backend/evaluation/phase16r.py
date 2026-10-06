"""Real Phase16A-R scores; replay never loads a model or contacts a service."""
import argparse
import hashlib
import json
import math
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation import cross_encoder_local as adapter
from evaluation.dataset import identifier
from evaluation.multi_query import inputs, replay
from evaluation.phase13 import measured_rankings
from evaluation.phase16a import ROOT, SETTINGS, audit_cases, snapshot_hashes
from evaluation.reranking import (ADOPTION_CRITERIA, rerank, threshold_sweep,
    select_context, score_distributions, movement, finite_score)
from evaluation.retrieval_metrics import retrieval_metrics, covered, expected
from rag_context import build_context

SCORES = ROOT/"phase16a_real_scores.json"
TIMING = ROOT/"reports/phase16a-reranking-timing.json"
# Declared before inference. Unit logit grid spans development scores; these
# three offsets avoid an unconstrained two-threshold combinatorial search.
EVIDENCE_OFFSETS = (0.,2.,4.)
MAX_GRID_POINTS = 41
PRACTICAL_SECONDS_PER_QUESTION = 2.


def historical_hashes():
    return {**snapshot_hashes(), **{name:hashlib.sha256((ROOT/"reports"/name).read_bytes()).hexdigest()
        for name in ("phase16a-reranking.json","phase16a-reranking.md")}}


def candidate_sets():
    cases,ranking,_=measured_rankings()
    _,_,_,safety=inputs()
    measured=replay(json.loads((ROOT/"phase15a_rankings.json").read_text(encoding="utf-8")))
    safety_ranking={c["id"]:measured[identifier(c["question"])] for c in safety}
    return cases,ranking,safety,safety_ranking


def candidate_fingerprint():
    cases,hits,safety,safety_hits=candidate_sets()
    return hashlib.sha256(json.dumps([cases,hits,safety,safety_hits],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def measure():
    cases,hits,safety,safety_hits=candidate_sets()
    all_cases=cases+safety; all_hits={**hits,**safety_hits}
    pairs=[(c["question"],h["text"]) for c in all_cases for h in all_hits[c["id"]]]
    if len(pairs)!=225 or any(len(all_hits[c["id"]])!=5 for c in all_cases):
        raise ValueError("Historical candidate set changed")
    # No network fallback, including model initialization or telemetry.
    with patch("socket.socket.connect",side_effect=RuntimeError("Offline model evaluation")),patch("socket.socket.connect_ex",side_effect=RuntimeError("Offline model evaluation")):
        started=perf_counter(); model=adapter.load(); load_seconds=perf_counter()-started
        tokens=model.tokenizer([p[0] for p in pairs],[p[1] for p in pairs],truncation=False,padding=False)
        lengths=[len(ids) for ids in tokens["input_ids"]]
        started=perf_counter(); values=adapter.score_pairs(model,pairs); elapsed=perf_counter()-started
    offset=0; scored={}
    for c in all_cases:
        count=len(all_hits[c["id"]])
        scored[c["id"]]=[{"chunk_id":h["chunk_id"],"score":values[offset+i]} for i,h in enumerate(all_hits[c["id"]])]
        offset+=count
    path=adapter.snapshot()
    data=dict(measurement="Real local CrossEncoder raw logits; identity activation; no sigmoid",model=adapter.MODEL,
        requested_model=adapter.REQUESTED_MODEL,revision=adapter.REVISION,source="https://huggingface.co/"+adapter.MODEL,
        device="cpu",batch_size=adapter.BATCH_SIZE,threads=4,max_length=adapter.MAX_LENGTH,
        maximum_pair_tokens=max(lengths),truncated_pairs=sum(n>adapter.MAX_LENGTH for n in lengths),
        pairs_scored=len(pairs),benchmark_pairs=175,safety_pairs=50,
        runtime={name:version(name) for name in ("sentence-transformers","torch","transformers")},
        snapshot_file_bytes={name:(path/name).stat().st_size for name in adapter.FILES},
        snapshot_sha256={name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in adapter.FILES},
        candidate_fingerprint=candidate_fingerprint(),historical_hashes=historical_hashes(),scores=scored)
    timing=dict(model_load_seconds=load_seconds,inference_seconds=elapsed,
        average_seconds_per_question=elapsed/45,candidate_pairs_per_second=225/elapsed,
        note="CPU four threads, 225 pairs batched across 45 questions; cold predict call, excludes model load and token-length audit. Not production request latency.")
    return data,timing


def scored_rankings(data):
    if data["model"]!=adapter.MODEL or data["revision"]!=adapter.REVISION or data["candidate_fingerprint"]!=candidate_fingerprint() or data["historical_hashes"]!=historical_hashes():
        raise ValueError("Measured model or historical input fingerprint mismatch")
    cases,hits,safety,safety_hits=candidate_sets()
    if set(data["scores"])!={c["id"] for c in cases+safety}:
        raise ValueError("Measured query set changed")
    output={}
    for c in cases+safety:
        original=(hits if c["id"] in hits else safety_hits)[c["id"]]
        stored=data["scores"][c["id"]]
        if [h["chunk_id"] for h in stored]!=[h["chunk_id"] for h in original]:
            raise ValueError("Measured candidate ordering/identity mismatch")
        scores=[finite_score(h["score"]) for h in stored]
        output[c["id"]]=rerank(c["question"],original,lambda pairs:scores)
    return cases,hits,safety,safety_hits,output


def grid(rankings):
    scores=[h["reranker_score"] for rows in rankings.values() for h in rows]
    low,high=math.floor(min(scores)),math.ceil(max(scores))
    step=max(1,math.ceil((high-low)/(MAX_GRID_POINTS-1)))
    return [float(n) for n in range(low,high+step,step)]


def candidate_gates(row,baseline_rows):
    old={r["id"]:r for r in baseline_rows}
    recovered=[r["id"] for r in row["cases"] if r["answerable"] and r["accepted"] and not old[r["id"]]["accepted"]]
    corrected=[r["id"] for r in row["cases"] if not r["answerable"] and not r["accepted"] and old[r["id"]]["accepted"]]
    new_fp=[r["id"] for r in row["cases"] if not r["answerable"] and r["accepted"] and not old[r["id"]]["accepted"]]
    gates=dict(recover_at_least_four=len(recovered)>=4,correct_at_least_one=len(corrected)>=1,
        at_most_one_new_fp=len(new_fp)<=1,complete_at_least_eighteen=row["complete_evidence"]>=18,
        multi_at_least_four=row["multi_complete"]>=4,precision_at_least_0875=row["decision"]["precision"]>=.875,
        recall_improved=row["decision"]["recall"]>14/22,context_bounded=row["maximum_characters"]<=12000)
    return dict(recovered_false_negatives=recovered,corrected_false_positives=corrected,new_false_positives=new_fp,adoption_gates=gates)


def choose_development(sweep):
    """Declared selection: passing criteria first, not F1 alone; no safety input.

    If none pass, retain a diagnostic comparator by number of satisfied gates,
    then lower FP, complete evidence, multi completeness, recall, less extra
    context, and stricter thresholds. It is explicitly not an adopted policy.
    """
    return max(sweep,key=lambda r:(all(r["adoption_gates"].values()),sum(r["adoption_gates"].values()),
        -r["decision"]["fp"],r["complete_evidence"],r["multi_complete"],r["decision"]["recall"],
        -r["added_non_required"],r["threshold"],r["evidence_threshold"]))


def case_details(cases,original,ranked,primary,evidence):
    output=[]
    for c in cases:
        hits=ranked[c["id"]]
        accepted,context=select_context(hits,primary,evidence_threshold=evidence)
        old=build_context(original[c["id"]],SETTINGS)
        included={h["chunk_id"] for h in context.chunks};old_ids={h["chunk_id"] for h in old.chunks}
        strongest=max((h["score"] for h in original[c["id"]]),default=-1)
        output.append(dict(id=c["id"],question=c["question"],expected_answerable=c["expected_answerable"],
            expected_evidence=c["expected_sources"],original_top_cosine=strongest,reranker_top_score=hits[0]["reranker_score"],
            accepted=accepted,complete=bool(expected(c)) and covered(c,context.chunks)==expected(c),
            existing_fp_corrected=not c["expected_answerable"] and strongest>=.5 and not accepted,
            new_fp=not c["expected_answerable"] and strongest<.5 and accepted,
            candidates=[dict(chunk_id=h["chunk_id"],document=h["source_filename"],page_start=h["page_start"],page_end=h["page_end"],
                original_rank=h["original_rank"],cosine=h["score"],reranker_score=h["reranker_score"],reranker_rank=h["reranker_rank"],
                required=bool(covered(c,[h])),included=h["chunk_id"] in included,
                newly_included=h["chunk_id"] in included-old_ids,
                gate_witness=accepted and h["reranker_score"]>=primary) for h in hits]))
    return output


def compare(data,timing):
    cases,original,safety,safety_original,all_ranked=scored_rankings(data)
    ranked={c["id"]:all_ranked[c["id"]] for c in cases}
    baseline=audit_cases(cases,original)
    baseline_contexts={c["id"]:build_context(original[c["id"]],SETTINGS) for c in cases}
    thresholds=grid(ranked)  # Original benchmark ONLY, including range selection.
    sweep=[]
    for delta in EVIDENCE_OFFSETS:
        for row in threshold_sweep(cases,ranked,baseline_contexts,thresholds,evidence_offset=delta):
            row.update(candidate_gates(row,baseline["cases"]))
            sweep.append(row)
    selected=choose_development(sweep)
    primary,evidence=selected["threshold"],selected["evidence_threshold"]
    # Safety is evaluated only after a development comparator is frozen.
    safety_ranked={c["id"]:all_ranked[c["id"]] for c in safety}
    safety_old={c["id"]:build_context(safety_original[c["id"]],SETTINGS) for c in safety}
    safety_result=threshold_sweep(safety,safety_ranked,safety_old,[primary],evidence_offset=primary-evidence)[0]
    safety_baseline=audit_cases(safety,safety_original)
    safety_result.update(candidate_gates(safety_result,safety_baseline["cases"]))
    safety_ok=not safety_result["new_false_positives"] and safety_result["decision"]["precision"]>=safety_baseline["decision"]["precision"]
    practical=timing["average_seconds_per_question"]<=PRACTICAL_SECONDS_PER_QUESTION
    main_pass=all(selected["adoption_gates"].values())
    recommendation=("A. PROCEED TO PHASE 16B" if main_pass and safety_ok and practical else
                    "C. FURTHER EVALUATION REQUIRED" if main_pass else "B. DO NOT INTEGRATE")
    baseline_ranking=retrieval_metrics(cases,original); new_ranking=retrieval_metrics(cases,ranked)
    if new_ranking["hit_at_5"]!=baseline_ranking["hit_at_5"]:
        raise ValueError("Hit@5 must be invariant")
    return dict(phase="16A-R",recommendation=recommendation,measurement={k:v for k,v in data.items() if k!="scores"},
        declared_criteria=ADOPTION_CRITERIA,baseline=baseline,reranked_ranking=new_ranking,
        movements=movement(cases,ranked),score_distributions=score_distributions(cases,ranked),
        thresholds=thresholds,evidence_offsets=list(EVIDENCE_OFFSETS),sweep=sweep,
        comparator=selected,production_candidate_selected=main_pass and safety_ok and practical,
        selection_rule="Development criteria pass first, then satisfied criterion count, fewer FP, completeness, multi completeness, recall, fewer added non-required passages, stricter thresholds; safety never used to choose.",
        policy_c="Not evaluated: a 0.50 cosine intersection cannot recover the eight failures; no independent evidence justifies choosing a lower sanity floor. Adding one would introduce another tuned parameter.",
        details=case_details(cases,original,ranked,primary,evidence),
        safety_baseline=safety_baseline,safety_candidate=safety_result,
        safety_details=case_details(safety,safety_original,safety_ranked,primary,evidence),
        safety_score_distributions=score_distributions(safety,safety_ranked),
        final_checks=dict(development_criteria_pass=main_pass,safety_no_new_fp_and_precision_preserved=safety_ok,
                          local_compute_under_two_seconds_per_question=practical),
        timing=timing,historical_hashes=historical_hashes(),
        interpretation={
            "scope":"Recommendation concerns this model and bounded tested policies, not all possible rerankers or relevance policies.",
            "single_vs_dual":"At gate 3, evidence 3 or 1 retains 12/22 complete and 0/5 multi; evidence -1 restores one backup page, reaching 13/22 and 1/5. Two thresholds help but do not satisfy adoption.",
            "recall_tradeoff":"Gate -6 / evidence -10 reaches 18/22 complete and 4/5 multi, but TP/TN/FP/FN becomes 19/8/5/3, precision 0.79167, and neither existing FP is corrected. Evidence retention alone is not sufficient.",
            "new_false_negative":"multi-storage was baseline-answerable but its strongest reranker score is -0.79987; the selected diagnostic gate rejects it. Two recovered old FNs therefore yield only one net additional TP.",
            "useful_ranking":"para-history required page moves 2 to 1, but -6.94406 still fails the comparator gate. Promotion is not the same as recovered answerability.",
            "harmful_ranking":"para-signature required signature page moves 1 to 3 with -9.81566; expiration and CSRF passages outrank the integrity evidence. This reduces MRR even though overall Hit@1 is unchanged.",
            "false_positive_correction":"unsupported-refresh originally ranks web cache page first; CE demotes it 1 to 5 (-6.89962) but promotes access-token expiry to rank 1 (0.77108). unsupported-rotation keeps signature verification first (1.14804). Gate 3 rejects both; neither passage establishes the absent refresh-retention or rotation policy.",
            "multi_page_loss":"multi-token loses required signature evidence (about -9.44) despite accepting expiry (about 3.01); multi-deletion loses history snapshot evidence (about -6.34) despite accepting deletion (about 3.70). multi-cloud ranks PaaS better (5 to 3), yet its required score is about -11.00.",
            "new_related_context":"ambiguous-data newly includes storage-guide.pdf page 1 (-0.67882), describing physical PDFs and MongoDB metadata. This is useful related background beyond the labeled data-guide page; non-required is not automatically misleading.",
            "budget":"All fixed synthetic pairs are at most 65 tokens, below 512; no truncation or 12000-character budget exclusion explains these evidence losses.",
            "safety":"Frozen policy rejects mixed JWT/irrigation and unsupported rotation/revocation, but also rejects the answerable three-topic question. No policy was retuned on safety results.",
        },
        limitations=["Exploratory thresholds selected on the same small synthetic development benchmark; not held-out accuracy.",
            "Safety cases are separate checks, not threshold-training inputs; a positive passage does not establish support for every question part.",
            "Non-required is a ground-truth proxy, not proof of irrelevance. Model relevance is not factual confidence or entailment.",
            "Local batched throughput is not production latency; no Gemini answers or production services were evaluated."])


def markdown(report):
    selected=report["comparator"];d=selected["decision"]
    lines=["# Phase16A-R — real cross-encoder evaluation","",report["recommendation"],"",
        f"Model `{adapter.MODEL}` (approved spelling `{adapter.REQUESTED_MODEL}`), revision `{adapter.REVISION}`. Raw scalar logits, identity activation; relevance scores are NOT calibrated probabilities or factual confidence.","",
        "## Measured setup","",json.dumps(report["measurement"],indent=2),"",
        "## Ranking, distributions, and cost","",f"Baseline: `{report['baseline']['ranking']}`", "",
        f"Reranked: `{report['reranked_ranking']}`", "",f"Movements: `{report['movements']}`", "",
        json.dumps(report["score_distributions"],indent=2),"",json.dumps(report["timing"],indent=2),"",
        "## Policies and selection","","A = production cosine .50 gate / .30 evidence. B = reranker gate, keeping all passages above the evidence threshold. Single-threshold and offsets 2/4 logit units are evaluated. No score-space arithmetic.","",
        report["policy_c"],"",report["selection_rule"],"",
        f"Frozen development comparator: primary **{selected['threshold']}**, evidence **{selected['evidence_threshold']}**. Production candidate selected: **{report['production_candidate_selected']}**.","",
        f"Decision {d}; complete {selected['complete_evidence']}/22; multi {selected['multi_complete']}/5.","",
        f"Recovered FNs: {selected['recovered_false_negatives']}; corrected FPs: {selected['corrected_false_positives']}; new FPs: {selected['new_false_positives']}.","",
        f"Criteria: `{selected['adoption_gates']}`. Final checks: `{report['final_checks']}`.","",
        "## Full bounded development sweep","","| Gate | Evidence | TP/TN/FP/FN | P/R/F1 | Complete /22 | Multi /5 | Added non-required / removed required | Avg/max chunks | Avg/max chars | Budget cases |","|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["sweep"]:
        d=row["decision"]
        lines.append(f"| {row['threshold']} | {row['evidence_threshold']} | "+"/".join(str(d[k]) for k in ('tp','tn','fp','fn'))+" | "+"/".join(f"{d[k]:.5f}" for k in ('precision','recall','f1'))+f" | {row['complete_evidence']} | {row['multi_complete']} | {row['added_non_required']}/{row['removed_required']} | {row['average_chunks_accepted']:.3f}/{row['maximum_chunks']} | {row['average_characters_accepted']:.2f}/{row['maximum_characters']} | {len(row['budget_affected'])} |")
    lines += ["","## Every case and candidate (trusted original top five only)",""]
    for row in report["details"]:
        lines += [f"### {row['id']}","",row["question"],"",f"Expected evidence: {row['expected_evidence']}. Accepted: {row['accepted']}; complete: {row['complete']}.","",
            "| Source | Original rank/cosine | CE rank/logit | Required | Included | Newly included | Gate witness |","|---|---|---|---|---|---|---|"]
        for h in row["candidates"]:
            lines.append(f"| {h['document']} p{h['page_start']}–{h['page_end']} | {h['original_rank']} / {h['cosine']:.8f} | {h['reranker_rank']} / {h['reranker_score']:.8f} | {h['required']} | {h['included']} | {h['newly_included']} | {h['gate_witness']} |")
    lines += ["","## All thirteen unsupported cases","","| Case | Original max cosine | Max CE | Accepted | Existing FP corrected | New FP |","|---|---|---|---|---|---|"]
    for row in report["details"]:
        if not row["expected_answerable"]:
            lines.append(f"| {row['id']} | {row['original_top_cosine']:.8f} | {row['reranker_top_score']:.8f} | {row['accepted']} | {row['existing_fp_corrected']} | {row['new_fp']} |")
    lines += ["","## Separate safety set — checked after selection","",f"Baseline: `{report['safety_baseline']['decision']}`; complete {report['safety_baseline']['complete_evidence']}/4.","",
        f"Comparator: `{report['safety_candidate']['decision']}`; complete {report['safety_candidate']['complete_evidence']}/4. No thresholds changed after this check.","",
        "| Case/question | Max cosine | Max CE | Accepted | Complete | New FP |","|---|---|---|---|---|---|"]
    for row in report["safety_details"]:
        lines.append(f"| {row['id']}: {row['question']} | {row['original_top_cosine']:.8f} | {row['reranker_top_score']:.8f} | {row['accepted']} | {row['complete']} | {row['new_fp']} |")
    lines += ["","## Boundaries and limitations","",*report["limitations"],"",
        "Historical reports and production settings are unchanged. Citations remain assigned after final evidence selection by the trusted server map. No production reranker, Phase16B implementation, model replacement, decomposition, hybrid/BM25, LLM judge, or Phase17 work.","",
        "Model-free replay: `python -B backend/evaluation/phase16r.py`. Explicit local-only measurement: add `--measure`; independent offline repeat: add `--verify-offline`. Download is a separate explicit adapter command, never an inference fallback. Timing is stored separately; metric replay uses recorded values and is deterministic.",""]
    lines += ["## Evidence and distractor interpretation","",*[f"- **{key}**: {value}" for key,value in report["interpretation"].items()],""]
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure",action="store_true")
    parser.add_argument("--verify-offline",action="store_true")
    args=parser.parse_args()
    if args.measure and args.verify_offline:parser.error("Choose one measurement mode")
    if args.measure:
        data,timing=measure()
        SCORES.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
        TIMING.write_text(json.dumps(timing,indent=2)+"\n",encoding="utf-8")
    data=json.loads(SCORES.read_text(encoding="utf-8"));timing=json.loads(TIMING.read_text(encoding="utf-8"))
    if args.verify_offline:
        repeated,repeat_timing=measure()
        if repeated["candidate_fingerprint"]!=data["candidate_fingerprint"]:raise ValueError("Candidate drift")
        differences=[abs(a["score"]-b["score"]) for key in data["scores"] for a,b in zip(data["scores"][key],repeated["scores"][key])]
        delta=max(differences)
        if delta>1e-6:raise ValueError("Local inference exceeded repeat tolerance")
        verification=dict(max_absolute_score_difference=delta,tolerance=1e-6,pairs_verified=len(differences),network_connections_blocked=True,
                          repeated_inference_seconds=repeat_timing["inference_seconds"])
        (ROOT/"reports/phase16a-reranking-offline-check.json").write_text(json.dumps(verification,indent=2)+"\n",encoding="utf-8")
        print("Offline repeat:",verification)
    report=compare(data,timing)
    (ROOT/"reports/phase16a-reranking-measured.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    (ROOT/"reports/phase16a-reranking-measured.md").write_text(markdown(report),encoding="utf-8")
    print(report["recommendation"])
    print("Distributions:",report["score_distributions"])
    print("Ranking:",report["reranked_ranking"])
    r=report["comparator"]
    print("Comparator:",r["threshold"],r["evidence_threshold"],r["decision"],"complete",r["complete_evidence"],"multi",r["multi_complete"])
    print("Criteria:",r["adoption_gates"])
    print("Safety:",report["safety_candidate"]["decision"])


if __name__=="__main__":main()
