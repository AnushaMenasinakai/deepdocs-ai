"""Phase16 mechanics use fake scores only; no inference or cloud access."""
import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import public_chunk
from evaluation.phase16a import ROOT, build_report, markdown, snapshot_hashes, SETTINGS, PRODUCTION, HISTORICAL
from evaluation.phase13 import measured_rankings
from evaluation.retrieval_metrics import retrieval_metrics
from evaluation.reranking import (rerank, finite_score, select_context, threshold_sweep,
                                  score_distributions, movement, ADOPTION_CRITERIA)
from rag_context import build_context


def hit(name="a", page=1, score=.6, text="Synthetic passage"):
    return {**public_chunk(dict(id=name,document="synthetic.pdf",page=page,text=text)),"score":score}


def case(answerable=True, pages=(1,2)):
    return dict(id="test",question="Synthetic question?",category="multi_chunk",
                expected_answerable=answerable,
                expected_sources=[dict(document="synthetic.pdf",page=p) for p in pages] if answerable else [])


class RerankerInterfaceTests(unittest.TestCase):
    def test_pairs_identity_cosine_and_input_immutable(self):
        candidates=[hit(),hit("b",2,.4)]
        before=copy.deepcopy(candidates)
        scorer=Mock(return_value=[-2.,3.])
        result=rerank("Question?",candidates,scorer)
        scorer.assert_called_once_with([("Question?",h["text"]) for h in candidates])
        self.assertEqual(candidates,before)
        self.assertEqual(result[0]["chunk_id"],candidates[1]["chunk_id"])
        self.assertEqual(result[0]["score"],.4)
        self.assertEqual(result[0]["reranker_score"],3.)
        self.assertEqual(result[0]["original_rank"],2)
        self.assertEqual(result[0]["reranker_rank"],1)
        self.assertEqual({h["chunk_id"] for h in result},{h["chunk_id"] for h in candidates})

    def test_finite_scalar_only(self):
        for value in (True,False,None,"0.9",[],{},float("nan"),float("inf"),-float("inf"),10**1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError): finite_score(value)
        self.assertEqual(finite_score(-7),-7.)

    def test_bounded_candidates_bad_output_and_duplicates(self):
        scorer=Mock(return_value=[1.])
        for rows in ([hit()]*2,[hit(str(i),i+1) for i in range(6)],[{**hit(),"score":True}]):
            with self.assertRaises(ValueError):rerank("q",rows,scorer)
        scorer.assert_not_called()
        for result in (None,[],[1,2],{"score":1},[[1]],"1"):
            with self.subTest(result=result):
                with self.assertRaises(ValueError):rerank("q",[hit()],lambda pairs:result)

    def test_empty_and_failure_do_not_retrieve(self):
        scorer=Mock(side_effect=RuntimeError("fake unavailable scorer"))
        self.assertEqual(rerank("q",[],scorer),[])
        scorer.assert_not_called()
        with self.assertRaises(RuntimeError):rerank("q",[hit()],scorer)

    def test_stable_ties_determinism(self):
        rows=[hit("b",2),hit("a",1)]
        first=rerank("q",rows,lambda pairs:[.2,.2])
        self.assertEqual(first,rerank("q",rows,lambda pairs:[.2,.2]))
        self.assertEqual([h["chunk_id"] for h in first],[h["chunk_id"] for h in rows])

    def test_hit5_invariant_required_promotion_distractor_demotion(self):
        c=case(pages=(2,))
        rows=[hit(),hit("b",2,.4)]
        ranked=rerank("q",rows,lambda pairs:[-1.,2.])
        old=retrieval_metrics([c],{"test":rows});new=retrieval_metrics([c],{"test":ranked})
        self.assertEqual(old["hit_at_5"],new["hit_at_5"])
        self.assertEqual(old["hit_at_1"],0);self.assertEqual(new["hit_at_1"],1)
        self.assertEqual(movement([c],{"test":ranked}),dict(required_promoted=1,required_demoted=0,non_required_promoted=0,non_required_demoted=1))


