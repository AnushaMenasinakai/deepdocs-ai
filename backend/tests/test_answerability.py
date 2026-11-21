"""Answerability contracts; mocked verdicts do not prove model entailment."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest
import test_rag as helpers
from config import GeminiSettings, EmbeddingSettings, RAGSettings
from gemini_provider import GeminiProvider, SYSTEM_INSTRUCTIONS
from rag_context import build_context
from rag_citations import citation_context
from rag import INSUFFICIENT_ANSWER

class AnswerabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=helpers.AskTests()
        await self.fixture.asyncSetUp()
        self.addCleanup(self.fixture.doCleanups)
        await self.fixture.setup_ask()

    async def asyncTearDown(self):
        await self.fixture.asyncTearDown()

    async def exercise(self, question, hits, payload=None, error=None):
        generate=AsyncMock(side_effect=error, return_value=helpers.response(json.dumps(payload)))
        client=SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
        with patch('google.genai.Client',return_value=client):
            provider=GeminiProvider(GeminiSettings('test-private-key'))
        with patch('retrieval.search_chunks',AsyncMock(return_value=hits)), patch('rag.get_gemini_provider',return_value=provider):
            result=await self.fixture.ask({'question':question})
        self.assertLessEqual(generate.await_count,1)
        return result,generate

    async def test_supported_claim_cites_only_supporting_source(self):
        self.assertEqual(EmbeddingSettings().provider,'local')
        hits=[helpers.hit('Alex coordinates Aurora.',.8),helpers.hit('Seeds are refrigerated.',.7)]
        (status,body,_),call=await self.exercise('Who coordinates Aurora?',hits,
            {'supported':True,'claims':[{'text':'Alex coordinates Aurora.','citation_ids':[1]}]})
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'answered')
        self.assertEqual([s['document_id'] for s in body['sources']],[hits[0]['document_id']])
        call.assert_awaited_once()
        self.assertEqual(call.call_args.kwargs['config'].system_instruction,SYSTEM_INSTRUCTIONS)

    async def test_misleading_similarity_missing_attribute_and_multi_part_refusals(self):
        for question in ('What is the orbital period of Neptune?', "What is Alex's email address?",
                         'Who coordinates Aurora and what is their email address?'):
            with self.subTest(question=question):
                (status,body,_),call=await self.exercise(question,[helpers.hit('Alex coordinates Aurora.',.95)],
                    {'supported':False,'claims':[]})
                self.assertEqual(status,200)
                self.assertEqual(body['answer'],INSUFFICIENT_ANSWER)
                self.assertEqual(body['sources'],[])
                self.assertEqual(body['claims'],[])
                call.assert_awaited_once()

    async def test_empty_evidence_bypasses_provider_including_direct_call(self):
        (status,body,_),call=await self.exercise('Who coordinates Aurora?',[],{'supported':False,'claims':[]})
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'insufficient_context')
        call.assert_not_awaited()
        with patch('google.genai.Client'):
            provider=GeminiProvider(GeminiSettings('test-key'))
        self.assertIsNone(await provider.answer('question',citation_context(build_context([],RAGSettings()))))
        provider.client.aio.models.generate_content.assert_not_called()

    async def test_forged_citation_and_contradictory_refusal_envelope_are_errors(self):
        for payload in (
            {'supported':True,'claims':[{'text':'Unsupported claim','citation_ids':[99]}]},
            {'supported':False,'claims':[{'text':'Unsupported claim','citation_ids':[1]}]}):
            before=len(self.fixture.history.documents)
            (status,body,_),_=await self.exercise('question',[helpers.hit(score=.9)],payload)
            self.assertEqual(status,503)
            self.assertNotEqual(body.get('status'),'insufficient_context')
            self.assertEqual(len(self.fixture.history.documents),before)

    async def test_provider_503_is_not_an_evidence_refusal(self):
        from google.genai.errors import ServerError
        (status,body,_),call=await self.exercise('question',[helpers.hit(score=.9)],
            error=ServerError(503,{'error':{'message':'PRIVATE'}}))
        self.assertEqual(status,503)
        self.assertNotIn('PRIVATE',str(body))
        self.assertNotEqual(body.get('status'),'insufficient_context')
        self.assertEqual(len(self.fixture.history.documents),0)
        call.assert_awaited_once()
