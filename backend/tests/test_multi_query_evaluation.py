"""Offline replay gates for Phase 15A. No cached model required by unit tests."""
import ast
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.dataset import public_chunk, load_dataset
from evaluation.multi_query import (ROOT, fuse, gate, experimental_context, compare, replay,
                                    historical_hashes, fingerprints, measure, PRIMARY, SECONDARY)
from rag_context import build_context


def hit(name="a", score=.8, page=1, text="Synthetic text"):
    return {**public_chunk(dict(id=name,document="test.pdf",page=page,text=text)),"score":score,"rank":1}


class FusionTests(unittest.TestCase):
    def test_duplicate_identity_max_score_and_contributions(self):
        a,b=hit(),hit("b",.7,2)
        top,union=fuse([[a,b],[{**a,"score":.9}]],"max")
        self.assertEqual(len(top),2);self.assertEqual(len(union),2)
        self.assertEqual(top[0]["score"],.9)
        self.assertEqual([c["query_index"] for c in top[0]["contributions"]],[0,1])
        self.assertAlmostEqual(top[0]["fusion_score"],2/61)
        self.assertEqual(top[0]["document_id"],a["document_id"])

    def test_rrf_order_and_cosine_are_separate(self):
        a,b=hit("a",.9),hit("b",.4,2)
        top,_=fuse([[a,b],[b]],"rrf")
        self.assertEqual(top[0]["chunk_id"],b["chunk_id"])
        self.assertEqual(top[0]["score"],.4)
        self.assertFalse(gate([[b],[b]],"or")[0])
        self.assertTrue(gate([[a,b],[b]],"or")[0])

    def test_stable_ties_determinism_and_unique_limit(self):
        rows=[[hit(f"{q}-{i}",.8,i+1) for i in range(5)] for q in range(4)]
        for method in ("max","rrf"):
            top,union=fuse(rows,method)
            self.assertEqual((top,union),fuse(rows,method));self.assertEqual(len(top),5);self.assertEqual(len(union),20)
            self.assertEqual(len({h["chunk_id"] for h in top}),5)
            expected=(sorted(h["chunk_id"] for h in union)[:5] if method=="max" else
                      sorted(r[0]["chunk_id"] for r in rows)+[min(r[1]["chunk_id"] for r in rows)])
            self.assertEqual([h["chunk_id"] for h in top],expected)

    def test_conflicting_provenance_and_unbounded_inputs_rejected(self):
        a=hit()
        with self.assertRaises(ValueError):fuse([[a],[{**a,"text":"forged"}]])
        with self.assertRaises(ValueError):fuse([[a]]*5)
        with self.assertRaises(ValueError):fuse([[a]],"unknown")
        top,_=fuse([[{**a,"score":True},{**a,"score":float("nan")},a]])
        self.assertEqual(len(top),1)