class ExperimentalPolicyTests(unittest.TestCase):
    def test_replace_and_intersection_are_separate_score_spaces(self):
        rows=rerank("q",[hit(score=.4)],lambda pairs:[7.])
        self.assertTrue(select_context(rows,6.,"replace")[0])
        self.assertFalse(select_context(rows,6.,"intersection")[0])
        rows=rerank("q",[hit(score=.9)],lambda pairs:[-7.])
        self.assertFalse(select_context(rows,0.,"replace")[0])
        self.assertFalse(select_context(rows,0.,"intersection")[0])

    def test_multipage_preserves_order_and_complete_budget(self):
        rows=rerank("q",[hit(),hit("b",2,.4),hit("large",3,.4,"x"*13000)],lambda pairs:[2.,3.,4.])
        gate,context=select_context(rows,1.)
        self.assertTrue(gate);self.assertEqual([h["page_start"] for h in context.chunks],[2,1])
        exact=len(context.serialized)
        self.assertEqual(len(select_context(rows,1.,budget=exact)[1].chunks),2)
        self.assertEqual(len(select_context(rows,1.,budget=exact-1)[1].chunks),1)
        self.assertEqual(select_context(rows,1.,budget=2)[1].serialized,"[]")
        self.assertLessEqual(exact,12000)

    def test_bad_threshold_budget_policy_and_duplicates(self):
        rows=rerank("q",[hit()],lambda pairs:[1.])
        for threshold in (True,float("nan"),float("inf"),"1"):
            with self.assertRaises(ValueError):select_context(rows,threshold)
        with self.assertRaises(ValueError):select_context(rows,0.,"unknown")
        with self.assertRaises(ValueError):select_context(rows,0.,budget=1)
        with self.assertRaises(ValueError):select_context(rows*2,0.)

    def test_sweep_completeness_accounting_and_determinism(self):
        c=case();hits=[hit(),hit("b",2,.4)]
        ranked={"test":rerank("q",hits,lambda pairs:[2.,1.])}
        old={"test":build_context(hits,SETTINGS)}
        first=threshold_sweep([c],ranked,old,[3.,0.,1.5])
        self.assertEqual(first,threshold_sweep([c],ranked,old,[0.,3.,1.5,0.]))
        self.assertEqual([r["complete_evidence"] for r in first],[1,0,0])
        self.assertEqual([r["multi_complete"] for r in first],[1,0,0])
        self.assertEqual(first[1]["removed_required"],1)
        self.assertEqual(first[2]["decision"]["fn"],1)
        no_room=threshold_sweep([c],ranked,old,[0.],budget=2)[0]
        self.assertEqual(no_room["decision"]["tp"],1)
        self.assertEqual(no_room["provider_eligible_decision"]["fn"],1)
        self.assertEqual(no_room["budget_affected"],["test"])
        with self.assertRaises(ValueError):threshold_sweep([c],ranked,old,[])
        with self.assertRaises(ValueError):threshold_sweep([c],ranked,old,list(range(42)))

    def test_unsupported_rotation_mixed_unrelated_accounting_not_model_claim(self):
        # Authored scalars intentionally demonstrate the risk: not measured results.
        for question,score,fp in (("JWT rotation",2.,1),("JWT and irrigation",2.,1),("rice farming",-2.,0)):
            with self.subTest(question=question):
                c=case(False);c["question"]=question
                hits=[hit(score=.4)]
                ranked={"test":rerank(question,hits,lambda pairs:[score])}
                row=threshold_sweep([c],ranked,{"test":build_context(hits,SETTINGS)},[0.])[0]
                self.assertEqual(row["decision"]["fp"],fp)
                self.assertEqual(row["added_non_required"],fp)

    def test_score_distributions_are_not_probabilities(self):
        c=case(pages=(1,))
        rows=rerank("q",[hit(),hit("b",2)],lambda pairs:[-3.,9.])
        result=score_distributions([c],{"test":rows})
        self.assertEqual(result["required_answerable"]["minimum"],-3.)
        self.assertEqual(result["non_required_answerable"]["maximum"],9.)
        self.assertIsNone(result["unsupported"]["mean"])


