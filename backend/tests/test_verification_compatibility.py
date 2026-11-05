"""Offline provider compatibility and safe deterministic diagnostics."""
import copy
import json
import unittest
from google.genai import types
from pydantic import ValidationError
from gemini_provider import verification_response_schema
from verified_rag import ProviderVerificationPayload, VerificationPayload, QuestionPart, validate_verification, VerificationMalformed
from rag_schemas import AnswerClaim
from test_verified_rag import accepted_payload, context_for, hit

class SchemaCompatibilityTests(unittest.TestCase):
    def test_shape_and_sdk_serialization(self):
        strict = ProviderVerificationPayload.model_json_schema()
        simple = verification_response_schema()
        def shape(node):
            if '$ref' in node:
                return shape(strict['$defs'][node['$ref'].split('/')[-1]])
            return (node.get('type'), node.get('enum'), node.get('required'),
                    {k: shape(v) for k, v in node.get('properties', {}).items()},
                    shape(node['items']) if 'items' in node else None)
        self.assertEqual(shape(strict), shape(simple))
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_json_schema=simple)
        self.assertEqual(json.loads(config.model_dump_json())["response_json_schema"], simple)
        def check(node):
            self.assertLessEqual(set(node), {'type', 'enum', 'required', 'properties', 'items'})
            for child in node.get('properties', {}).values(): check(child)
            if 'items' in node: check(node['items'])
        check(simple)
        self.assertEqual(ProviderVerificationPayload.model_json_schema(), strict)

    def test_internal_constraints_remain(self):
        for field, value in [('passage_id', 'p99'), ('start', -1), ('quote', ''), ('extra', 'secret')]:
            with self.subTest(field=field):
                payload = accepted_payload()
                payload['claims'][0]['evidence'][0][field] = value
                with self.assertRaises(ValidationError): VerificationPayload.model_validate(payload)

class DiagnosticTests(unittest.TestCase):
    def test_categories_and_no_sensitive_output(self):
        cases = []
        def case(reason, mutate):
            payload = accepted_payload(); mutate(payload); cases.append((reason, payload))
        case('unknown_passage_id', lambda p: p['claims'][0]['evidence'][0].update(passage_id='p2'))
        case('quote_mismatch', lambda p: p['claims'][0]['evidence'][0].update(quote='SECRET'))
        case('quote_span_out_of_bounds', lambda p: p['claims'][0]['evidence'][0].update(end=500))
        case('duplicate_verdict', lambda p: p['claims'].append(copy.deepcopy(p['claims'][0])))
        case('question_part_set_mismatch', lambda p: p['coverage'][0].update(part_id=2))
        case('duplicate_question_part', lambda p: p['coverage'].append(copy.deepcopy(p['coverage'][0])))
        case('citation_mismatch', lambda p: p['claims'][0].update(evidence=[]))
        case('invalid_claim_coverage', lambda p: p['coverage'][0].update(claim_ids=[2]))
        case('inconsistent_outcome_or_coverage', lambda p: p.update(outcome='contradicted'))
        case('invalid_structure', lambda p: p.update(private='SECRET'))
        for reason, payload in cases:
            with self.subTest(reason=reason):
                with self.assertLogs('verified_rag', level='WARNING') as logs:
                    with self.assertRaises(VerificationMalformed) as caught:
                        validate_verification(payload, draft_claims=[AnswerClaim(text='It opens at nine.', citation_ids=[1])],
                            question_parts=[QuestionPart(part_id=1,text='When does it open?')], context=context_for([hit()]))
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(str(caught.exception), 'Verification response is unusable.')
                self.assertEqual(logs.output, ['WARNING:verified_rag:Verification rejected: ' + reason])
                self.assertNotIn('SECRET', str(caught.exception) + ''.join(logs.output))
