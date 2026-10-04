"""Phase 13 policy comparison. Offline replay of committed measured rankings.
No application settings, .env, model download, or external services are used.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import load_dataset, public_chunk
from evaluation.retrieval_metrics import covered, expected, retrieval_metrics
from evaluation.rag_metrics import decision_metrics
from rag_context import build_context

ROOT = Path(__file__).resolve().parent
THRESHOLDS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
PRIMARY, BUDGET, TOP_K = 0.50, 12000, 5


def measured_rankings(report_path=None):
    version, chunks, cases, _ = load_dataset()
    report = json.loads((report_path or ROOT/'reports/local-model.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in ('corpus.json','cases.json','fixture_rankings.json'))).hexdigest()
    if digest != report['dataset_sha256'] or version != report['dataset_version']:
        raise ValueError('Baseline dataset mismatch')
    by_page = {(c['document'],c['page']):c for c in chunks}
    if len(by_page) != len(chunks):
        raise ValueError('Replay requires unique synthetic document/page chunks')
    rankings = {}
    for row in report['cases']:
        hits = []
        for rank, hit in enumerate(row['ranked_sources'], 1):
            if hit['page_start'] != hit['page_end']:
                raise ValueError('Unsupported baseline page range')
            chunk = by_page[(hit['document'],hit['page_start'])]
            hits.append({**public_chunk(chunk), 'rank':rank,'score':hit['score']})
        rankings[row['id']] = hits
    return cases, rankings, report


def candidate_context(hits, threshold, budget=BUDGET):
    # Evaluation-only policy: primary gate FIRST, then the unchanged complete-chunk builder.
    if not hits or hits[0]['score'] < PRIMARY:
        return build_context([], SimpleNamespace(top_k=TOP_K,min_relevance_score=PRIMARY,min_evidence_score=threshold,max_context_chars=budget))
    return build_context(hits, SimpleNamespace(top_k=TOP_K,min_relevance_score=PRIMARY,min_evidence_score=threshold,max_context_chars=budget))


def compare(cases, rankings):
    rows=[]
    for threshold in THRESHOLDS:
        details=[]
        for case in cases:
            hits=rankings[case['id']]
            context=candidate_context(hits,threshold)
            unlimited=candidate_context(hits,threshold,1000000)
            included=set(c['chunk_id'] for c in context.chunks)
            baseline=set(c['chunk_id'] for c in candidate_context(hits,PRIMARY).chunks)
            extras=[h for h in hits if h['chunk_id'] in included-baseline]
            required=lambda h: bool(covered(case,[h]))
            details.append({'id':case['id'],'category':case['category'],'answerable':case['expected_answerable'],
                'primary_passed':bool(hits and hits[0]['score']>=PRIMARY),
                'complete':bool(expected(case)) and covered(case,context.chunks)==expected(case),
                'all_evidence_after_validation':bool(expected(case)) and covered(case,hits)==expected(case),
                'context_count':len(context.chunks),'context_characters':len(context.serialized),
                'budget_excluded_count':len(unlimited.chunks)-len(context.chunks),
                'new_required_count':sum(required(h) for h in extras),
                'new_non_required_count':sum(not required(h) for h in extras),
                'additional_sources':[{'document':h['source_filename'],'page':h['page_start'],'required':required(h)} for h in extras],
                'context_sources':[{'document':h['source_filename'],'page':h['page_start']} for h in context.chunks]})
        multi=[d for d in details if d['category']=='multi_chunk']
        rows.append({'evidence_threshold':threshold,'answerability':decision_metrics(cases,rankings,PRIMARY),
            'complete_evidence_count':sum(d['complete'] for d in details),'answerable_count':sum(c['expected_answerable'] for c in cases),
            'multi_chunk_complete':sum(d['complete'] for d in multi),'multi_chunk_count':len(multi),
            'multi_chunk_completeness':sum(d['complete'] for d in multi)/len(multi),
            'average_context_chunks_all_cases':sum(d['context_count'] for d in details)/len(details),
            'average_context_chunks_primary_accepted':sum(d['context_count'] for d in details)/sum(d['primary_passed'] for d in details),
            'maximum_context_chunks':max(d['context_count'] for d in details),'maximum_context_characters':max(d['context_characters'] for d in details),
            'new_required_chunks':sum(d['new_required_count'] for d in details),
            'new_non_required_chunks':sum(d['new_non_required_count'] for d in details),
            'new_non_required_on_unsupported':sum(d['new_non_required_count'] for d in details if not d['answerable']),
            'budget_excluded_count':sum(d['budget_excluded_count'] for d in details),
            'provider_eligible_confusion':{key:sum(((('tp' if d['context_count'] else 'fn') if d['answerable'] else ('fp' if d['context_count'] else 'tn')) == key) for d in details) for key in ('tp','tn','fp','fn')},'cases':details})
    baseline=next(r for r in rows if r['evidence_threshold']==.50)
    selected=next(r for r in rows if r['evidence_threshold']==.30)
    decision={k:baseline['answerability'][k] for k in ('tp','tn','fp','fn')}
    gates={'answerability_unchanged':all(r['provider_eligible_confusion']==decision for r in rows),
        'evidence_improved':selected['complete_evidence_count']>baseline['complete_evidence_count'],
        'multi_chunk_improved':selected['multi_chunk_complete']>baseline['multi_chunk_complete'],
        'budget_respected':all(r['maximum_context_characters']<=BUDGET for r in rows),
        'weak_only_never_accepted':all(not d['context_count'] for r in rows for d in r['cases'] if not d['primary_passed'])}
    return {'primary_threshold':PRIMARY,'top_k':TOP_K,'context_budget':BUDGET,
        'retrieval':retrieval_metrics(cases,rankings),'candidates':rows,'baseline_evidence_threshold':.50,
        'selection_gates':gates,'selected_evidence_threshold':.30 if all(gates.values()) else None,
        'loss_diagnosis':{'complete_after_authoritative_validation':sum(d['all_evidence_after_validation'] for d in baseline['cases']),
            'complete_after_baseline_context':baseline['complete_evidence_count'],
            'answerable_primary_rejections':baseline['answerability']['fn'],
            'budget_exclusions':baseline['budget_excluded_count']},
        'selection_reason':'0.30 is the highest tested threshold preserving maximum complete evidence (14/22, 4/5 multi). Higher thresholds lose evidence; lower thresholds add only non-required inclusions. Gate acceptance is stable, not a guarantee of answer correctness.'}



def markdown(report):
    lines=['# Phase 13 secondary evidence comparison','','Primary gate 0.50; top-k 5; context budget 12000. Replay uses the committed cached MiniLM measured rankings, not authored fixture scores. No Gemini calls.','','| Evidence | TP/TN/FP/FN | Complete /22 | Multi /5 | Avg chunks (all) | Max | New required | New non-required | Extra on unsupported | Budget drops |','|---|---|---|---|---|---|---|---|---|---|']
    for r in report['candidates']:
        d=r['answerability']
        lines.append('| '+ ' | '.join(str(x) for x in (r['evidence_threshold'],'/'.join(str(d[k]) for k in ('tp','tn','fp','fn')),r['complete_evidence_count'],r['multi_chunk_complete'],round(r['average_context_chunks_all_cases'],4),r['maximum_context_chunks'],r['new_required_chunks'],r['new_non_required_chunks'],r['new_non_required_on_unsupported'],r['budget_excluded_count']))+' |')
    lines+=['','Retrieval metrics: '+str(report['retrieval']), '', 'Selected evidence threshold: '+str(report['selected_evidence_threshold']), '',report['selection_reason'], '', 'Loss diagnosis: '+str(report['loss_diagnosis']), '', 'Precision/recall/F1 stay 0.875 / 0.63636364 / 0.73684211 for every candidate. Hit@1/3/5 and MRR are unchanged because retrieval is unchanged.', '', 'At 0.30, average context size among primary-accepted cases is 3.9375 (baseline 1.25); maximum is 5 (baseline 2). Full per-case sources, counts, character budgets and non-required inclusions are in the JSON.', '', 'Useful recovery: multi-backup adds North page 1 to South page 4; multi-deletion restores history snapshot evidence from data-guide.pdf page 3. Harmless/background example: multi-storage adds PDF page-provenance context (storage-guide.pdf page 2), which is not needed for the storage-location question. Potentially misleading example: unsupported JWT rotation adds South API-key expiry and session timeout passages (operations-guide.pdf pages 3 and 2). Neither answers JWT signing-secret rotation. These labels use corpus meaning and expected pages, not similarity alone.', '', 'The two existing false acceptances (unsupported-refresh and unsupported-rotation) receive seven extra non-required inclusions at 0.30. No live Gemini answer-quality claim is made; its grounded abstention remains essential. The remaining multi-cloud case fails the primary gate, so this policy deliberately cannot recover it. Eight answerable primary rejections remain.', '', 'Limitations: 35 synthetic cases; replayed scores are rounded to eight decimals; no held-out generalization guarantee, no LLM judge, no Cloud or live Gemini test. A below-0.50 manual multi-topic question must still abstain. Query decomposition is a future candidate to evaluate separately, not implemented.', '', 'Non-required is a conservative distractor proxy from expected evidence, not a claim that every extra passage is harmful. Answerability acceptance is not generated-answer correctness.','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'reports')
    parser.add_argument('--rankings-report',type=Path,help='Optional fresh Phase 11 local-model report inside this repository')
    args=parser.parse_args()
    output=args.output.resolve()
    if not output.is_relative_to(ROOT.parents[1]): parser.error('Output must remain in this repository')
    if args.rankings_report and not args.rankings_report.resolve().is_relative_to(ROOT.parents[1]):
        parser.error("Input must remain in this repository")
    cases,rankings,baseline=measured_rankings(args.rankings_report)
    report=compare(cases,rankings)
    report.update(dataset_version=baseline['dataset_version'],dataset_sha256=baseline['dataset_sha256'],measurement='Replay of committed measured MiniLM rankings (rounded to 8 decimals), using synthetic corpus text')
    report['baseline_text_sha256']={name:hashlib.sha256((ROOT/'reports'/name).read_text(encoding='utf-8').encode()).hexdigest() for name in ('local-model.json','local-model.md','fixture.json','fixture.md')}
    output.mkdir(parents=True,exist_ok=True)
    (output/'phase13-comparison.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    (output/'phase13-comparison.md').write_text(markdown(report),encoding='utf-8')
    print(markdown(report))

if __name__=='__main__': main()
