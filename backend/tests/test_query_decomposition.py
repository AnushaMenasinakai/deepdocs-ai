"""Pure Phase 15A query planning tests. No model or network."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.query_decomposition import decompose, MAX_QUERIES, STRATEGIES


class DecompositionTests(unittest.TestCase):
    def test_single_questions_and_related_concepts_left_intact(self):
        for question in ("Which algorithm stores password hashes?", "Research and development",
                         "How does JWT authentication work and why is its signature checked?",
                         "How does JWT authentication work, and why is its signature checked?",
                         "How do irrigation and fertilizer improve rice farming?",
                         "Where are PDF originals kept, and where are their embedding vectors kept?"):
            for strategy in STRATEGIES:
                with self.subTest(question=question,strategy=strategy):
                    self.assertEqual(decompose(question,strategy),[question])

    def test_clear_three_topics_retains_original_without_synonyms(self):
        question="Explain JWT authentication, MongoDB storage, and cloud service models."
        self.assertEqual(decompose(question),[question,"JWT authentication","MongoDB storage","cloud service models"])
        self.assertEqual(decompose(question,"clauses"),[question])

    def test_unsupported_topic_list_is_not_mistaken_for_a_safety_judge(self):
        question="Explain JWT signing-key rotation and emergency key revocation."
        self.assertEqual(decompose(question),[question,"JWT signing-key rotation","emergency key revocation"])
        # Rule recognizes syntax only. The measured OR-gate false positive is reported.

    def test_explicit_clauses_and_shared_frame(self):
        for separator in ("; ","? ",", and "):
            q="Where are PDFs stored"+separator+"how long do access tokens last?"
            self.assertEqual(decompose(q,"clauses"),[q,"Where are PDFs stored","how long do access tokens last"])
        q="Compare what the customer manages in IaaS and PaaS."
        self.assertEqual(decompose(q,"topics"),[q,"Compare what the customer manages in IaaS","Compare what the customer manages in PaaS"])

    def test_bounded_no_empty_duplicate_or_random_queries(self):
        values=["Explain alpha topic, beta topic, gamma topic, delta topic, and fifth topic.",
                "Explain alpha topic, alpha topic, and beta topic.",
                "How does storage work; how does storage work; where are vectors kept?",
                "Explain alpha topic, , and beta topic."]
        for q in values:
            for strategy in STRATEGIES:
                with self.subTest(q=q,strategy=strategy):
                    first=decompose(q,strategy)
                    self.assertEqual(first,decompose(q,strategy));self.assertEqual(first[0],q)
                    self.assertLessEqual(len(first),MAX_QUERIES)
                    self.assertTrue(all(text.strip() for text in first))
                    self.assertEqual(len(first),len({t.casefold().rstrip("?. ") for t in first}))
        self.assertEqual(len(decompose(values[0])),4)

    def test_invalid_input_is_explicit(self):
        for value in (None,"", " ", "x"*1001):
            with self.subTest(value=str(value)[:10]),self.assertRaises(ValueError):decompose(value)
        with self.assertRaises(ValueError):decompose("valid", "unbounded")
