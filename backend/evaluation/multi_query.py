"""Phase 15A developer experiment. Default replay is offline and model-free.
--measure explicitly uses cached MiniLM + in-memory Qdrant, with sockets blocked.
No production imports this module. Reports never overwrite Phase 11/13 artifacts.
"""
import argparse
import asyncio
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import load_dataset, identifier, public_chunk
from evaluation.query_decomposition import decompose, STRATEGIES, MAX_QUERIES
from evaluation.retrieval_metrics import covered, expected, retrieval_metrics, valid_hits
from evaluation.phase13 import measured_rankings
from rag_context import Context, build_context

ROOT = Path(__file__).resolve().parent
PRIMARY, SECONDARY, TOP_K, BUDGET, RRF_K = .50, .30, 5, 12000, 60
HISTORICAL = tuple(f"{name}.{ext}" for name in ("fixture", "local-model", "phase13-comparison") for ext in ("json", "md"))
# Fixed BEFORE inference. Conservative product gates, not fitted to candidate results.
CRITERIA = dict(minimum_recovered_false_negatives=2, minimum_complete_evidence=16,
    maximum_false_positives=2, minimum_precision=.875, minimum_multi_complete=4,
    maximum_average_queries=2, maximum_queries=4, maximum_added_non_required=35,
    no_new_unsupported_acceptances=True, no_new_safety_acceptances=True,
    no_ranking_regression=True, maximum_context_characters=12000)
FIELDS = ("document_id", "chunk_id", "source_filename", "page_start", "page_end", "text")


def historical_hashes():
    return {name: hashlib.sha256((ROOT/"reports"/name).read_bytes()).hexdigest() for name in HISTORICAL}


def inputs():
    version, corpus, cases, _ = load_dataset()
    safety = json.loads((ROOT/"phase15a_safety.json").read_text(encoding="utf-8"))
    return version, corpus, cases, safety["cases"]


def fingerprints():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ("corpus.json", "cases.json", "phase15a_safety.json", "query_decomposition.py")}


def query_plan(cases):
    queries = {}
    for case in cases:
        for strategy in STRATEGIES:
            for text in decompose(case["question"], strategy):
                queries[identifier(text)] = text
    return queries


def fuse(rankings, method="max"):
    """Return at most five unique trusted hits; retain max cosine separately from RRF."""
    if method not in ("max", "rrf") or not 1 <= len(rankings) <= MAX_QUERIES:
        raise ValueError("Invalid fusion experiment")
    merged = {}
    for query_index, ranking in enumerate(rankings):
        for rank, hit in enumerate(valid_hits(ranking)[:TOP_K], 1):
            chunk_id = hit["chunk_id"]
            if chunk_id not in merged:
                merged[chunk_id] = {**hit, "score": float(hit["score"]), "fusion_score": 0., "contributions": []}
            row = merged[chunk_id]
            if any(row.get(key) != hit.get(key) for key in (*FIELDS, "chunk_index")):
                raise ValueError("Conflicting authoritative provenance")
            row["score"] = max(row["score"], float(hit["score"]))
            row["fusion_score"] += 1/(RRF_K+rank)
            row["contributions"].append({"query_index": query_index, "rank": rank, "cosine": hit["score"]})
    def order(row):
        value = row["score"] if method == "max" else row["fusion_score"]
        return (-value, row["document_id"], row["chunk_index"], row["chunk_id"])
    ordered = sorted(merged.values(), key=order)
    return [{**row, "rank": rank} for rank, row in enumerate(ordered[:TOP_K], 1)], ordered


def gate(rankings, mode):
    if mode not in ("original", "or"):
        raise ValueError("Invalid experimental gate")
    scores = [max((h["score"] for h in valid_hits(r)), default=-1.) for r in rankings]
    accepted = bool(scores and (scores[0] if mode == "original" else max(scores)) >= PRIMARY)
    return accepted, scores


