"""Recorded real-score replay and fake adapter tests; never download/load weights."""
import copy
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation import cross_encoder_local as adapter
from evaluation.phase16r import (ROOT,SCORES,TIMING,compare,scored_rankings,grid,
    choose_development,historical_hashes,candidate_sets)
from evaluation.reranking import rerank,select_context,threshold_sweep
from evaluation.phase16a import SETTINGS
from rag_context import build_context


class LocalCrossEncoderAdapterTests(unittest.TestCase):
    def test_constructor_is_actual_cross_encoder_local_only_identity(self):
        factory=Mock();identity=object()
        adapter.construct_model(Path("synthetic-snapshot"),factory,identity)
        args,kwargs=factory.call_args
        self.assertEqual(args,(str(Path("synthetic-snapshot")),))
        self.assertTrue(kwargs["local_files_only"])
        self.assertFalse(kwargs["trust_remote_code"])
        self.assertIs(kwargs["activation_fn"],identity)
        self.assertEqual(kwargs["device"],"cpu")
        self.assertEqual(kwargs["max_length"],512)
        self.assertEqual(kwargs["model_kwargs"],{"use_safetensors":True})

    def test_loading_path_reuses_installed_class_no_network(self):
        model=Mock();model.model.config.num_labels=1
        factory=Mock(return_value=model)
        torch=Mock();torch.nn.Identity.return_value=object()
        modules={"torch":torch,"sentence_transformers":types.SimpleNamespace(CrossEncoder=factory)}
        with patch.dict(sys.modules,modules),patch.dict(os.environ,{},clear=False),patch.object(adapter,"snapshot",return_value=Path("synthetic")),patch("socket.socket.connect",side_effect=AssertionError("network")):
            self.assertIs(adapter.load(),model)
            self.assertEqual(os.environ["HF_HUB_OFFLINE"],"1")
            self.assertEqual(os.environ["TRANSFORMERS_OFFLINE"],"1")
        torch.use_deterministic_algorithms.assert_called_once_with(True)
        model.model.eval.assert_called_once()
        self.assertTrue(factory.call_args.kwargs["local_files_only"])

    def test_scalar_pair_order_and_batch_limit(self):
        model=Mock();model.predict.return_value=[3.,-2.]
        pairs=[("first","passage a"),("second","passage b")]
        self.assertEqual(adapter.score_pairs(model,pairs),[3.,-2.])
        model.predict.assert_called_once_with(pairs,batch_size=16,show_progress_bar=False,convert_to_numpy=True)
        for bad in ([],pairs*113,[("q","")],["text"]):
            with self.assertRaises(ValueError):adapter.score_pairs(model,bad)
        for bad in ([float("nan"),1],[float("inf"),1],[True,1],[[1],[2]],[1],"1"):
            model.predict.return_value=bad
            with self.assertRaises(ValueError):adapter.score_pairs(model,pairs)

    def test_approved_model_identity_and_files(self):
        self.assertEqual(adapter.REQUESTED_MODEL,"cross-encoder/ms-marco-MiniLM-L-6-v2")
        self.assertEqual(adapter.MODEL,"cross-encoder/ms-marco-MiniLM-L6-v2")
        self.assertEqual(len(adapter.REVISION),40)
        self.assertIn("model.safetensors",adapter.FILES)
        self.assertNotIn("pytorch_model.bin",adapter.FILES)
        self.assertTrue(adapter.CACHE.is_relative_to(ROOT.parent/".cache"))


class RealScoreReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads(SCORES.read_text(encoding="utf-8"))
        cls.timing=json.loads(TIMING.read_text(encoding="utf-8"))
        cls.saved=json.loads((ROOT/"reports/phase16a-reranking-measured.json").read_text(encoding="utf-8"))

    def test_real_measured_report_distinct_from_unavailable_historical(self):
        self.assertEqual(self.data["model"],adapter.MODEL)
        self.assertEqual(self.data["pairs_scored"],225)
        self.assertEqual(self.data["benchmark_pairs"],175)
        self.assertEqual(self.data["safety_pairs"],50)
        self.assertIn("Real local CrossEncoder",self.data["measurement"])
        old=json.loads((ROOT/"reports/phase16a-reranking.json").read_text(encoding="utf-8"))
        self.assertEqual(old["pairs_scored"],0)
        self.assertIsNone(old["reranker_model"])
        self.assertEqual(self.data["historical_hashes"],historical_hashes())

    def test_replay_deterministic_without_models_or_services(self):
        with patch.object(adapter,"load",side_effect=AssertionError("model")),patch.object(adapter,"download",side_effect=AssertionError("download")),patch("socket.socket.connect",side_effect=AssertionError("network")),patch("socket.socket.connect_ex",side_effect=AssertionError("network")):
            first=compare(self.data,self.timing);second=compare(self.data,self.timing)
        self.assertEqual(first,second);self.assertEqual(first,self.saved)

    def test_candidate_identity_order_no_unseen_and_hit5_invariant(self):
        cases,old,safety,safety_old,ranked=scored_rankings(self.data)
        for case in cases+safety:
            original=({**old,**safety_old})[case["id"]]
            rows=ranked[case["id"]]
            self.assertEqual({h["chunk_id"] for h in original},{h["chunk_id"] for h in rows})
            self.assertEqual(len(rows),5)
            self.assertEqual([h["reranker_score"] for h in rows],sorted((h["reranker_score"] for h in rows),reverse=True))
            by_id={h["chunk_id"]:h for h in original}
            for h in rows:
                for field in ("document_id","chunk_id","page_start","page_end","text","score"):
                    self.assertEqual(h[field],by_id[h["chunk_id"]][field])
        self.assertEqual(self.saved["baseline"]["ranking"]["hit_at_5"],self.saved["reranked_ranking"]["hit_at_5"])

    def test_measured_identity_score_and_fingerprint_tampering_rejected(self):
        for field,value in (("model","other"),("candidate_fingerprint","bad"),("revision","bad")):
            data=copy.deepcopy(self.data);data[field]=value
            with self.assertRaises(ValueError):scored_rankings(data)
        for field,value in (("chunk_id","unseen"),("score",float("nan")),("score",True)):
            data=copy.deepcopy(self.data);next(iter(data["scores"].values()))[0][field]=value
            with self.assertRaises(ValueError):scored_rankings(data)

    def test_threshold_grid_development_only_bounded_and_covering(self):
        cases,_,_,_,ranked=scored_rankings(self.data)
        original={c["id"]:ranked[c["id"]] for c in cases}
        values=grid(original)
        self.assertLessEqual(len(values),41)
        self.assertLessEqual(min(values),min(h["reranker_score"] for rows in original.values() for h in rows))
        self.assertGreaterEqual(max(values),max(h["reranker_score"] for rows in original.values() for h in rows))
        self.assertEqual(values,self.saved["thresholds"])
        self.assertEqual(choose_development(self.saved["sweep"]),self.saved["comparator"])

    def test_secondary_cannot_open_primary_and_multiple_pages_retained(self):
        cases,old,_,_,_=scored_rankings(self.data)
        hits=old[cases[0]["id"]]
        rows=rerank("q",hits,lambda pairs:[2.,1.,0.,-1.,-2.])
        self.assertFalse(select_context(rows,3.,evidence_threshold=-2.)[0])
        accepted,context=select_context(rows,2.,evidence_threshold=0.)
        self.assertTrue(accepted);self.assertEqual(len(context.chunks),3)
        self.assertLessEqual(len(context.serialized),12000)
        with self.assertRaises(ValueError):select_context(rows,1.,evidence_threshold=2.)
        with self.assertRaises(ValueError):select_context(rows,1.,evidence_threshold=True)

    def test_single_threshold_and_dual_threshold_sweep_preserve_gate_decisions(self):
        cases,old,_,_,ranked=scored_rankings(self.data)
        baseline={c["id"]:build_context(old[c["id"]],SETTINGS) for c in cases}
        single=threshold_sweep(cases,ranked,baseline,[0.])
        dual=threshold_sweep(cases,ranked,baseline,[0.],evidence_offset=2.)
        self.assertEqual(single[0]["decision"],dual[0]["decision"])
        self.assertGreaterEqual(dual[0]["complete_evidence"],single[0]["complete_evidence"])
        self.assertEqual(dual[0]["evidence_threshold"],-2.)
        with self.assertRaises(ValueError):threshold_sweep(cases,ranked,baseline,[0.],evidence_offset=-1.)

    def test_safety_not_used_for_selection(self):
        altered=copy.deepcopy(self.data)
        safety_ids={c["id"] for c in candidate_sets()[2]}
        for key in safety_ids:
            for row in altered["scores"][key]:row["score"]=-100.
        report=compare(altered,self.timing)
        self.assertEqual(report["thresholds"],self.saved["thresholds"])
        self.assertEqual(report["comparator"],self.saved["comparator"])