# Reviewed against c15932bf2a3e16b76dacc8bedd7c2a7a51565a24, not generated at
# test runtime from the reports' own claims. Only CRLF -> LF is normalized.
EVIDENCE_SHA256 = {
    'fixture.json': '53fb503c48dc1bef518114f13acb268dbc5a998c215f8312593fca34f77250ca',
    'fixture.md': 'efe661a2f2c55c84f16388374db764c9b9e88413d20e00bbaa6d76d89fc030cc',
    'local-model.json': 'e24e6ceff93f8ea3e2126b9e6d3d8791c0b949126c9cc69e8d342524d189010c',
    'local-model.md': '8fcdff4f34e7358406b392efd4943dbeef8274bb96e8797da54a3a85954389ca',
    'phase13-comparison.json': '122c6e835814540da2cb0f180ae8599f9084110f62efff6fa8afbb4e224061c9',
    'phase13-comparison.md': '56af029c5fe0f4b3c5df1cf6965cb1fbffd41ea7c8aa32390cc3545e755991c7',
    'phase15a-multi-query.json': 'fec6d794e5c34aab5242ac6e15e4dda077fd26d8d428d34f25d981c9b53913d9',
    'phase15a-multi-query.md': '7f5bef55173449f386a78121bfcdb4096dd858a5302d9ae3da4a25ecafc9e9c5',
    'phase16a-reranking.json': '376e997dae86ab96fd0736283e534054597c1ae698c2aa10b6a51459e36d3b88',
    'phase16a-reranking.md': '534f9e2e38fc4d81f1fdb71f55a090db3d89bd93f6b3f15617bfc6a2a1e07307',
}


