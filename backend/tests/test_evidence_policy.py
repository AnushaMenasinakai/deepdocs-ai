"""Phase 13 deterministic safety gates; no models, network, or live providers."""
from citation_helpers import claims_for
import copy
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config import RAGSettings, EmbeddingSettings, ConfigurationError, load_rag_settings
from rag_context import build_context, RAGFailure
from rag import answer_question, supporting_sources
from evaluation.phase13 import measured_rankings, compare, candidate_context, ROOT
from evaluation.context_checks import chunk
import test_rag as helpers
# Load shared fixture dependencies before the historical evaluator temporarily
# replaces sys.modules; NumPy native modules cannot be unloaded and reimported.
import qdrant_helpers


class EvidencePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_cannot_be_bypassed_by_secondary_including_boundary(self):
        for score in (.14,.30,.499999):
            with self.subTest(score=score), patch('retrieval.search_chunks',AsyncMock(return_value=[chunk('weak','Only weak material',score)])), patch('rag.get_gemini_provider') as provider:
                result=await answer_question(None,None,None,'Question',EmbeddingSettings(),RAGSettings())
                self.assertEqual(result['status'],'insufficient_context'); self.assertEqual(result['sources'],[])
                provider.assert_not_called()
        self.assertTrue(build_context([chunk('boundary','Qualifying',.50)],RAGSettings()).chunks)

    async def test_secondary_evidence_order_sources_duplicate_and_cutoff(self):
        values=[chunk('lead','Primary',.8),chunk('next','Secondary',.30,2),chunk('excluded','Too weak',.299,3),chunk('duplicate-page','More primary-page evidence',.4)]
        context=build_context(values,RAGSettings())
        self.assertEqual([c['text'] for c in context.chunks],['Primary','More primary-page evidence','Secondary'])
        self.assertEqual([s['page_start'] for s in supporting_sources(context)],[1,2])
        self.assertEqual(len(build_context([values[0],values[0],values[1]],RAGSettings()).chunks),2)
        self.assertEqual(json.loads(context.serialized),list(context.chunks))

    async def test_malformed_primary_cannot_unlock_valid_weak_evidence(self):
        for bad in ({'page_start':0},{'text':'\ud800'},{'source_filename':'../private.pdf'},{'score':True},{'score':float('nan')},{'score':float('inf')},{'document_id':'bad'}):
            with self.subTest(bad=repr(bad)):
                self.assertFalse(build_context([{**chunk('strong','Text',.9),**bad},chunk('weak','Text',.4)],RAGSettings()).chunks)

    async def test_complete_chunks_budget_and_sources_only_included(self):
        values=[chunk('primary','Primary evidence',.9),chunk('large','x'*13000,.45,2),chunk('small','Small secondary',.35,3)]
        full=build_context(values,RAGSettings())
        self.assertEqual([s['page_start'] for s in supporting_sources(full)],[1,3])
        exact=len(full.serialized)
        self.assertEqual(len(build_context(values,RAGSettings(max_context_chars=exact)).chunks),2)
        smaller=build_context(values,RAGSettings(max_context_chars=exact-1))
        self.assertEqual([s['page_start'] for s in supporting_sources(smaller)],[1])
        self.assertFalse(build_context([chunk('huge','x'*13000,.9)],RAGSettings()).chunks)
        self.assertLessEqual(len(full.serialized),12000)

    async def test_all_measured_provider_decisions_match_unchanged_primary_gate(self):
        cases,rankings,_=measured_rankings()
        for case in cases:
            provider=SimpleNamespace(answer=AsyncMock(side_effect=claims_for('Synthetic validated answer')))
            hits=rankings[case['id']]
            with self.subTest(case=case['id']), patch('retrieval.search_chunks',AsyncMock(return_value=hits)),patch('rag.get_gemini_provider',return_value=provider) as factory:
                result=await answer_question(None,None,None,case['question'],EmbeddingSettings(),RAGSettings())
                if hits[0]['score']<.5:
                    factory.assert_not_called(); self.assertEqual(result['status'],'insufficient_context')
                else:
                    provider.answer.assert_awaited_once()
                    self.assertEqual(provider.answer.call_args.args[1].chunks,candidate_context(hits,.30).chunks)


