# Phase 16A — second-stage relevance evaluation

C. FURTHER EVALUATION REQUIRED

No suitable local cross-encoder found in inspected cache locations; model-based experiment stopped without download.

No reranker inference occurred. All second-stage results are **not measured**, not zero. Only historical measured MiniLM scores are replayed. No download or external services.

## Baseline

Ranking: `{'answerable_count': 22, 'unsupported_count': 13, 'no_valid_hit_count': 0, 'hit_at_1': 0.9545454545454546, 'hit_at_3': 1.0, 'hit_at_5': 1.0, 'mrr': 0.9772727272727273, 'all_evidence_at_5': 1.0}`

Decision: `{'tp': 14, 'tn': 11, 'fp': 2, 'fn': 8, 'precision': 0.875, 'recall': 0.6363636363636364, 'f1': 0.7368421052631579}`

Complete evidence: 14/22; multi-chunk: 4/5. Context: `{'average_chunks_accepted': 3.9375, 'maximum_chunks': 5, 'average_serialized_characters_accepted': 1280.25, 'maximum_serialized_characters': 1725, 'budget_affected': []}`.

## Model availability

Installed sentence-transformers/torch: {'sentence-transformers': '6.1.0', 'torch': '2.14.0'}

- project_embedding_cache: exists=True; models=[{'name': 'sentence-transformers/all-MiniLM-L6-v2', 'architectures': ['BertModel'], 'weights_present': True, 'tokenizer_present': True, 'potential_sequence_scorer': False}]
- project_model_cache: exists=False; models=[]
- huggingface_hub: exists=False; models=[]

Inventory is limited to known caches, not a claim about every file on the machine. The cached all-MiniLM-L6-v2 bi-encoder is not a trained cross-encoder; attaching an untrained classifier would not be a valid experiment.

## Predeclared criteria and policies

{
  "minimum_recovered_false_negatives": 4,
  "minimum_corrected_false_positives": 1,
  "maximum_new_false_positives": 1,
  "minimum_complete_evidence": 18,
  "minimum_multi_chunk_complete": 4,
  "minimum_precision": 0.875,
  "recall_must_exceed": 0.6363636363636364,
  "maximum_context_characters": 12000,
  "deterministic_offline": true,
  "compute_cost": "Must be measured and reviewed; not assessable without a local model"
}

- A: Unchanged cosine 0.50 primary / 0.30 evidence; production context builder
- B: Any reranker score >= model-specific threshold; keep all qualifying candidates in reranked order
- C: B AND original strongest cosine >=0.50; conservative control, cannot recover original gate false negatives

No model-specific threshold was guessed. A future authorized measurement must establish native score semantics and a bounded sweep before selection. Cosine and cross-encoder scores must never be added or treated as comparable. No criteria can be assessed for adoption yet.

## Eight answerable false negatives and two existing false positives

### direct-password

Which algorithm stores password hashes?

