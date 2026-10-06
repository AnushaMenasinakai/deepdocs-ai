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


## Phase 15A: deterministic multi-query experiment (evaluation only)

**Recommendation B — DO NOT INTEGRATE the tested strategies.** The measured experiment recovered none of the eight original false negatives, retained 14/22 complete answers and 4/5 multi-chunk answers, and introduced two new unsupported acceptances on the separate safety set under the topic/combined OR gate. This is a rejection of these bounded rules, not proof that every possible decomposition method fails.

Artifacts:

- [Comparison and exhaustive case analysis](reports/phase15a-multi-query.md).
- [Machine-readable comparison](reports/phase15a-multi-query.json).
- [Cached measured rankings](phase15a_rankings.json): 61 unique query strings, top-five chunk identities and rounded cosine scores, model revision, input hashes, and dependency versions; no vectors or source text.
- [Separate safety cases](phase15a_safety.json): version 1.0.0, ten synthetic cases (four answerable, six unsupported). The original 35-case dataset and all six Phase 11/13 report files are unchanged.

### Hypothesis and adoption gates

The hypothesis was that splitting explicit independent targets could raise a weak original top cosine while retaining required pages. Criteria were declared before inference: recover at least two of eight original false negatives; at least 16/22 complete answers; no new unsupported acceptance and no more than two original false positives; precision at least 0.875; at least 4/5 multi-chunk complete; no Hit@1/3/5 or MRR regression; at most four queries and an average no greater than two (checked separately on benchmark and safety sets); at most 35 additional non-required inclusions on the original benchmark; no new safety-set false positives; context at most 12000 characters. None qualified. No thresholds/rules were tuned to rescue the result after measurement.

### Fixed decomposition rules

All strategies retain the trimmed original as Q0. Expansion is deterministic, case-insensitively deduplicated (ignoring terminal question marks/periods), nonempty, and capped at **four total queries**. Truncation retains the first three additional targets in textual order. There are no synonyms, acronym expansions, corpus-term lookups, LLM calls, or automatic rewriting of single-target questions.

1. **Original:** one query, current baseline.
2. **Clauses:** split explicit semicolons, question-mark-separated questions, or comma-and followed by a question head. All parts must begin with a question/instruction head. Later clauses containing unresolved pronouns such as `its`, `their`, or `that` are left intact. This avoids naively splitting every `and` and preserves the JWT/signature example and the PDF/“their embedding vectors” dependency.
3. **Topics:** split an explicit Explain/Describe/Outline/List topic list on commas/final `and`, requiring at least two words per item and no unresolved demonstratives/pronouns. A narrow `Compare <shared frame> in X and Y` rule repeats the frame without changing X/Y. It does not split a general conjunction or `Research and development`. Syntax is not semantic validation: an unsupported topic can still be split.
4. **Combined:** clause expansions followed by topic expansions under the same cap/deduplication.

Clauses split none of the original 35 cases; topics/combined split only `multi-cloud`. Seven of the eight false negatives are not explicit independent-topic questions. This coverage limitation is a central result, not hidden by a global average. On the safety set clauses split three cases, topics four, and combined seven.

### Retrieval, fusion, gates, and packing

Measurement reuses the existing isolated adapter: cached-only CPU MiniLM, normalized 384-dimensional embeddings, official **RAM-only** Qdrant cosine queries, and the real production owner/KB/generation/payload/MongoDB-authority validation against synthetic records. Both socket connection methods are blocked around measurement; Hugging Face offline/local-files-only settings prevent downloads. No `.env`, real Knowledge Base, Cloud client, history, or Gemini is used. A missing cache fails; there is no network fallback.

Each query gets **five validated hits**. The merged candidate is also capped at **five unique chunk IDs**, keeping evidence capacity fixed rather than introducing a second top-k optimization. Per-query execution was shared across candidates for measurement efficiency: 61 unique searches were measured once. Reported cost is the logical search count for each strategy per question, not the deduplicated experiment's compute bill.