def verify_evidence(name, read_bytes):
    if name not in EVIDENCE_SHA256:
        raise AssertionError('Unknown historical evidence')
    digest = hashlib.sha256(read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    if digest != EVIDENCE_SHA256[name]:
        raise AssertionError('Historical evidence changed: ' + name)


def behavioral_report(report):
    return {key: value for key, value in report.items() if key != 'production_sha256'}


class AuditReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in EVIDENCE_SHA256:
            verify_evidence(name, (ROOT/'reports'/name).read_bytes)
        cls.saved=json.loads((ROOT/"reports/phase16a-reranking.json").read_text(encoding="utf-8"))

    def test_replay_deterministic_no_network_models_or_providers(self):
        with patch("socket.socket.connect",side_effect=AssertionError("network")),patch("socket.socket.connect_ex",side_effect=AssertionError("network")),patch("embeddings.load_model",side_effect=AssertionError("model")),patch("rag.get_gemini_provider",side_effect=AssertionError("provider")):
            first=build_report(self.saved["environment"])
            second=build_report(self.saved["environment"])
        self.assertEqual(first,second)
        self.assertEqual(behavioral_report(first),behavioral_report(self.saved))
        self.assertEqual(first['production_sha256'], {
            name: hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest()
            for name in PRODUCTION})
        self.assertEqual(markdown(first),(ROOT/"reports/phase16a-reranking.md").read_text(encoding="utf-8"))
        self.assertEqual(first["pairs_scored"],0)
        self.assertIsNone(first["reranked_metrics"])
        self.assertEqual(first["thresholds_evaluated"],[])
        self.assertEqual(first["recommendation"],"C. FURTHER EVALUATION REQUIRED")

    def test_eight_false_negatives_and_two_false_positives_reproduced(self):
        rows=self.saved["baseline"]["cases"]
        fn=[r for r in rows if r["answerable"] and not r["accepted"]]
        self.assertEqual({r["id"] for r in fn},{"direct-password","para-signature","para-owner","para-ocr","para-history","multi-cloud","ambiguous-data","ambiguous-remember"})
        self.assertTrue(all(r["required_evidence_present_at_5"] for r in fn))
        self.assertEqual({r["id"] for r in rows if not r["answerable"] and r["accepted"]},{"unsupported-refresh","unsupported-rotation"})
        self.assertEqual(self.saved["baseline"]["complete_evidence"],14)
        self.assertEqual(self.saved["baseline"]["multi_complete"],4)
        self.assertEqual(self.saved["baseline"]["candidate_pairs_available"],175)

    def test_all_unsupported_and_safety_have_no_invented_second_stage_results(self):
        unsupported=[r for r in self.saved["baseline"]["cases"] if not r["answerable"]]
        self.assertEqual(len(unsupported),13)
        safety=self.saved["safety_baseline"]
        self.assertEqual(safety["case_count"],10)
        self.assertEqual(safety["complete_evidence"],2)
        for row in unsupported+safety["cases"]:
            self.assertIsNone(row["reranker_decision"])
            self.assertTrue(all(h["reranker_score"] is None and h["reranker_rank"] is None for h in row["candidates"]))

    def test_historical_files_and_recorded_provenance_unchanged(self):
        self.assertEqual(set(EVIDENCE_SHA256), set(HISTORICAL) | {
            'phase16a-reranking.json', 'phase16a-reranking.md'})
        for name in EVIDENCE_SHA256:
            verify_evidence(name, (ROOT/'reports'/name).read_bytes)
        self.assertEqual(snapshot_hashes(),self.saved["historical_report_sha256"])

    def test_evidence_allows_only_line_ending_variants(self):
        for name in EVIDENCE_SHA256:
            raw = (ROOT/'reports'/name).read_bytes().replace(b'\r\n', b'\n')
            with self.subTest(name=name):
                for variant in (raw, raw.replace(b'\n', b'\r\n')):
                    verify_evidence(name, lambda: variant)
                for tampered in (raw + b' ', raw.replace(b'\n', b' \n', 1), raw[:-1], raw + b'\r'):
                    with self.assertRaises(AssertionError):
                        verify_evidence(name, lambda: tampered)

    def test_changed_values_and_recorded_hashes_are_rejected(self):
        for field in ('production_sha256', 'baseline', 'adoption_criteria'):
            changed = copy.deepcopy(self.saved)
            changed[field] = {}
            with self.subTest(field=field), self.assertRaises(AssertionError):
                verify_evidence('phase16a-reranking.json', lambda: json.dumps(changed).encode())

    def test_missing_and_unknown_evidence_fail_closed(self):
        reader = Mock(side_effect=FileNotFoundError('missing evidence'))
        with self.assertRaises(FileNotFoundError):
            verify_evidence('phase16a-reranking.json', reader)
        reader.reset_mock()
        with self.assertRaises(AssertionError):
            verify_evidence('unknown.json', reader)
        reader.assert_not_called()

    def test_configuration_source_change_affects_only_provenance(self):
        original = Path.read_bytes
        config_path = ROOT.parent/'config.py'
        changed = original(config_path) + b'\n# Legitimate configuration maintenance\n'
        def read(path):
            return changed if path == config_path else original(path)
        with patch.object(Path, 'read_bytes', read), patch('socket.socket.connect', side_effect=AssertionError('network')), patch('socket.socket.connect_ex', side_effect=AssertionError('network')), patch('embeddings.load_model', side_effect=AssertionError('model')), patch('rag.get_gemini_provider', side_effect=AssertionError('provider')):
            report = build_report(self.saved['environment'])
        self.assertEqual(report['production_sha256']['config.py'], hashlib.sha256(changed).hexdigest())
        self.assertNotEqual(report['production_sha256']['config.py'], hashlib.sha256(original(config_path)).hexdigest())
        self.assertEqual(behavioral_report(report), behavioral_report(self.saved))

    def test_only_production_hashes_excluded_from_behavior(self):
        for field in self.saved:
            changed = copy.deepcopy(self.saved)
            changed[field] = {'deliberate_change': True}
            with self.subTest(field=field):
                self.assertEqual(behavioral_report(changed) == behavioral_report(self.saved), field == 'production_sha256')

    def test_no_runtime_imports_evaluation_and_no_model_loader_in_new_modules(self):
        for source in ROOT.parent.glob("*.py"):
            tree=ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):self.assertFalse((node.module or "").startswith("evaluation"))
                if isinstance(node,ast.Import):self.assertFalse(any(n.name.startswith("evaluation") for n in node.names))
        for name in ("reranking.py","phase16a.py"):
            tree=ast.parse((ROOT/name).read_text(encoding="utf-8"))
            imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            self.assertFalse(any((n or "").startswith(("sentence_transformers","google","qdrant_client")) for n in imports))

    def test_adoption_criteria_not_weakened(self):
        self.assertEqual(ADOPTION_CRITERIA["minimum_recovered_false_negatives"],4)
        self.assertEqual(ADOPTION_CRITERIA["minimum_complete_evidence"],18)
        self.assertEqual(ADOPTION_CRITERIA["minimum_precision"],.875)
        self.assertEqual(ADOPTION_CRITERIA["maximum_new_false_positives"],1)