Expected: [{'document': 'authentication-guide.pdf', 'page': 3}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | authentication-guide.pdf p3–3 | 0.44279455 | True | Not measured |
| 2 | data-guide.pdf p1–1 | 0.28860458 | False | Not measured |
| 3 | data-guide.pdf p2–2 | 0.25276707 | False | Not measured |
| 4 | operations-guide.pdf p1–1 | 0.17411221 | False | Not measured |
| 5 | storage-guide.pdf p1–1 | 0.16049525 | False | Not measured |
### para-signature

What stops someone editing a login token and passing it off as genuine?

Expected: [{'document': 'authentication-guide.pdf', 'page': 1}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | authentication-guide.pdf p1–1 | 0.41665697 | True | Not measured |
| 2 | web-guide.pdf p1–1 | 0.40223454 | False | Not measured |
| 3 | authentication-guide.pdf p2–2 | 0.39186361 | False | Not measured |
| 4 | operations-guide.pdf p3–3 | 0.34184004 | False | Not measured |
| 5 | authentication-guide.pdf p3–3 | 0.30985787 | False | Not measured |
### para-owner

Can a different account discover my Knowledge Base by guessing its identifier?

Expected: [{'document': 'authentication-guide.pdf', 'page': 4}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | authentication-guide.pdf p4–4 | 0.34289047 | True | Not measured |
| 2 | authentication-guide.pdf p3–3 | 0.27867466 | False | Not measured |
| 3 | data-guide.pdf p1–1 | 0.19856192 | False | Not measured |
| 4 | operations-guide.pdf p3–3 | 0.17556605 | False | Not measured |
| 5 | data-guide.pdf p2–2 | 0.17418029 | False | Not measured |
### para-ocr

Can the system read words from a scan that contains pictures but no selectable text?

Expected: [{'document': 'storage-guide.pdf', 'page': 2}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | storage-guide.pdf p2–2 | 0.48013180 | True | Not measured |
| 2 | data-guide.pdf p2–2 | 0.23136444 | False | Not measured |
| 3 | storage-guide.pdf p3–3 | 0.19077710 | False | Not measured |
| 4 | data-guide.pdf p1–1 | 0.15502933 | False | Not measured |
| 5 | web-guide.pdf p3–3 | 0.13416093 | False | Not measured |
### para-history

Will an earlier response be remembered automatically when I ask something new?

Expected: [{'document': 'data-guide.pdf', 'page': 3}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | web-guide.pdf p2–2 | 0.36147529 | False | Not measured |
| 2 | data-guide.pdf p3–3 | 0.34304364 | True | Not measured |
| 3 | data-guide.pdf p1–1 | 0.14792566 | False | Not measured |
| 4 | authentication-guide.pdf p2–2 | 0.14194255 | False | Not measured |
| 5 | operations-guide.pdf p2–2 | 0.11561224 | False | Not measured |
### multi-cloud

Compare what the customer manages in IaaS and PaaS.

Expected: [{'document': 'cloud-guide.pdf', 'page': 1}, {'document': 'cloud-guide.pdf', 'page': 2}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | cloud-guide.pdf p1–1 | 0.27171542 | True | Not measured |
| 2 | cloud-guide.pdf p3–3 | 0.26971811 | False | Not measured |
| 3 | authentication-guide.pdf p3–3 | 0.15951189 | False | Not measured |
| 4 | operations-guide.pdf p3–3 | 0.11960169 | False | Not measured |
| 5 | cloud-guide.pdf p2–2 | 0.11700260 | True | Not measured |
### unsupported-refresh

How long are refresh tokens stored?

Expected: []. Original decision: True; complete evidence: False.

Accepted topical similarity does not establish support for the requested fact/policy.

Corpus describes access-token expiration, not refresh-token retention; access and refresh tokens are distinct.

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | web-guide.pdf p2–2 | 0.57503199 | False | Not measured |
| 2 | authentication-guide.pdf p2–2 | 0.56038844 | False | Not measured |
| 3 | operations-guide.pdf p2–2 | 0.42335443 | False | Not measured |
| 4 | operations-guide.pdf p3–3 | 0.40537517 | False | Not measured |
| 5 | operations-guide.pdf p1–1 | 0.36208191 | False | Not measured |
### unsupported-rotation

How often does this server rotate its JWT signing secret?

Expected: []. Original decision: True; complete evidence: False.

Accepted topical similarity does not establish support for the requested fact/policy.

Corpus describes signature verification with a configured secret, not signing-key rotation; other session/API-key timings are distractors.

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | authentication-guide.pdf p1–1 | 0.51616776 | False | Not measured |
| 2 | operations-guide.pdf p3–3 | 0.43592272 | False | Not measured |
| 3 | operations-guide.pdf p2–2 | 0.39635071 | False | Not measured |
| 4 | authentication-guide.pdf p2–2 | 0.37295361 | False | Not measured |
| 5 | web-guide.pdf p2–2 | 0.33616833 | False | Not measured |
### ambiguous-data

Where is application metadata stored?

Expected: [{'document': 'data-guide.pdf', 'page': 1}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | data-guide.pdf p1–1 | 0.47693742 | True | Not measured |
| 2 | storage-guide.pdf p1–1 | 0.35370438 | False | Not measured |
| 3 | cloud-guide.pdf p2–2 | 0.32550393 | False | Not measured |
| 4 | operations-guide.pdf p4–4 | 0.30025573 | False | Not measured |
| 5 | operations-guide.pdf p1–1 | 0.28046075 | False | Not measured |
### ambiguous-remember

Does the next question use my past answers?

Expected: [{'document': 'data-guide.pdf', 'page': 3}]. Original decision: False; complete evidence: False.

Required evidence is in top five, but strongest cosine is below 0.50; not a candidate-recall or context-budget failure.

All required pages present within top five: True

| Original rank | Source | Cosine | Required | Reranker score/rank |
|---|---|---|---|---|
| 1 | data-guide.pdf p3–3 | 0.34939177 | True | Not measured |
| 2 | data-guide.pdf p1–1 | 0.14381718 | False | Not measured |
| 3 | web-guide.pdf p2–2 | 0.13214717 | False | Not measured |
| 4 | operations-guide.pdf p4–4 | 0.12910506 | False | Not measured |
| 5 | operations-guide.pdf p1–1 | 0.12112999 | False | Not measured |

## All thirteen unsupported cases

| Case | Top cosine | Original accepted | Second-stage decision / corrected or new FP |
|---|---|---|---|
| unsupported-refresh | 0.57503199 | True | Not measured |
| unsupported-rotation | 0.51616776 | True | Not measured |
| unsupported-region | 0.44434452 | False | Not measured |
| unsupported-price | 0.18819380 | False | Not measured |
| unsupported-recovery | 0.44002135 | False | Not measured |
| unrelated-rice | 0.15710584 | False | Not measured |
| unrelated-bread | 0.10990535 | False | Not measured |
| unrelated-moon | 0.08359429 | False | Not measured |
| unrelated-piano | 0.05855029 | False | Not measured |
| unrelated-bird | 0.15400278 | False | Not measured |
| ambiguous-expiration | 0.45293337 | False | Not measured |
| ambiguous-best | 0.32897553 | False | Not measured |
| ambiguous-safety | 0.20144689 | False | Not measured |

## Separate Phase15 safety cases

Baseline decision: `{'tp': 3, 'tn': 5, 'fp': 1, 'fn': 1, 'precision': 0.75, 'recall': 0.75, 'f1': 0.75}`. Complete evidence 2/4. No subqueries used. No reranker safety conclusion is possible.

| Case | Question | Original top score | Original accepted | Second-stage outcome |
|---|---|---|---|---|
| safety-related-signature | How does JWT authentication work and why is its signature checked? | 0.78077547 | True | Not measured |
| safety-three-topics | Explain JWT authentication, MongoDB storage, and cloud service models. | 0.54308439 | True | Not measured |
| safety-unsupported-rotation | Explain JWT signing-key rotation and emergency key revocation. | 0.49870881 | False | Not measured |
| safety-unrelated-conjunction | How do irrigation and fertilizer improve rice farming? | 0.15741285 | False | Not measured |
| safety-single-concept | Research and development | 0.23648274 | False | Not measured |
| safety-explicit-questions | Where are original PDFs stored? How long do access tokens remain valid? | 0.52362345 | True | Not measured |
| safety-semicolon-unsupported | How long are refresh tokens stored; what is the JWT signing-key rotation policy? | 0.52263948 | True | Not measured |
| safety-mixed-supported-unsupported | Explain JWT signatures and rice irrigation. | 0.45187106 | False | Not measured |
| safety-comparison-unsupported | Compare what the customer manages in IaaS and the lunar habitat. | 0.26397895 | False | Not measured |
| safety-semicolon-supported | Where is application metadata stored; which algorithm stores password hashes? | 0.45132195 | False | Not measured |

## Scope, cost, and limitations

Fixed candidate sets: 175 original-benchmark pairs, 50 safety pairs; at most five per question. **Zero reranker pairs scored**; device, latency, score distributions, threshold sweep, promoted/demoted evidence, and candidate completeness are not measured.

Test-only infrastructure checks scalar validation, identity preservation, stable sorting, Hit@5 invariance, multipage retention, decision accounting, and complete-chunk budget packing. Fake scores are never reported as measured relevance. No useful/harmful reranking example can be asserted without a model.

The eight false negatives are primary-gate losses after successful candidate retrieval. The two existing false positives show topical similarity without ground-truth support. Reranking may or may not fix this; availability currently prevents testing that hypothesis.

Future citation interaction: only final selected evidence may enter the unchanged Phase14 server-assigned citation mapping. A scorer returns scalars only; it must never invent identities, filenames, pages, or citations. History remains a snapshot, never retrieval context.

Production .50/.30, top-five, 12000 budget, MiniLM/384, single-query retrieval, citations, and frontend remain unchanged. Historical reports are fingerprinted and never rewritten. No Phase16B plan is proposed under recommendation C. Phase17 is not started.

Run `python -B backend/evaluation/phase16a.py` for this model-free audit replay. The command blocks socket connections and writes only the two Phase16 reports. Tests never load a model. A reranker must be made available separately under explicit authorization before actual measurement; this command will not download or execute one.

No measured second-stage scores, ranking changes, threshold calibration, recovered cases, or inference cost.
Unit-test fake scalar scores verify mechanics only and are never benchmark results.
Cross-encoder relevance is not calibrated probability, factual confidence, or proof that every question part is supported.
Non-required candidates are not automatically harmful; ground truth does not label every possible supporting passage.
No Phase16B design without measured adoption evidence; no public endpoint or production integration.