def experimental_context(hits, accepted, budget=BUDGET):
    """Mirror complete-chunk packing, but preserve experimental fusion order.
    Cosine (not the RRF score) controls evidence; the separately evaluated gate
    has already run. Cap remains five, including if fusion drops the gate witness.
    """
    selected, serialized, seen = [], "[]", set()
    if not accepted:
        return Context(serialized, ())
    for hit in valid_hits(hits)[:TOP_K]:
        if hit["score"] < SECONDARY or hit["chunk_id"] in seen:
            continue
        record = {key: hit[key] for key in FIELDS}
        candidate = json.dumps([*selected, record], ensure_ascii=False, separators=(",", ":"))
        candidate.encode("utf-8")
        if len(candidate) > budget:
            continue
        selected.append(record); seen.add(hit["chunk_id"]); serialized = candidate
    return Context(serialized, tuple(selected))


def source(hit):
    return {"document": hit["source_filename"], "page_start": hit["page_start"], "page_end": hit["page_end"]}


def confusion(rows):
    counts = Counter(("tp" if r["accepted"] else "fn") if r["answerable"] else ("fp" if r["accepted"] else "tn") for r in rows)
    tp, tn, fp, fn = (counts[k] for k in ("tp", "tn", "fp", "fn"))
    precision = tp/(tp+fp) if tp+fp else 0.
    recall = tp/(tp+fn) if tp+fn else 0.
    return dict(tp=tp, tn=tn, fp=fp, fn=fn, precision=precision, recall=recall,
                f1=2*precision*recall/(precision+recall) if precision+recall else 0.)


def evaluate_case(case, strategy, fusion, mode, measurements):
    queries = decompose(case["question"], strategy)
    rankings = [measurements[identifier(q)] for q in queries]
    merged, union = fuse(rankings, fusion)
    accepted, scores = gate(rankings, mode)
    context = experimental_context(merged, accepted)
    unlimited = experimental_context(merged, accepted, 1000000)
    baseline_hits = rankings[0]
    baseline_context = experimental_context(baseline_hits, gate([baseline_hits], "original")[0])
    included = {c["chunk_id"] for c in context.chunks}
    old = {c["chunk_id"] for c in baseline_context.chunks}
    new = [h for h in merged if h["chunk_id"] in included-old]
    old_pages, new_pages = covered(case, baseline_context.chunks), covered(case, context.chunks)
    discovered = []
    for document, page in sorted(expected(case)):
        discoveries = [i for i, ranking in enumerate(rankings) if any(h["source_filename"] == document and h["page_start"] <= page <= h["page_end"] for h in ranking)]
        discovered.append({"document": document, "page": page, "query_indices": discoveries, "included": (document, page) in new_pages})
    new_fp = not case["expected_answerable"] and accepted and not gate([baseline_hits], "original")[0]
    lost = old_pages-new_pages
    ranking_harmed = bool(covered(case, baseline_hits[:1])) and not covered(case, merged[:1])
    quality = ("intact" if len(queries)==1 else "harmful" if new_fp or lost or ranking_harmed else
               "useful" if new_pages-old_pages else "neutral")
    return dict(id=case["id"], category=case["category"], question=case["question"], answerable=case["expected_answerable"],
        expected_evidence=case["expected_sources"], queries=queries, query_top_scores=scores,
        per_query_rankings=[[{**source(h), "chunk_id":h["chunk_id"], "score":h["score"]} for h in r] for r in rankings],
        accepted=accepted, new_false_positive=new_fp, gate_opening_query_indices=[i for i,s in enumerate(scores) if s>=PRIMARY],
        complete=bool(expected(case)) and new_pages==expected(case),
        complete_retrieved_at_5=bool(expected(case)) and covered(case,merged)==expected(case),
        complete_retrieved_union=bool(expected(case)) and covered(case,union)==expected(case),
        required_lost_by_merge_cap=[{"document":d,"page":p} for d,p in sorted(covered(case,union)-covered(case,merged))],
        required_recovered=[{"document":d,"page":p} for d,p in sorted(new_pages-old_pages)],
        required_lost=[{"document":d,"page":p} for d,p in sorted(lost)],
        added_non_required=[source(h) for h in new if not covered(case,[h])],
        added_required=[source(h) for h in new if covered(case,[h])],
        context_sources=[source(h) for h in context.chunks], context_chunk_count=len(context.chunks),
        context_characters=len(context.serialized), budget_excluded=len(unlimited.chunks)-len(context.chunks),
        primary_witness_excluded=accepted and not any(h["score"]>=PRIMARY for h in merged),
        total_hits=sum(len(r) for r in rankings), unique_merged_chunks=len(union),
        duplicate_hits=sum(len(r) for r in rankings)-len(union), required_evidence_discovery=discovered,
        subquery_contributions=[dict(query_index=i,unique_discoveries=sum(len(h["contributions"])==1 and h["contributions"][0]["query_index"]==i for h in union),
            merged_hits=sum(any(c["query_index"]==i for c in h["contributions"]) for h in union),
            context_hits=sum(h["chunk_id"] in included and any(c["query_index"]==i for c in h["contributions"]) for h in union)) for i in range(len(queries))],
        merged_ranking=[{**source(h),"chunk_id":h["chunk_id"],"score":h["score"],"fusion_score":h["fusion_score"],"contributions":h["contributions"]} for h in merged],
        decomposition_quality=quality), merged