class GateAndEvidenceTests(unittest.TestCase):
    def test_original_fail_subquery_pass_and_unsupported_or_hazard(self):
        rows=[[hit(score=.3)],[hit("fragment",.8)]]
        self.assertEqual(gate(rows,"original"),(False,[.3,.8]))
        self.assertEqual(gate(rows,"or"),(True,[.3,.8]))
        self.assertTrue(gate(list(reversed(rows)),"original")[0])
        self.assertFalse(gate([[],[hit(score=.49999)]],"or")[0])
        self.assertTrue(gate([[hit(score=.5)]],"original")[0])
        with self.assertRaises(ValueError):gate(rows,"rrf-threshold")

    def test_secondary_budget_complete_chunks_order_and_dedup(self):
        values=[hit(),hit("too-large",.7,2,"x"*13000),hit("edge",.3,3),hit("weak",.299,4)]
        context=experimental_context(values,True)
        self.assertEqual([c["page_start"] for c in context.chunks],[1,3])
        self.assertEqual(len(experimental_context([values[0],values[0]],True).chunks),1)
        self.assertFalse(experimental_context(values,False).chunks)
        exact=len(context.serialized)
        self.assertEqual(len(experimental_context(values,True,exact).chunks),2)
        self.assertEqual(len(experimental_context(values,True,exact-1).chunks),1)
        self.assertLessEqual(len(context.serialized),12000)

    def test_original_only_packing_matches_production_for_all_measured_cases(self):
        data=json.loads((ROOT/'phase15a_rankings.json').read_text(encoding='utf-8'))
        rankings=replay(data)
        for hits in rankings.values():
            settings=SimpleNamespace(top_k=5,min_relevance_score=.5,min_evidence_score=.3,max_context_chars=12000)
            self.assertEqual(experimental_context(hits,gate([hits],"original")[0]),build_context(hits,settings))


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=json.loads((ROOT/'phase15a_rankings.json').read_text(encoding='utf-8'))

    def test_reproducible_report_offline_and_original_baseline_preserved(self):
        with patch('socket.socket.connect',side_effect=AssertionError('network')),patch('embeddings.load_model',side_effect=AssertionError('inference')):
            first=compare(self.data);second=compare(self.data)
        self.assertEqual(first,second)
        committed=json.loads((ROOT/'reports/phase15a-multi-query.json').read_text(encoding='utf-8'))
        self.assertEqual(first,committed)
        self.assertTrue(first['baseline_parity'])
        self.assertEqual(first['historical_hashes'],historical_hashes())
        self.assertEqual(first['recommendation'],'B — DO NOT INTEGRATE')
        self.assertEqual(len(first['baseline_false_negatives']),8)
        for c in first['candidates']:
            self.assertEqual((c['decision']['tp'],c['decision']['tn'],c['decision']['fp'],c['decision']['fn']),(14,11,2,8))
            self.assertEqual((c['complete_evidence'],c['multi_complete']),(14,4))
            self.assertLessEqual(c['maximum_queries'],4);self.assertLessEqual(c['maximum_context_characters'],12000)
            self.assertEqual(c['recovered_false_negatives'],[])
        safety=next(c for c in first['safety_candidates'] if c['name']=='combined/max/or')
        self.assertEqual(set(safety['new_false_positives']),{'safety-unsupported-rotation','safety-mixed-supported-unsupported'})
        self.assertEqual(safety['decision']['fp'],3)

    def test_corrupt_replay_inputs_fail_closed(self):
        for field in ('fingerprints','historical_hashes'):
            data=copy.deepcopy(self.data);data[field]={}
            with self.subTest(field=field),self.assertRaises(ValueError):replay(data)
        data=copy.deepcopy(self.data);next(iter(data['queries'].values()))['hits'][0]['score']=float('nan')
        with self.assertRaises(ValueError):replay(data)

    def test_safety_cases_are_separate_and_original_dataset_unmodified(self):
        version,corpus,cases,_=load_dataset()
        self.assertEqual((version,len(cases),len(corpus)),('1.0.0',35,20))
        safety=json.loads((ROOT/'phase15a_safety.json').read_text())['cases']
        self.assertEqual(len(safety),10)
        self.assertFalse({c['id'] for c in cases}&{c['id'] for c in safety})
        known={(c['document'],c['page']) for c in corpus}
        self.assertTrue(all((s['document'],s['page']) in known for c in safety for s in c['expected_sources']))

    def test_production_modules_never_import_experiment(self):
        for path in ROOT.parent.glob('*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8'))
            names=[]
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):names.extend(a.name for a in node.names)
                if isinstance(node,ast.ImportFrom):names.append(node.module or '')
            self.assertFalse(any(name.startswith('evaluation') for name in names),path.name)


class MeasurementIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_measurement_blocks_sockets_before_retrieval(self):
        async def fake(*args):
            self.assertEqual(args[-1],'local-model')
            with socket.socket() as connection:
                with self.assertRaises(RuntimeError):connection.connect(('example.invalid',443))
            raise ValueError('Synthetic stop before any model loading')
        with patch('evaluation.adapters.retrieve_dataset',side_effect=fake):
            with self.assertRaisesRegex(ValueError,'Synthetic stop'):await measure()