class EvidenceConfigurationTests(unittest.TestCase):
    def test_defaults_valid_overrides_and_safe_invalid_configuration(self):
        with patch('config.dotenv_values',return_value={}),patch.dict(os.environ,{},clear=True):
            self.assertEqual(load_rag_settings().min_evidence_score,.30)
        for value in ('0','0.30','0.50'):
            with patch('config.dotenv_values',return_value={'RAG_MIN_EVIDENCE_SCORE':value}),patch.dict(os.environ,{},clear=True):
                self.assertEqual(load_rag_settings().min_evidence_score,float(value))
        for value in ('0.51','-0.1','1.1','private','nan','inf'):
            with self.subTest(value=value),patch('config.dotenv_values',return_value={'RAG_MIN_EVIDENCE_SCORE':value}),patch.dict(os.environ,{},clear=True):
                with self.assertRaises(ConfigurationError) as error: load_rag_settings()
                self.assertEqual(str(error.exception),'Invalid question-answering configuration.')
        with patch('config.dotenv_values',return_value={'RAG_MIN_EVIDENCE_SCORE':'.2'}),patch.dict(os.environ,{'RAG_MIN_EVIDENCE_SCORE':'.4'},clear=True):
            self.assertEqual(load_rag_settings().min_evidence_score,.4)
        with self.assertRaises(RAGFailure):build_context([],RAGSettings(min_evidence_score=.6))


class ComparisonTests(unittest.TestCase):
    def test_historical_files_preserved_and_replay_reproducible(self):
        report=json.loads((ROOT/'reports/phase13-comparison.json').read_text())
        for name,digest in report['baseline_text_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT/'reports'/name).read_text(encoding='utf-8').encode()).hexdigest(),digest)
        cases,rankings,baseline=measured_rankings()
        with patch('socket.socket.connect',side_effect=AssertionError('No network')),patch('embeddings.load_model',side_effect=AssertionError('No inference')):
            result=compare(cases,rankings)
            self.assertEqual(result,compare(cases,rankings))
        for key,value in result.items(): self.assertEqual(report[key],value)
        old=next(r for r in result['candidates'] if r['evidence_threshold']==.50)
        self.assertEqual(old['complete_evidence_count'],baseline['context_all_evidence_count'])
        self.assertEqual(old['multi_chunk_complete'],0)
        historical={row['id']:row for row in baseline['cases']}
        for row in old['cases']:
            self.assertEqual(row['context_count'],historical[row['id']]['context_chunk_count'])
            self.assertEqual(row['context_characters'],historical[row['id']]['context_characters'])
        self.assertAlmostEqual(result['retrieval']['mrr'],baseline['retrieval']['mrr'],places=7)
        self.assertEqual(result['loss_diagnosis']['complete_after_authoritative_validation'],22)
        self.assertEqual(result['loss_diagnosis']['budget_exclusions'],0)
        selected=next(r for r in result['candidates'] if r['evidence_threshold']==.30)
        self.assertEqual((selected['complete_evidence_count'],selected['multi_chunk_complete']),(14,4))
        self.assertEqual(selected['new_non_required_chunks'],39)
        self.assertEqual(selected['new_non_required_on_unsupported'],7)
        self.assertTrue(all(result['selection_gates'].values()))
        self.assertEqual(selected['provider_eligible_confusion'],{'tp':14,'tn':11,'fp':2,'fn':8})


class EvidenceHistoryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=helpers.AskTests.asyncSetUp
    asyncTearDown=helpers.AskTests.asyncTearDown
    setup_ask=helpers.AskTests.setup_ask
    ask=helpers.AskTests.ask

    async def test_new_history_snapshots_secondary_sources_old_history_unchanged(self):
        await self.setup_ask()
        first=chunk('old','Primary original',.8)
        provider=SimpleNamespace(answer=AsyncMock(side_effect=claims_for('Grounded answer')))
        with patch('retrieval.search_chunks',AsyncMock(return_value=[first])),patch('rag.get_gemini_provider',return_value=provider):
            self.assertEqual((await self.ask())[0],200)
        old=copy.deepcopy(self.history.documents)
        values=[first,chunk('secondary','New evidence',.35,2)]
        with patch('retrieval.search_chunks',AsyncMock(return_value=values)) as retrieve,patch('rag.get_gemini_provider',return_value=provider):
            status,body,_=await self.ask({'question':'A separate question'})
        self.assertEqual(status,200);self.assertEqual(len(body['sources']),2)
        self.assertEqual(len(self.history.documents),2)
        for key,value in old.items():self.assertEqual(self.history.documents[key],value)
        new=next(v for k,v in self.history.documents.items() if k not in old)
        self.assertEqual(new['sources'],body['sources'])
        self.assertEqual(retrieve.call_args.args[3].query,'A separate question')
        self.assertEqual(retrieve.call_args.args[3].top_k,5)
        self.assertEqual(provider.answer.call_args.args[0],'A separate question')
        self.assertNotIn('Grounded answer',provider.answer.call_args.args[1].serialized)