def evaluate_strategy(cases, strategy, fusion, mode, measurements):
    rows, rankings = [], {}
    for case in cases:
        row, hits = evaluate_case(case, strategy, fusion, mode, measurements)
        rows.append(row); rankings[case["id"]] = hits
    accepted = [r for r in rows if r["accepted"]]
    multi = [r for r in rows if r["category"]=="multi_chunk"]
    return dict(name=f"{strategy}/{fusion}/{mode}", strategy=strategy, fusion=fusion, gate=mode,
        retrieval=retrieval_metrics(cases,rankings), decision=confusion(rows),
        complete_evidence=sum(r["complete"] for r in rows), multi_complete=sum(r["complete"] for r in multi), multi_count=len(multi),
        incomplete_answerable=[r["id"] for r in rows if r["answerable"] and not r["complete"]],
        recovered_false_negatives=[r["id"] for r in rows if r["answerable"] and r["accepted"] and r["query_top_scores"][0]<PRIMARY],
        new_false_positives=[r["id"] for r in rows if r["new_false_positive"]],
        required_pages_recovered=sum(len(r["required_recovered"]) for r in rows), required_pages_lost=sum(len(r["required_lost"]) for r in rows),
        added_non_required=sum(len(r["added_non_required"]) for r in rows),
        calls=sum(len(r["queries"]) for r in rows), additional_searches=sum(len(r["queries"])-1 for r in rows),
        average_queries=sum(len(r["queries"]) for r in rows)/len(rows), maximum_queries=max(len(r["queries"]) for r in rows),
        search_multiplier=sum(len(r["queries"]) for r in rows)/len(rows),
        average_context_chunks=sum(r["context_chunk_count"] for r in accepted)/len(accepted) if accepted else 0,
        maximum_context_chunks=max(r["context_chunk_count"] for r in rows), maximum_context_characters=max(r["context_characters"] for r in rows),
        budget_affected=[r["id"] for r in rows if r["budget_excluded"]],
        merge_cap_evidence_losses=[r["id"] for r in rows if r["required_lost_by_merge_cap"]],
        decomposed_case_count=sum(len(r["queries"])>1 for r in rows),
        total_retrieval_hits=sum(r["total_hits"] for r in rows),
        duplicate_hit_fraction=sum(r["duplicate_hits"] for r in rows)/sum(r["total_hits"] for r in rows) if sum(r["total_hits"] for r in rows) else 0,
        total_context_non_required=sum(sum(not any(s["document"]==e["document"] and s["page_start"]<=e["page"]<=s["page_end"] for e in r["expected_evidence"]) for s in r["context_sources"]) for r in rows),
        duplicate_hits=sum(r["duplicate_hits"] for r in rows), unique_chunks_per_case=sum(r["unique_merged_chunks"] for r in rows)/len(rows),
        decomposition_counts=dict(Counter(r["decomposition_quality"] for r in rows)), cases=rows)