- **Max-score fusion:** each chunk keeps its highest observed cosine; sort descending by that cosine.
- **Experimental RRF:** sum `1 / (60 + rank)` over queries returning the chunk, with rank starting at 1. Sort descending by the sum. Ties in either approach use document ID, chunk index, then chunk ID. The sum never serves as a cosine threshold; max observed cosine remains separately available for evidence eligibility.
- **Original gate:** Q0 must achieve cosine >=0.50. It cannot recover an original primary-gate false negative by construction.
- **OR gate:** at least one query's best validated cosine must achieve >=0.50. This tests relevance eligibility only, not support for every requested part.
- After the chosen gate passes, use max observed cosine >=0.30 for evidence, retain fusion order, deduplicate chunks, and pack complete chunks under the unchanged 12000-character serialized budget. This packing is evaluation-only; original-only packing is tested against the actual production builder. RRF ordering deliberately differs from cosine ordering, without converting its score to cosine. The report flags a gate witness excluded by the merge cap.

### Results and costs

All twelve candidate combinations retained original-benchmark TP/TN/FP/FN **14/11/2/8**, precision **0.875**, recall **0.63636**, F1 **0.73684**, complete evidence **14/22**, and multi-chunk completeness **4/5**. No required pages were recovered or lost in final context; no additional non-required context entered original-benchmark accepted cases because the only expanded case remained rejected.

Original/clauses ranking stays Hit@1 **95.45%**, Hit@3/5 **100%**, MRR@5 **0.97727**. Topics/combined with max fusion drop Hit@1 to **90.91%** and MRR@5 to **0.95455**: SaaS outranks required IaaS for `multi-cloud`. RRF preserves baseline ranking metrics but does not open that case's primary gate. Its original/IaaS/PaaS query maxima are **0.27171542 / 0.28087182 / 0.24494221**.

Original/clauses cost 35 searches (1.000x). Topics/combined cost 37 (1.057x, maximum three queries). Accepted benchmark contexts average **3.9375** chunks, maximum five and **1725** serialized characters. No measured case loses a chunk to the 12000-character budget. The merge cap can still exclude required evidence on the separate safety set; JSON distinguishes that from budget exclusion.

On the ten safety cases, original has TP/TN/FP/FN **3/5/1/1**, precision/recall/F1 **0.75/0.75/0.75**, and 2/4 complete answers. Topics/combined OR produce **3/3/3/1**, precision **0.50**, recall **0.75**, F1 **0.60**, still 2/4 complete. Original-gate variants preserve the original decisions. Clauses cost 16 searches (1.6x), topics 19 (1.9x), combined 25 (2.5x); maximum four. Safety contexts remain at most five chunks and **1671** characters. Depending on fusion/gate, additional non-required inclusions range from zero to eleven. These safety cases are reported separately, never blended into 22/35 baseline denominators.

Two new OR false positives explain the risk:

- Unsupported JWT key rotation/revocation: original **0.49870881**; `JWT signing-key rotation` **0.50352790**, which crosses the gate even though the corpus has no such policy.
- JWT signatures **and rice irrigation**: original **0.45187106**; the JWT fragment **0.71712740**, irrigation **0.12476901**. A supported fragment makes the unsupported whole eligible under OR.

The two original false acceptances remain `unsupported-refresh` (0.57503199) and `unsupported-rotation` (0.51616776). All thirteen original unsupported queries remain intact under the conservative rules, so their decisions do not change. Exhaustive scores and decisions are in the comparison report.

### Evidence, distractors, and diagnostic definitions

Every merged hit records which query/rank/cosine contributed it. Required-page discovery records all finding queries and whether the page survives context. Reports include union size, duplicate-hit frequency, per-query unique discoveries, selected contributions, recovered/lost required pages, and merge-cap/budget effects. Deduplication uses trusted chunk identity, never similar text.

“Useful decomposition” means a required context page was added without a new false positive or evidence/rank loss. “Harmful” means a new false positive, required-page loss, or loss of the original expected Hit@1. Others are neutral; intact cases are counted separately. No tested case gained required context pages. This does not deny stronger targeting: independent PDF-storage/token-expiry subqueries correctly strengthen already-included evidence, but that is not a coverage improvement.

