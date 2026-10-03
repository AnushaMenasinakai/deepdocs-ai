# DeepDocs evaluation 1.0.0

Authored fixture scores; engineering regression ONLY

Cases: 35; chunks: 20; top_k: 5; context budget: 12000.

## Retrieval

{'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.63636364, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.81818182, 'all_evidence_at_5': 1.0}

## Baseline

{'value': 0.5, 'tp': 21, 'tn': 8, 'fp': 5, 'fn': 1, 'precision': 0.80769231, 'recall': 0.95454545, 'f1': 0.875}

## Threshold sweep

| Threshold | TP | TN | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.2 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.25 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.3 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.35 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.4 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.45 | 22 | 5 | 8 | 0 | 0.73333333 | 1.0 | 0.84615385 |
| 0.5 | 21 | 8 | 5 | 1 | 0.80769231 | 0.95454545 | 0.875 |
| 0.55 | 21 | 8 | 5 | 1 | 0.80769231 | 0.95454545 | 0.875 |
| 0.6 | 21 | 8 | 5 | 1 | 0.80769231 | 0.95454545 | 0.875 |
| 0.65 | 21 | 8 | 5 | 1 | 0.80769231 | 0.95454545 | 0.875 |
| 0.7 | 21 | 13 | 0 | 1 | 1.0 | 0.95454545 | 0.97674419 |
| 0.75 | 21 | 13 | 0 | 1 | 1.0 | 0.95454545 | 0.97674419 |
| 0.8 | 21 | 13 | 0 | 1 | 1.0 | 0.95454545 | 0.97674419 |

## Score distributions

{'answerable': {'count': 22, 'missing': 0, 'minimum': 0.48, 'maximum': 0.82, 'mean': 0.80454545, 'median': 0.82}, 'unsupported': {'count': 13, 'missing': 0, 'minimum': 0.14, 'maximum': 0.66, 'mean': 0.41153846, 'median': 0.45}}

## Categories

| Category | Cases | Hit@1 | Hit@3 | Hit@5 | MRR | FP | FN |
|---|---|---|---|---|---|---|---|
| direct | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 0 |
| paraphrase | 5 | 0.4 | 1.0 | 1.0 | 0.7 | 0 | 0 |
| multi_chunk | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 0 |
| related_unsupported | 5 | N/A | N/A | N/A | N/A | 5 | 0 |
| unrelated | 5 | N/A | N/A | N/A | N/A | 0 | 0 |
| ambiguous | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 1 |
| disambiguation | 5 | 0.0 | 1.0 | 1.0 | 0.5 | 0 | 0 |

## Context and decisions

{'exact_budget_keeps_evidence': True, 'one_character_below_skips_whole_chunk': True, 'bounded': True, 'oversized_and_low_excluded': True, 'relevance_order_preserved': True, 'complete_text_preserved': True, 'duplicate_page_deduplicated': True, 'sources_only_included_pages': True, 'invalid_provenance_excluded': True, 'duplicate_chunk_excluded': True, 'empty_context': True}

All expected evidence survives context for 21 answerable cases.
Provider-call confusion: {'tp': 21, 'tn': 8, 'fp': 5, 'fn': 1}

## Observed failures

{'false_acceptance': ['unsupported-refresh', 'unsupported-rotation', 'unsupported-region', 'unsupported-price', 'unsupported-recovery'], 'false_rejection': ['ambiguous-remember'], 'incomplete_context_evidence': ['ambiguous-remember']}

## Recommendation and limits

Keep production 0.50. Synthetic results are diagnostic, not sufficient evidence for a global threshold change; evaluate independent held-out real-domain cases.

Scores are cosine similarity, not confidence. Mock provider calls measure eligibility, not answer correctness. Related-but-unsupported questions can pass the guard; no threshold proves entailment. Multi-chunk Hit@k counts any expected page; all-evidence coverage is reported separately. The small synthetic benchmark is not universal accuracy, a PDF extraction benchmark, a production Cloud test, or an LLM answer-quality judge.