async def measure():
    from evaluation.adapters import retrieve_dataset
    from importlib.metadata import version as package_version
    version, corpus, cases, safety = inputs()
    queries = query_plan(cases+safety)
    # Fail closed on ALL socket connections, even if an SDK ignores offline flags.
    with patch("socket.socket.connect", side_effect=RuntimeError("Offline evaluation prohibits network")), patch("socket.socket.connect_ex", side_effect=RuntimeError("Offline evaluation prohibits network")):
        hits, dimension = await retrieve_dataset(corpus, [{"id":i,"question":q} for i,q in queries.items()], {}, "local-model")
    lookup = {identifier(c["id"]):c["id"] for c in corpus}
    revision = (ROOT.parent/".cache/embeddings/models--sentence-transformers--all-MiniLM-L6-v2/refs/main").read_text().strip()
    if len(revision)!=40 or any(c not in "0123456789abcdef" for c in revision): raise ValueError("Invalid cached revision")
    return dict(version="1.0.0",dataset_version=version,fingerprints=fingerprints(),historical_hashes=historical_hashes(),
        model="sentence-transformers/all-MiniLM-L6-v2",dimension=dimension,model_revision=revision,
        runtime={k:package_version(k) for k in ("sentence-transformers","qdrant-client","torch")},
        measurement="Cached-only CPU MiniLM, official in-memory Qdrant, production authoritative retrieval validation; all socket connections blocked",
        queries={i:{"text":q,"hits":[{"chunk":lookup[h["chunk_id"]],"score":round(h["score"],8)} for h in hits[i]]} for i,q in queries.items()})


def replay(data):
    _,corpus,cases,safety = inputs()
    if data["fingerprints"]!=fingerprints() or data["historical_hashes"]!=historical_hashes():
        raise ValueError("Evaluation input fingerprint mismatch")
    if data["dimension"]!=384 or data["model"]!="sentence-transformers/all-MiniLM-L6-v2": raise ValueError("Model mismatch")
    expected_queries=query_plan(cases+safety)
    if set(data["queries"])!=set(expected_queries):raise ValueError("Query plan mismatch")
    index=Counter(); by_id={}
    for c in corpus:
        by_id[c["id"]]=public_chunk(c,index[c["document"]]);index[c["document"]]+=1
    rankings={}
    for key,row in data["queries"].items():
        if row["text"]!=expected_queries[key] or len(row["hits"])>TOP_K:raise ValueError("Invalid measured query")
        seen=set(); hits=[]
        for rank,h in enumerate(row["hits"],1):
            if h["chunk"] in seen or type(h["score"]) not in (int,float) or not math.isfinite(h["score"]) or not -1<=h["score"]<=1:raise ValueError("Invalid measured hit")
            seen.add(h["chunk"]);hits.append({**by_id[h["chunk"]],"rank":rank,"score":h["score"]})
        if any(a["score"]<b["score"] for a,b in zip(hits,hits[1:])):raise ValueError("Invalid ranking order")
        rankings[key]=hits
    return rankings


