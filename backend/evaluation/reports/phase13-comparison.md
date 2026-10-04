# Phase 13 secondary evidence comparison

Primary gate 0.50; top-k 5; context budget 12000. Replay uses the committed cached MiniLM measured rankings, not authored fixture scores. No Gemini calls.

| Evidence | TP/TN/FP/FN | Complete /22 | Multi /5 | Avg chunks (all) | Max | New required | New non-required | Extra on unsupported | Budget drops |
|---|---|---|---|---|---|---|---|---|---|
| 0.2 | 14/11/2/8 | 14 | 4 | 2.1429 | 5 | 4 | 51 | 7 | 0 |
| 0.25 | 14/11/2/8 | 14 | 4 | 1.9429 | 5 | 4 | 44 | 7 | 0 |
| 0.3 | 14/11/2/8 | 14 | 4 | 1.8 | 5 | 4 | 39 | 7 | 0 |
| 0.35 | 14/11/2/8 | 13 | 3 | 1.4571 | 5 | 3 | 28 | 6 | 0 |
| 0.4 | 14/11/2/8 | 11 | 1 | 1.0286 | 4 | 1 | 15 | 3 | 0 |
| 0.45 | 14/11/2/8 | 11 | 1 | 0.6571 | 3 | 1 | 2 | 0 | 0 |
| 0.5 | 14/11/2/8 | 10 | 0 | 0.5714 | 2 | 0 | 0 | 0 | 0 |

Retrieval metrics: {'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.9545454545454546, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.9772727272727273, 'all_evidence_at_5': 1.0}

Selected evidence threshold: 0.3

0.30 is the highest tested threshold preserving maximum complete evidence (14/22, 4/5 multi). Higher thresholds lose evidence; lower thresholds add only non-required inclusions. Gate acceptance is stable, not a guarantee of answer correctness.

Loss diagnosis: {'complete_after_authoritative_validation': 22, 'complete_after_baseline_context': 10, 'answerable_primary_rejections': 8, 'budget_exclusions': 0}

Precision/recall/F1 stay 0.875 / 0.63636364 / 0.73684211 for every candidate. Hit@1/3/5 and MRR are unchanged because retrieval is unchanged.

At 0.30, average context size among primary-accepted cases is 3.9375 (baseline 1.25); maximum is 5 (baseline 2). Full per-case sources, counts, character budgets and non-required inclusions are in the JSON.

Useful recovery: multi-backup adds North page 1 to South page 4; multi-deletion restores history snapshot evidence from data-guide.pdf page 3. Harmless/background example: multi-storage adds PDF page-provenance context (storage-guide.pdf page 2), which is not needed for the storage-location question. Potentially misleading example: unsupported JWT rotation adds South API-key expiry and session timeout passages (operations-guide.pdf pages 3 and 2). Neither answers JWT signing-secret rotation. These labels use corpus meaning and expected pages, not similarity alone.

The two existing false acceptances (unsupported-refresh and unsupported-rotation) receive seven extra non-required inclusions at 0.30. No live Gemini answer-quality claim is made; its grounded abstention remains essential. The remaining multi-cloud case fails the primary gate, so this policy deliberately cannot recover it. Eight answerable primary rejections remain.

Limitations: 35 synthetic cases; replayed scores are rounded to eight decimals; no held-out generalization guarantee, no LLM judge, no Cloud or live Gemini test. A below-0.50 manual multi-topic question must still abstain. Query decomposition is a future candidate to evaluate separately, not implemented.

Non-required is a conservative distractor proxy from expected evidence, not a claim that every extra passage is harmful. Answerability acceptance is not generated-answer correctness.
