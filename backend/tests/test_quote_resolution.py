import copy
import unittest
from verified_rag import resolve_verification_quotes, validate_verification, VerificationMalformed, QuestionPart
from rag_schemas import AnswerClaim
from test_verified_rag import accepted_payload, context_for, hit

def wire(quote='nine', passage='p1'):
    value=accepted_payload()
    for record in value['claims']+value['coverage']:
        record['evidence']=[dict(passage_id=passage,quote=quote)]
    return value

class QuoteResolutionTests(unittest.TestCase):
    def test_unique_unicode_codepoint_offsets(self):
        context=context_for([hit('😀 café opens at nine.')])
        value=resolve_verification_quotes(wire(),context)
        self.assertEqual(value['claims'][0]['evidence'][0]['start'],16)
        decision=validate_verification(value,draft_claims=[AnswerClaim(text='Opens at nine.',citation_ids=[1])],question_parts=[QuestionPart(part_id=1,text='When?')],context=context)
        self.assertTrue(decision.accepted)

    def test_exact_failure_categories(self):
        cases=[('nine nine','nine','p1','ambiguous_quote'),('aaa','aa','p1','ambiguous_quote'),
            ('nine','Nine','p1','missing_quote'),('a b','a  b','p1','missing_quote'),
            ('é','e\u0301','p1','missing_quote'),('nine','nine','p2','unknown_passage_id'),
            ('nine','','p1','missing_quote'),('nine',' ','p1','missing_quote'),
            ('nine','nine','p99','invalid_evidence_reference')]
        for text,quote,passage,reason in cases:
            with self.subTest(reason=reason,text=text),self.assertLogs('verified_rag') as logs:
                with self.assertRaises(VerificationMalformed) as caught:
                    resolve_verification_quotes(wire(quote,passage),context_for([hit(text)]))
                self.assertEqual(caught.exception.reason,reason)
                self.assertEqual(logs.output,['WARNING:verified_rag:Verification rejected: '+reason])

    def test_wrong_existing_passage_no_fallback(self):
        with self.assertRaises(VerificationMalformed) as caught:
            resolve_verification_quotes(wire(),context_for([hit('different'),hit('nine')]))
        self.assertEqual(caught.exception.reason,'missing_quote')

    def test_multiple_claims_passages_and_strict_postchecks(self):
        context=context_for([hit(),hit('The office closes at five.')])
        value=wire()
        value['claims'].append(dict(claim_id=2,verdict='supported',evidence=[dict(passage_id='p2',quote='five')]))
        value['coverage'].append(dict(part_id=2,status='covered',claim_ids=[2],evidence=[dict(passage_id='p2',quote='five')]))
        draft=[AnswerClaim(text='Opens at nine.',citation_ids=[1]),AnswerClaim(text='Closes at five.',citation_ids=[2])]
        parts=[QuestionPart(part_id=1,text='When open?'),QuestionPart(part_id=2,text='When close?')]
        def validate(v):return validate_verification(resolve_verification_quotes(v,context),draft_claims=draft,question_parts=parts,context=context)
        self.assertTrue(validate(value).accepted)
        for kind in ['duplicate','missing_part','contradiction','extra_offset']:
            bad=copy.deepcopy(value)
            if kind=='duplicate':bad['claims'][0]['evidence']*=2
            if kind=='missing_part':bad['coverage'].pop()
            if kind=='contradiction':bad['outcome']='contradicted'
            if kind=='extra_offset':bad['claims'][0]['evidence'][0]['start']=0
            with self.subTest(kind=kind),self.assertRaises(VerificationMalformed):validate(bad)
