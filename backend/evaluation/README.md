# Phase 11: retrieval and RAG-decision evaluation

This developer-only harness measures ranking, relevance-gate decisions, and context survival. It does not score Gemini prose and is never imported by normal application startup. Production model, chunking, prompts, collection layout, top_k=5, threshold=0.50, and context budget=12000 are unchanged.

## Dataset 1.0.0

`corpus.json` contains 20 synthetic page-bounded text chunks across six named documents. These are plain-text fixtures, not uploaded PDFs. Authentication, application/vector storage, cloud service responsibilities, PDF lifecycle, and deliberately similar token/backup terminology create meaningful distractors. North/South service policies are invented benchmark facts, not real DeepDocs features. Source names and one-based pages are explicit. `cases.json` has 35 questions: five per category, 22 answerable and 13 unsupported.

- **direct:** a fact explicitly stated on one page.
- **paraphrase:** different wording for a stated fact.
- **multi_chunk:** two expected evidence pages; all are required for complete coverage.
- **related_unsupported:** same domain, but requested refresh-token policy, signing-key rotation, hosting region, pricing, or recovery-time fact is absent.
- **unrelated:** agriculture, baking, astronomy, music, or bird migration.
- **ambiguous:** three underspecified/subjective unsupported questions and two broadly worded but answerable questions.
- **disambiguation:** distinguish idle-session timers, API-key expiration, backup schedules, CSRF tokens, and endpoint limits.

Ground truth is manually authored answerability and document/page evidence, not desired model prose. Ambiguous-question labels assume no previous conversational context. Version/fingerprint the corpus, cases, and score fixtures together when revising labels. This is one small development benchmark, not an independent test split or representative sample of production PDFs.

## Two deliberately separate modes

**Default `fixture`:** `fixture_rankings.json` contains explicitly hand-authored scores and distractor ordering. They are not real model measurements. A fake embedding provider and score store exercise the actual `retrieval.search_chunks` MongoDB payload/generation verification path against in-memory synthetic records. This mode provides reproducible engineering regression gates, not evidence for tuning semantic similarity.

**Opt-in `local-model`:** loads the existing all-MiniLM-L6-v2 cache using `local_files_only=True`, CPU, and `trust_remote_code=False`. Both Hugging Face offline flags are set. It reuses production embedding batching/validation, official in-memory Qdrant cosine querying through `VectorStore`, and the production authoritative retrieval checks. The dimension is obtained from the model (384 in the recorded run). Synthetic MongoDB records exist only in RAM. The Qdrant client closes in `finally`; no persistent collection or vector database is created. No `.env` or Cloud URL is loaded, and missing weights fail rather than downloading or falling back.

Neither mode contacts Atlas, Qdrant Cloud, Gemini, or real Knowledge Bases. Both mock the Gemini provider to measure **whether it would be called**, not whether it would answer correctly. Ordinary tests use only fixture mode; a test of local-model cache failure uses a fake constructor.

## Run from the project root

```powershell
.\backend\.venv\Scripts\python.exe -B backend/evaluation/runner.py
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_evaluation.py -v -p no:cacheprovider
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -v -p no:cacheprovider
```

Optional measured local inference, only if the existing model is cached:

```powershell
.\backend\.venv\Scripts\python.exe -B backend/evaluation/runner.py --mode local-model
```

Default outputs are ignored `.verification/evaluation/fixture.json` and `.md`, or `local-model.json` and `.md`. `--output backend/evaluation/reports` explicitly refreshes the checked-in reference reports. Output directories must remain inside the project. JSON includes dataset version/SHA-256, mode, model/cache revision and package versions, metrics, threshold sweep, category metrics, per-case source/score diagnostics, and failure IDs. It contains no vectors, full chunk text, private data, or secrets. Reports have no wall-clock timestamp; fixture runs are exactly repeatable. Real-model floating-point results may vary across hardware/dependency versions and are not asserted exactly.

## Metric definitions

Only **answerable** cases enter Hit@k and MRR denominators. A hit is a validated returned chunk whose source filename matches an expected document and whose inclusive page range includes an expected page. The synthetic corpus uses unique document filenames. Hit@1/3/5 count at least one expected source within k results. MRR is the mean reciprocal rank of the first expected source in the returned top five; missing evidence contributes zero (**MRR@5**, not an unbounded ranking metric). Duplicate chunk IDs and malformed metric inputs are skipped without reordering surviving ranks. Unsupported-only categories report ranking metrics as N/A, not perfect scores.

For multi-page questions, any-hit metrics can overstate completeness: `all_evidence_at_5` additionally requires every expected page. Context evidence fractions and all-evidence counts measure how much survives the actual relevance guard and context budget. `no_valid_hit_count` counts empty validated result sets, not sets lacking ground-truth evidence.

Threshold acceptance means a finite top validated cosine score is **greater than or equal to** the threshold. Missing scores reject. TP = answerable/accepted; TN = unsupported/rejected; FP = unsupported/accepted; FN = answerable/rejected. Precision = TP/(TP+FP); recall = TP/(TP+FN); F1 is their harmonic mean. Zero-denominator decision metrics return zero. This evaluates relevance/answerability eligibility, not Gemini confidence or entailment. Scores are never percentages of answer accuracy.

The 0.20–0.80 sweep advances by 0.05, holding all other settings constant. The context builder still filters each individual chunk, not only the top score. It may drop additional evidence even when the question passes the top-score guard.

## Measured baseline: cached model, isolated Qdrant

See [local-model.md](reports/local-model.md) and [local-model.json](reports/local-model.json). Model/dimension: all-MiniLM-L6-v2 / 384.

