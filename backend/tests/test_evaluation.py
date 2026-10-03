"""Deterministic tooling gates; no cached model, network, Cloud, or LLM needed."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import load_dataset, CATEGORIES
from evaluation.retrieval_metrics import retrieval_metrics, valid_hits, covered
from evaluation.rag_metrics import decision_metrics, threshold_sweep, score_distribution
from evaluation.context_checks import context_probes, chunk
from evaluation.runner import evaluate, markdown
from evaluation.adapters import retrieve_dataset
from config import RAGSettings, EmbeddingSettings
from rag import answer_question


def case(name="test", answerable=True):
    return {"id": name, "expected_answerable": answerable,
            "expected_sources": [{"document": "probe.pdf", "page": 1}] if answerable else []}


class MetricTests(unittest.TestCase):
    def test_dataset_version_categories_and_synthetic_provenance(self):
        version, corpus, cases, fixtures = load_dataset()
        self.assertEqual(version, "1.0.0")
        self.assertEqual(len(cases), 35)
        self.assertEqual(len(corpus), 20)
        self.assertEqual({c["category"] for c in cases}, set(CATEGORIES))
        self.assertTrue(all(sum(c["category"] == category for c in cases) == 5 for category in CATEGORIES))
        self.assertEqual(sum(c["expected_answerable"] for c in cases), 22)
        self.assertTrue(all(len(hits) == 5 for hits in fixtures.values()))

    def test_hand_calculated_ranks_missing_and_unsupported_denominator(self):
        a,b,c,d = case("a"),case("b"),case("c"),case("d", False)
        wrong = {**chunk("wrong", "distractor"), "source_filename": "other.pdf"}
        rankings = {"a": [chunk("one", "evidence")], "b": [wrong, chunk("two", "evidence")], "c": [], "d": []}
        result = retrieval_metrics([a,b,c,d], rankings)
        self.assertAlmostEqual(result["hit_at_1"], 1/3)
        self.assertAlmostEqual(result["hit_at_3"], 2/3)
        self.assertAlmostEqual(result["hit_at_5"], 2/3)
        self.assertAlmostEqual(result["mrr"], .5)
        self.assertEqual(result["no_valid_hit_count"], 2)
        self.assertIsNone(retrieval_metrics([d], rankings)["mrr"])

    def test_multiple_evidence_pages_any_hit_vs_all_evidence(self):
        value = case()
        value["expected_sources"].append({"document": "probe.pdf", "page": 3})
        one = chunk("one", "text")
        result = retrieval_metrics([value], {"test": [one]})
        self.assertEqual(result["hit_at_1"], 1)
        self.assertEqual(result["all_evidence_at_5"], 0)
        self.assertEqual(len(covered(value, [{**one,"page_end":3}])), 2)

    def test_malformed_score_or_metadata_and_duplicates_do_not_inflate(self):
        value = chunk("one", "text")
        bad = [None, {}, {**value,"score":True}, {**value,"score":float("nan")},
               {**value,"score":float("inf")}, {**value,"score":".8"}, {**value,"score":1.1},
               {**value,"page_start":0}, {**value,"page_end":False}, {**value,"text":""}]
        self.assertEqual(valid_hits([*bad,value,value]), [value])

    def test_confusion_matrix_threshold_equality_and_missing(self):
        cases = [case("a"),case("b"),case("c",False),case("d",False)]
        scores = {"a":[chunk("a","text",.5)],"b":[],"c":[chunk("c","text",.6)],"d":[chunk("d","text",.1)]}
        result = decision_metrics(cases,scores,.5)
        self.assertEqual({k:result[k] for k in ("tp","tn","fp","fn")},dict(tp=1,tn=1,fp=1,fn=1))
        self.assertEqual((result["precision"],result["recall"],result["f1"]),(.5,.5,.5))
        self.assertEqual(decision_metrics([],{},.5)["f1"],0)
        for threshold in [True,-1,1.1,float("nan")]:
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                decision_metrics(cases,scores,threshold)

    def test_sweep_is_deterministic_and_distribution_counts_missing(self):
        cases=[case("a"),case("b"),case("c",False)]
        rankings={"a":[chunk("a","text",.5)],"b":[],"c":[chunk("c","text",.2)]}
        self.assertEqual([r["value"] for r in threshold_sweep(cases,rankings)],[n/100 for n in range(20,81,5)])
        self.assertEqual(score_distribution(cases,rankings,True),dict(count=2,missing=1,minimum=.5,maximum=.5,mean=.5,median=.5))

    def test_context_boundary_source_and_order_gates(self):
        checks=context_probes()
        self.assertEqual(len(checks),11)
        self.assertTrue(all(checks.values()), checks)


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_fixture_report_reproducibility_and_quality_gates(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network allowed")), patch("embeddings._load_model", side_effect=AssertionError("No model download allowed")):
            first=await evaluate()
            second=await evaluate()
        self.assertEqual(first,second)
        self.assertGreaterEqual(first["retrieval"]["hit_at_1"],.60)
        self.assertEqual(first["retrieval"]["hit_at_3"],1)
        self.assertGreaterEqual(first["retrieval"]["mrr"],.80)
        self.assertEqual(first["baseline_threshold"]["fp"],5)  # Known unsupported overlap, not hidden.
        self.assertEqual(first["baseline_threshold"]["fn"],1)
        self.assertEqual(first["categories"]["unrelated"]["decision"]["tn"],5)
        self.assertEqual(first["categories"]["unrelated"]["decision"]["fp"],0)
        self.assertEqual(first["provider_call_confusion"],{k:first["baseline_threshold"][k] for k in ("tp","tn","fp","fn")})
        self.assertTrue(all(c["sources_match_context"] for c in first["cases"]))
        self.assertTrue(all(c["context_characters"] <=12000 for c in first["cases"]))
        self.assertIn("engineering regression ONLY",first["measurement"])
        self.assertIn("not confidence",markdown(first))
        self.assertEqual(json.loads(json.dumps(first)),first)
        for key in ("owner_id", "vector", "storage_path", "system_instruction", "api_key"):
            self.assertNotIn('"'+key+'":',json.dumps(first))

    async def test_empty_and_low_context_never_construct_provider(self):
        for hits in ([],[chunk("low","text",.14)]):
            with patch("retrieval.search_chunks",AsyncMock(return_value=hits)),patch("rag.get_gemini_provider") as provider:
                result=await answer_question(None,None,None,"Question",EmbeddingSettings(),RAGSettings())
                self.assertEqual(result["status"],"insufficient_context")
                self.assertEqual(result["sources"],[])
                provider.assert_not_called()

    async def test_qualifying_context_can_call_provider_but_is_not_entailment_proof(self):
        provider=SimpleNamespace(answer=AsyncMock(return_value=None))
        with patch("retrieval.search_chunks",AsyncMock(return_value=[chunk("related","Related topic, no exact answer",.7)])),patch("rag.get_gemini_provider",return_value=provider):
            result=await answer_question(None,None,None,"Unsupported exact question",EmbeddingSettings(),RAGSettings())
        provider.answer.assert_awaited_once()
        self.assertEqual(result["status"],"insufficient_context")
        self.assertEqual(result["sources"],[])

    async def test_local_model_is_explicit_offline_only_no_remote_fallback(self):
        version,chunks,cases,fixtures=load_dataset()
        def unavailable(*args,**kwargs):
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])
            raise RuntimeError("not cached")
        with patch.dict(sys.modules,{"sentence_transformers":SimpleNamespace(SentenceTransformer=unavailable)}):
            with self.assertRaises(RuntimeError):
                await retrieve_dataset(chunks,cases,fixtures,"local-model")