Non-required means outside the explicit expected evidence set, not automatically irrelevant. The report gives corpus-grounded examples: local PDF/MongoDB storage material is harmless related background for the three-topic case; dashboard-session timeout and automation API-key expiry are potentially misleading extras for a JWT lifetime question. Signature verification/CSRF/cache passages do not establish a signing-key rotation policy. These are developer interpretations of synthetic text, not an LLM judge.

### Reproduce without live services

```powershell
# Replay recorded measured scores; no model required:
.\backend\.venv\Scripts\python.exe -B backend/evaluation/multi_query.py
# Optional fresh inference, existing cached weights only; sockets blocked:
.\backend\.venv\Scripts\python.exe -B backend/evaluation/multi_query.py --measure
# Ordinary unit tests never download/load the real model:
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_query_decomposition.py backend/tests/test_multi_query_evaluation.py -v -p no:cacheprovider
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -v -p no:cacheprovider
```

Default output names are exclusively `phase15a-multi-query.json/.md`; `--measure` also refreshes only `phase15a_rankings.json`. `--output` must remain inside the repository. Historical reports are fingerprinted and are never targets. The query-rules/dataset fingerprints must match for replay. Reports have no timestamp; ordinary replay is exactly deterministic. Fresh floating-point inference can vary slightly across runtime/hardware versions; rounded scores and recorded model revision support inspection without brittle universal exact-score assertions.

Production runtime and frontend files are unchanged. Primary 0.50, secondary 0.30, per-query production top-k 5, budget 12000, MiniLM/384, citations, semantic search, and history remain intact. No Phase 15B implementation/design is recommended from these results. Future work would require a separately authorized experiment and held-out examples, including partial-support hazards; this benchmark does not establish universal quality. No live answer quality, semantic entailment, latency/currency cost, or multilingual/general NLP coverage was measured. No new model, dependency, BM25, hybrid search, reranker, LLM decomposition/judge, public evaluation endpoint, or Phase 16 was added.

## Phase 16A: second-stage relevance audit (model unavailable)

**Recommendation C — FURTHER EVALUATION REQUIRED.** No suitable local cross-encoder was found in the inspected project and configured/default Hugging Face caches. The only cached model is the existing `sentence-transformers/all-MiniLM-L6-v2` bi-encoder. Installed `sentence-transformers==6.1.0` and `torch==2.14.0` provide libraries, not pretrained reranker weights. Attaching an untrained classifier to the bi-encoder would not constitute a valid experiment. No model was downloaded, loaded, or evaluated for reranking; no GPU/CPU inference device was used.

The hypothesis remains untested: joint question/passage scoring might separate supporting evidence from merely related text. Model-based work stopped at the availability check. The audit covers known caches, not an unrestricted search of personal files or the entire disk. No `.env` is read by this tooling.

Artifacts:

- [Audit report and individual case analysis](reports/phase16a-reranking.md).
- [Machine-readable audit](reports/phase16a-reranking.json).
- `reranking.py`: pure injected-scorer interface and policy/metric mechanics, tested with explicitly synthetic scalar scores only.
- `phase16a.py`: offline, model-free historical replay and cache inventory; writes only Phase16 report filenames.

### What was reproduced

The exact original validated top-five candidate sets are replayed from the committed measured MiniLM reports. No new retrieval or query decomposition occurs. All eight answerable false negatives already contain **all required evidence within five**; they fail the primary cosine gate, not candidate recall or context packing. The existing unsupported false positives remain refresh-token retention and signing-key rotation: the corpus contains access-token expiry/signature verification, not those policies.

Baseline remains Hit@1 95.45%, Hit@3/5 100%, MRR@5 0.97727, TP/TN/FP/FN 14/11/2/8, precision 0.875, recall 0.63636, F1 0.73684, complete evidence 14/22, and multi-chunk complete 4/5. The report contains every original candidate/cosine/required-evidence label for all 35 cases, detailed eight-FN/two-FP tables, all thirteen unsupported decisions, and separate ten-case Phase15 safety replay. No current result is represented as a reranker measurement.