- Hit@1: **21/22 = 95.45%**; Hit@3 and Hit@5: **22/22 = 100%**.
- MRR@5: **0.97727**; all expected pages occur within top five for all 22 answerable cases.
- At **0.50**: **TP 14, TN 11, FP 2, FN 8**. Precision **0.875**, recall **0.63636**, F1 **0.73684**.
- No query had an empty validated retrieval set. Empty-result behavior is tested separately.
- Answerable top scores: min **0.27172**, max **0.82214**, mean **0.56045**, median **0.58320**.
- Unsupported top scores: min **0.05855**, max **0.57503**, mean **0.28541**, median **0.20145**.

| Category | Answerable / total | Hit@1 | Hit@3/5 | MRR@5 | Baseline FP / FN |
|---|---:|---:|---:|---:|---:|
| Direct | 5 / 5 | 1.00 | 1.00 | 1.00 | 0 / 1 |
| Paraphrase | 5 / 5 | 0.80 | 1.00 | 0.90 | 0 / 4 |
| Multi-chunk | 5 / 5 | 1.00 | 1.00 | 1.00 | 0 / 1 |
| Related unsupported | 0 / 5 | N/A | N/A | N/A | 2 / 0 |
| Unrelated | 0 / 5 | N/A | N/A | N/A | 0 / 0 |
| Ambiguous | 2 / 5 | 1.00 | 1.00 | 1.00 | 0 / 2 |
| Disambiguation | 5 / 5 | 1.00 | 1.00 | 1.00 | 0 / 0 |

Paraphrase is the weakest ranking category and rejects four of five answerable questions at 0.50. Both answerable ambiguous cases are rejected. Related-but-unsupported refresh-token storage and JWT secret-rotation questions pass the guard despite their requested facts being absent. **Passing the relevance guard is not proof the document answers the question.** Existing Gemini abstention may help, but was deliberately not measured with real Gemini here.

Only **10/22** answerable cases retain *all* expected evidence in final context. All five multi-chunk cases lose at least one necessary page at 0.50, despite perfect top-five evidence retrieval; one loses all qualifying context. This is a per-chunk threshold weakness, not evidence that the context budget needs increasing. Eight answerable questions have no qualifying context at all. Full failure IDs and per-case evidence fractions are in JSON.

## Tuning decision: keep 0.50

| Candidate | TP / TN / FP / FN | Precision | Recall | F1 |
|---|---|---:|---:|---:|
| 0.25 (highest synthetic F1) | 22 / 7 / 6 / 0 | 0.78571 | 1.00000 | 0.88000 |
| 0.40 | 18 / 8 / 5 / 4 | 0.78261 | 0.81818 | 0.80000 |
| **0.50 baseline** | **14 / 11 / 2 / 8** | **0.87500** | **0.63636** | **0.73684** |
| 0.55 | 12 / 12 / 1 / 10 | 0.92308 | 0.54545 | 0.68571 |
| 0.60 | 9 / 13 / 0 / 13 | 1.00000 | 0.40909 | 0.58065 |

Lowering to 0.25 would triple unsupported acceptances from two to six. Raising to 0.60 removes those false positives on this tiny corpus but rejects 13/22 answerable cases. Neither is convincing evidence of a globally better operating point. The fixture-mode F1 of 0.875 is an authored regression property and is not used for this decision. Keep production 0.50, top_k=5, budget=12000, and the existing model/prompt unchanged.

Next evaluation should use an independent held-out, consented/public-safe domain corpus, more exact-answer unsupported examples, and human review of answers/evidence before considering configuration changes. No advanced retrieval technique is justified simply by this benchmark. No reranker, BM25/hybrid path, new model, dynamic top_k, query rewriting, or LLM judge was added.

## Context and decision gates

Eleven deterministic probes exercise exact budget inclusion, one-character-under exclusion, whole oversized chunk skipping, stable relevance order, unchanged text, low-score exclusion, invalid provenance rejection, duplicate chunk exclusion, duplicate-page source deduplication, included-page-only sources, and empty context. These test the existing production builder/source mapper directly.

The runner invokes the existing RAG service with fixed retrieval results and a mocked provider. Empty/low context never constructs the provider; qualifying context permits one call. Provider abstention remains insufficient context with no sources. Confusion counts are reported against ground truth rather than forcing unsupported high-score cases to pass a fabricated correctness gate. No history route is invoked, so no Ask history is created or changed.

Unit gates use the versioned fixture: Hit@1 >= 0.60, Hit@3 = 1, MRR >= 0.80, unrelated FP = 0, known FP/FN accounted for, all context probes true, output deterministic, and no sockets/model downloads. These catch plumbing regressions, not real-model semantic degradation. Optional local-model reports support manual comparisons using tolerances; they are not brittle exact-float tests.

## Scope and limitations

The benchmark represents pages/chunks directly; it does not evaluate PDF extraction, chunking realism, production corpus scale, Cloud latency, answer factuality, prompt-injection immunity, or universal real-world accuracy. Some labels are judgment calls. There is no separate held-out split yet. The existing tokenizer can truncate longer production chunks; these short fixtures do not characterize that limitation. Model-cache and runtime revisions accompany the reports.

Normal API schemas, ownership checks, Ask/history UI, production .env, and user storage are untouched. No public evaluation endpoint exists. No Gemini calls, LLM-as-a-judge, query/history vector persistence, conversational memory, or Phase 12 work is included. Existing model-dimension accessor emits a deprecation warning during optional inference; it still works and was left unchanged to preserve production scope.