def compare(data):
    rankings=replay(data)
    version,_,cases,safety=inputs()
    specs=[("original","max","original")]+[(s,f,g) for s in STRATEGIES[1:] for f in ("max","rrf") for g in ("original","or")]
    candidates=[evaluate_strategy(cases,*spec,rankings) for spec in specs]
    safety_results=[evaluate_strategy(safety,*spec,rankings) for spec in specs]
    baseline=candidates[0]
    old_cases,old_hits,old_report=measured_rankings()
    historical_retrieval=retrieval_metrics(old_cases,old_hits)
    # Historical metrics are rounded; compare numeric values with tolerance.
    baseline_parity=(all(baseline["decision"][k]==old_report["baseline_threshold"][k] for k in ("tp","tn","fp","fn")) and baseline["complete_evidence"]==14 and baseline["multi_complete"]==4)
    baseline_parity=baseline_parity and all(abs(baseline["retrieval"][k]-historical_retrieval[k])<1e-7 for k in ("hit_at_1","hit_at_3","hit_at_5","mrr"))
    for candidate,safe in zip(candidates[1:],safety_results[1:]):
        candidate["adoption_gates"]={
            "baseline_reproduced":baseline_parity,
            "recovers_two":len(candidate["recovered_false_negatives"])>=2,
            "complete_at_least_16":candidate["complete_evidence"]>=16,
            "no_new_false_positives":not candidate["new_false_positives"] and candidate["decision"]["fp"]<=2,
            "precision_preserved":candidate["decision"]["precision"]>=.875,
            "multi_preserved":candidate["multi_complete"]>=4,
            "ranking_preserved":all(candidate["retrieval"][k]>=baseline["retrieval"][k]-1e-9 for k in ("hit_at_1","hit_at_3","hit_at_5","mrr")),
            "cost_bounded":candidate["average_queries"]<=2 and candidate["maximum_queries"]<=4 and safe["average_queries"]<=2 and safe["maximum_queries"]<=4,
            "distractors_bounded":candidate["added_non_required"]<=35,
            "no_new_safety_false_positives":not safe["new_false_positives"],
            "budget_preserved":candidate["maximum_context_characters"]<=12000}
    passed=[c["name"] for c in candidates[1:] if all(c["adoption_gates"].values())]
    observations = [
        {"classification":"useful supporting context", "case":"safety-explicit-questions", "strategy":"clauses/max/or",
         "sources":["storage-guide.pdf page 1", "authentication-guide.pdf page 2"],
         "reason":"Independent subqueries directly target both required pages and strengthen their scores, but both were already included: no new required evidence recovered."},
        {"classification":"harmless related context", "case":"safety-three-topics", "strategy":"topics/max/original",
         "sources":["storage-guide.pdf page 1"],
         "reason":"Added local-PDF/MongoDB metadata storage material is related background. It does not supply missing cloud responsibility pages."},
        {"classification":"misleading distractor", "case":"safety-explicit-questions", "strategy":"clauses/max/or",
         "sources":["operations-guide.pdf page 2", "operations-guide.pdf page 3"],
         "reason":"Idle dashboard sessions and automation API-key lifetimes are different from JWT access expiry; extra timers could be conflated."},
        {"classification":"misleading distractor", "case":"safety-unsupported-rotation", "strategy":"topics/max/or",
         "sources":["authentication-guide.pdf page 1", "operations-guide.pdf page 3", "web-guide.pdf page 1", "web-guide.pdf page 2"],
         "reason":"Signature verification, automation-key expiry, CSRF, and response caching do not specify JWT rotation or emergency revocation. These are synthetic corpus judgments, not an LLM judge."}]
    return dict(version="1.0.0",dataset_version=version,measurement=data["measurement"],model=data["model"],dimension=data["dimension"],model_revision=data["model_revision"],runtime=data["runtime"],
        historical_hashes=historical_hashes(),fingerprints=fingerprints(),predeclared_criteria=CRITERIA,
        primary=PRIMARY,secondary=SECONDARY,per_query_top_k=TOP_K,merged_limit=TOP_K,budget=BUDGET,rrf_constant=RRF_K,
        unique_measured_queries=len(data["queries"]),baseline_parity=baseline_parity,candidates=candidates,safety_candidates=safety_results,
        baseline_false_negatives=[r["id"] for r in baseline["cases"] if r["answerable"] and not r["accepted"]],
        recommendation="C — FURTHER EVALUATION REQUIRED" if passed else "B — DO NOT INTEGRATE",
        qualifying_candidates=passed, distractor_examples=observations,
        reason="Passing synthetic gates still requires held-out safety evaluation." if passed else "No candidate meets all predeclared benefit/safety gates. Keep production unchanged.")


