# DeepDocs evaluation 1.0.0

Cached MiniLM CPU inference + real in-memory Qdrant + production retrieval validation; NOT Cloud/Gemini evaluation

Cases: 35; chunks: 20; top_k: 5; context budget: 12000.

## Retrieval

{'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.95454545, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.97727273, 'all_evidence_at_5': 1.0}

## Baseline

{'value': 0.5, 'tp': 14, 'tn': 11, 'fp': 2, 'fn': 8, 'precision': 0.875, 'recall': 0.63636364, 'f1': 0.73684211}

## Threshold sweep

| Threshold | TP | TN | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.2 | 22 | 6 | 7 | 0 | 0.75862069 | 1.0 | 0.8627451 |
| 0.25 | 22 | 7 | 6 | 0 | 0.78571429 | 1.0 | 0.88 |
| 0.3 | 21 | 7 | 6 | 1 | 0.77777778 | 0.95454545 | 0.85714286 |
| 0.35 | 19 | 8 | 5 | 3 | 0.79166667 | 0.86363636 | 0.82608696 |
| 0.4 | 18 | 8 | 5 | 4 | 0.7826087 | 0.81818182 | 0.8 |
| 0.45 | 16 | 10 | 3 | 6 | 0.84210526 | 0.72727273 | 0.7804878 |
| 0.5 | 14 | 11 | 2 | 8 | 0.875 | 0.63636364 | 0.73684211 |
| 0.55 | 12 | 12 | 1 | 10 | 0.92307692 | 0.54545455 | 0.68571429 |
| 0.6 | 9 | 13 | 0 | 13 | 1.0 | 0.40909091 | 0.58064516 |
| 0.65 | 7 | 13 | 0 | 15 | 1.0 | 0.31818182 | 0.48275862 |
| 0.7 | 5 | 13 | 0 | 17 | 1.0 | 0.22727273 | 0.37037037 |
| 0.75 | 3 | 13 | 0 | 19 | 1.0 | 0.13636364 | 0.24 |
| 0.8 | 1 | 13 | 0 | 21 | 1.0 | 0.04545455 | 0.08695652 |

## Score distributions

{'answerable': {'count': 22, 'missing': 0, 'minimum': 0.27171542, 'maximum': 0.8221439, 'mean': 0.56045273, 'median': 0.58320176}, 'unsupported': {'count': 13, 'missing': 0, 'minimum': 0.05855029, 'maximum': 0.57503199, 'mean': 0.28540567, 'median': 0.20144689}}

## Categories

| Category | Cases | Hit@1 | Hit@3 | Hit@5 | MRR | FP | FN |
|---|---|---|---|---|---|---|---|
| direct | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 1 |
| paraphrase | 5 | 0.8 | 1.0 | 1.0 | 0.9 | 0 | 4 |
| multi_chunk | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 1 |
| related_unsupported | 5 | N/A | N/A | N/A | N/A | 2 | 0 |
| unrelated | 5 | N/A | N/A | N/A | N/A | 0 | 0 |
| ambiguous | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 2 |
| disambiguation | 5 | 1.0 | 1.0 | 1.0 | 1.0 | 0 | 0 |

## Context and decisions

{'exact_budget_keeps_evidence': True, 'one_character_below_skips_whole_chunk': True, 'bounded': True, 'oversized_and_low_excluded': True, 'relevance_order_preserved': True, 'complete_text_preserved': True, 'duplicate_page_deduplicated': True, 'sources_only_included_pages': True, 'invalid_provenance_excluded': True, 'duplicate_chunk_excluded': True, 'empty_context': True}

All expected evidence survives context for 10 answerable cases.
Provider-call confusion: {'tp': 14, 'tn': 11, 'fp': 2, 'fn': 8}

## Observed failures

{'false_acceptance': ['unsupported-refresh', 'unsupported-rotation'], 'false_rejection': ['direct-password', 'para-signature', 'para-owner', 'para-ocr', 'para-history', 'multi-cloud', 'ambiguous-data', 'ambiguous-remember'], 'incomplete_context_evidence': ['direct-password', 'para-signature', 'para-owner', 'para-ocr', 'para-history', 'multi-storage', 'multi-cloud', 'multi-token', 'multi-deletion', 'multi-backup', 'ambiguous-data', 'ambiguous-remember']}

## Recommendation and limits

Keep production 0.50. Synthetic results are diagnostic, not sufficient evidence for a global threshold change; evaluate independent held-out real-domain cases.

Scores are cosine similarity, not confidence. Mock provider calls measure eligibility, not answer correctness. Related-but-unsupported questions can pass the guard; no threshold proves entailment. Multi-chunk Hit@k counts any expected page; all-evidence coverage is reported separately. The small synthetic benchmark is not universal accuracy, a PDF extraction benchmark, a production Cloud test, or an LLM answer-quality judge.