There are **175 benchmark pairs and 50 separate safety pairs available**, at most five per question; **zero reranker pairs were scored**. Reranker model, device, latency, distributions, thresholds, ranking changes, corrected/recovered cases, and experimental completeness are `null`/not measured. They are not zero-valued quality results. No useful/harmful reranking example can be asserted yet.

### Declared experiment mechanics, not production policy

The scorer contract accepts only `(question, passage)` pairs and returns exactly one finite real scalar per candidate. It cannot return or invent identities. Candidate count is bounded at five; malformed/duplicate candidates, booleans, nonnumeric values, NaN, infinity, and wrong-length score output are rejected. Original cosine/provenance stay intact. Scores sort descending with original rank as the stable tie-breaker. Candidate membership is preserved, so Hit@5 must be invariant; promotion/demotion counts distinguish required and non-required evidence.

Policies reserved for a future authorized model measurement:

- **A:** existing production 0.50 primary / 0.30 evidence baseline.
- **B:** a model-specific reranker threshold opens the gate; all qualifying passages, not just the winner, remain eligible in reranked order.
- **C:** B plus the original strongest cosine >=0.50. This deliberately conservative intersection cannot recover original primary false negatives; it tests false-positive suppression and evidence selection separately.

No threshold was guessed without knowing the model's score semantics. The pure sweep accepts at most 41 explicitly supplied thresholds in the scorer's own space. Fake unit-test thresholds are not proposed operating points. Distributions separately cover required answerable evidence, non-required answerable passages, and unsupported-query candidates, with min/max/mean/median/p10/p90. Scores are relevance signals, **not calibrated probabilities or factual confidence**; no cosine-plus-reranker arithmetic is used.

Complete-chunk context packing retains trusted fields and the 12000-character serialized budget. It reports gate acceptance separately from usable-context/provider eligibility, since a passing gate can still leave no fitting evidence. Tests cover multipage retention, skipped oversized chunks, exact budget boundaries, and added non-required/removed required evidence. Such tests establish implementation behavior, not model quality or semantic entailment.

Before any measured result, adoption criteria remain: recover >=4/8 FNs, correct >=1/2 FPs, <=1 new original-benchmark FP, target >=18/22 complete evidence, >=4/5 multi completeness, precision >=0.875, recall above 14/22, bounded context, deterministic offline behavior, and measured/reviewed computational cost. There is no candidate to assess against these criteria yet. No Phase16B implementation plan is proposed under recommendation C.

### Reproduction and boundaries

```powershell
.\backend\.venv\Scripts\python.exe -B backend/evaluation/phase16a.py
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_reranking_evaluation.py -q -p no:cacheprovider
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_evaluation.py backend/tests/test_evidence_policy.py backend/tests/test_query_decomposition.py backend/tests/test_multi_query_evaluation.py -q -p no:cacheprovider
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -q -p no:cacheprovider
```

The audit runner blocks socket connections; it has no model-loader or live-provider path. Report quality data replay deterministically; cache inventory intentionally reflects the inspected environment. Tests pin replay to the recorded inventory and verify historical report/production fingerprints. Ordinary tests never need a model download or network. No historical Phase11/13/15 reports are written.

If a suitable model becomes available later, its suitability, native score behavior, revision, truncation behavior, and local inference cost still need measurement under an explicitly authorized experiment. A classification-head architecture alone is not proof that a model is trained for relevance. The existing cache check flags potential candidates for review but does not auto-run them.

Phase14 citations remain downstream of final evidence selection: only the unchanged server mapping may assign IDs and source metadata. No scorer may invent filenames/pages/citations; history remains a snapshot and never becomes context. Production primary 0.50, secondary 0.30, top-k 5, budget 12000, MiniLM/384, single-query retrieval, frontend, and citations are unchanged. No cloud services, Gemini judge, BM25/hybrid retrieval, production reranker, new dependency, or Phase17 work was added.