def markdown(report):
    lines=["# Phase 15A: deterministic multi-query evaluation", "", report["recommendation"], "",report["reason"],"",
        "Cached MiniLM / in-memory Qdrant; socket connections blocked. Original 35-case benchmark and separate 10-case safety set. No Gemini or production integration.","",
        "## Predeclared criteria", "",json.dumps(report["predeclared_criteria"],sort_keys=True),"",
        "## Original benchmark comparison", "",
        "| Strategy / fusion / gate | Hit@1/3/5 | MRR@5 | TP/TN/FP/FN | P/R/F1 | Complete /22 | Multi /5 | Calls (multiplier) | Added non-required | Avg/max context chunks | Max chars |",
        "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in report["candidates"]:
        d=c["decision"];r=c["retrieval"]
        lines.append("| "+" | ".join([c["name"],"/".join(f"{r[k]:.5f}" for k in ("hit_at_1","hit_at_3","hit_at_5")),f"{r['mrr']:.5f}","/".join(str(d[k]) for k in ("tp","tn","fp","fn")),"/".join(f"{d[k]:.5f}" for k in ("precision","recall","f1")),str(c["complete_evidence"]),str(c["multi_complete"]),f"{c['calls']} ({c['search_multiplier']:.3f}x)",str(c["added_non_required"]),f"{c['average_context_chunks']:.3f}/{c['maximum_context_chunks']}",str(c["maximum_context_characters"])])+" |")
    lines += ["", "Ranking metrics use answerable cases only; a hit matches any expected document/page. MRR is truncated at five. Complete evidence requires ALL expected pages. Similarity is not confidence. All candidates cap merged evidence at five, as well as five per query; RRF is a separate ranking score, never compared with 0.50/0.30.","",
        "## Eight baseline false negatives", ""]
    baseline={r["id"]:r for r in report["candidates"][0]["cases"]}
    for key in report["baseline_false_negatives"]:
        r=baseline[key]
        lines += [f"### {key} ({r['category']})", "",r["question"],"", "Required: "+str(r["expected_evidence"]),
                  "",f"Original top cosine {r['query_top_scores'][0]:.8f}; primary rejects. All required pages already in top five: {r['complete_retrieved_at_5']}.","",
                  "Top five: "+"; ".join(f"{h['document']} p{h['page_start']} ({h['score']:.8f})" for h in r["per_query_rankings"][0]),"",
                  "| Candidate | Queries and top cosine | Gate | Recovered required pages | Added non-required |", "|---|---|---|---|---|"]
        for c in report["candidates"][1:]:
            x=next(d for d in c["cases"] if d["id"]==key)
            lines.append(f"| {c['name']} | "+"; ".join(f"{q} ({s:.8f})" for q,s in zip(x['queries'],x['query_top_scores']))+f" | {x['accepted']} | {x['required_recovered']} | {x['added_non_required']} |")
    lines += ["", "## All 13 unsupported original cases", "", "Each candidate is shown; a high cosine is not proof of answer support.","",
        "| Case / question | Candidate | Queries / top cosine | Gate | New FP | Opening query indices |", "|---|---|---|---|---|---|"]
    for r in report["candidates"][0]["cases"]:
        if r["answerable"]:continue
        for c in report["candidates"]:
            x=next(d for d in c["cases"] if d["id"]==r["id"])
            lines.append(f"| {r['id']}: {r['question']} | {c['name']} | "+"; ".join(f"Q{i}: {q} ({s:.8f})" for i,(q,s) in enumerate(zip(x['queries'],x['query_top_scores'])))+f" | {x['accepted']} | {x['new_false_positive']} | {x['gate_opening_query_indices']} |")
    lines += ["", "## Safety summary (4 answerable / 6 unsupported)", "", "| Candidate | TP/TN/FP/FN | P/R/F1 | Complete /4 | Calls / multiplier / max | Added non-required |", "|---|---|---|---|---|---|"]
    for c in report["safety_candidates"]:
        d=c["decision"]
        lines.append(f"| {c['name']} | "+"/".join(str(d[k]) for k in ('tp','tn','fp','fn'))+" | "+"/".join(f"{d[k]:.5f}" for k in ('precision','recall','f1'))+f" | {c['complete_evidence']} | {c['calls']} / {c['search_multiplier']:.2f}x / {c['maximum_queries']} | {c['added_non_required']} |")
    lines += ["", "## Phase 15-only safety set (separate denominator)", "", "| Case | Candidate | Queries / top cosine | Gate | New FP | Complete | Added non-required |", "|---|---|---|---|---|---|---|"]
    for c in report["safety_candidates"]:
        for r in c["cases"]:
            lines.append(f"| {r['id']} | {c['name']} | "+"; ".join(f"{q} ({s:.5f})" for q,s in zip(r['queries'],r['query_top_scores']))+f" | {r['accepted']} | {r['new_false_positive']} | {r['complete']} | {r['added_non_required']} |")
    lines += ["", "## Decomposition, cost, and diagnostics", "", "| Candidate | Intact/useful/neutral/harmful | Avg/max queries | Extra calls | Duplicate hits | Required pages recovered/lost | Budget affected |", "|---|---|---|---|---|---|---|"]
    for c in report["candidates"]:
        lines.append(f"| {c['name']} | {c['decomposition_counts']} | {c['average_queries']:.3f}/{c['maximum_queries']} | {c['additional_searches']} | {c['duplicate_hits']} | {c['required_pages_recovered']}/{c['required_pages_lost']} | {c['budget_affected']} |")
    lines += ["", "## Distractor interpretation", ""]
    for example in report["distractor_examples"]:
        lines.append(f"- **{example['classification']}** ({example['case']}, {example['strategy']}): {', '.join(example['sources'])}. {example['reason']}")
    lines += ["", "Full JSON contains per-query rankings, merged cosine and RRF scores, required-evidence discovery query indices, subquery contributions, and all failed adoption gates. No vectors or full corpus text are stored in this report.","",
        "## Limitations", "", "A small, already-studied synthetic development benchmark cannot establish generalization. Seven false negatives are not clear independent multi-topic requests. These conservative rules intentionally do not rewrite them or introduce synonyms. Original-gate fusion cannot recover original-gate false negatives by construction. OR can erase unsupported intent by accepting one supported fragment. Top-five fusion can displace required evidence even when its union contains it. Extra non-required passages are a conservative distractor proxy, not a semantic harm score. No generated answers or entailment were judged; no LLM judge was used. Rounded cached scores are reproducible by replay; fresh inference may have small hardware/version numerical differences.", "",
        "No Phase 15B architecture is recommended unless predeclared gates pass. Production settings, citations, history, search, and frontend remain unchanged.",""]
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure",action="store_true",help="Cached-only CPU inference; no download or network fallback")
    parser.add_argument("--output",type=Path,default=ROOT/"reports")
    args=parser.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT.parents[1]):parser.error("Output must remain inside repository")
    measurement=ROOT/"phase15a_rankings.json"
    if args.measure:
        data=asyncio.run(measure())
    else:
        data=json.loads(measurement.read_text(encoding="utf-8"))
    report=compare(data)
    out.mkdir(parents=True,exist_ok=True)
    if args.measure:measurement.write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")
    (out/"phase15a-multi-query.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    (out/"phase15a-multi-query.md").write_text(markdown(report),encoding="utf-8")
    print(report["recommendation"])
    print("Baseline reproduced:",report["baseline_parity"],"; unique measured queries:",report["unique_measured_queries"])
    for c in report["candidates"]:print(c["name"],c["decision"],"complete",c["complete_evidence"],"multi",c["multi_complete"],"calls",c["calls"])


if __name__=="__main__":main()
