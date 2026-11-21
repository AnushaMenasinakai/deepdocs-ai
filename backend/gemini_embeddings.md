# Optional Gemini embeddings

Local MiniLM remains the default. The adapter has a prior two-request live smoke
check and mocked application integration coverage. This is not live end-to-end
retrieval-quality or Render memory approval.

Opt-in settings (do not enable against the existing MiniLM collection):

- `EMBEDDING_PROVIDER=gemini`
- `GEMINI_EMBEDDING_MODEL=gemini-embedding-001`
- `EMBEDDING_DIMENSION=768`
- Existing server-only `GEMINI_API_KEY`
- Explicit dedicated `QDRANT_COLLECTION_NAME`, different from `deepdocs_chunks`.

Only the above remote model/dimension combination is currently supported. Local
`EMBEDDING_MODEL_NAME` and model-discovered dimensions retain their old behavior.
Remote metadata uses `gemini-embedding-001` as its embedding model identity, so
existing model eligibility checks exclude MiniLM generations. Qdrant's existing
dimension/cosine validation remains mandatory. Configuration never migrates,
deletes, renames, or automatically selects a replacement collection. Operators
must provision/select a dedicated collection. Gemini indexing rejects documents
whose recorded index uses a different collection, model or dimension before the
existing cleanup can erase previous vectors. Use fresh synthetic documents for
validation; migration of existing documents requires a separate approved plan.
Do not reuse an unrelated existing 768-dimensional collection either.

Indexing uses `RETRIEVAL_DOCUMENT`; queries use `RETRIEVAL_QUERY`. Each SDK request
contains at most 16 text segments. Original chunks per request are also limited
by `EMBEDDING_BATCH_SIZE`. Each provider input
is capped at 1,024 UTF-8 bytes: a deliberately conservative byte budget below the
model's 2,048-token limit, leaving room for tokenization overhead without loading
a local tokenizer or issuing counting calls. This is not an exact token counter.
Document chunks are partitioned at Unicode code-point boundaries into exact text
segments. Concatenating segments reproduces the original text byte for byte.
Stored chunk text, identifiers and page/citation metadata remain unchanged.
For multiple segments, their normalized vectors are averaged using UTF-8 byte
length weights and normalized again, producing one 768-dimensional chunk vector.
This pooling is an approximation, not a native full-chunk embedding; live ranking
and relevance-threshold evaluation remain necessary.

Each document chunk must fit both 16 KiB and 16 segments. Empty/whitespace-only
segments, oversized chunks and degenerate pooled vectors fail explicitly; no
content is silently dropped. All chunk sizes are checked before the first
embedding request. Queries retain the 1,024-byte cap and fail with 409 if larger.
Provider failures after earlier acknowledged vector writes retain the existing
indexing failure/publication behavior.

Vectors must match the requested count and dimension, contain only finite numeric
values, and have a finite nonzero norm. L2 normalization uses standard-library
math, without importing NumPy, PyTorch or Sentence Transformers in the remote path.

Requests reuse the existing bounded, application-owned embedding worker. The sync
SDK is rejected on an event-loop thread. Each batch owns and closes its client;
no network client or executor survives outside worker ownership. Transport timeout
is 30 seconds, SDK attempts is one, and responses arriving after the elapsed
30-second deadline are discarded. Production indexing and retrieval also wrap each
owned-worker wait in a 30-second async deadline, returning sanitized failure on
expiry while native ownership drains. Cancellation does not kill a native worker:
its capacity remains owned until the SDK call and client cleanup finish. Transport
timeouts are not a guarantee of thread termination at exactly 30 seconds. There
are no automatic retries, model fallbacks or secret/provider-response logging.

The offline integration tests exercise actual upload, PDF extraction, indexing,
query, answer/citation and history interfaces with mocked external services.
Mock scores verify orchestration, not semantic retrieval quality.

Manual live-provider utilities are deferred from the deployment candidate. They
are not required to run the application; no command to execute them is included
here. Any later live validation requires separate approval and bounded requests.

Private approved-account access and durable daily request budgets are implemented.
Their development defaults remain disabled; a future private deployment must
explicitly configure PRIVATE_ACCESS_ENABLED=true and GEMINI_USAGE_BUDGETS_ENABLED=true.
Request budgets do not guarantee provider token quotas, availability or zero cost.
Operational activation and deployment are not approved by this documentation.

Gemini-mode resource experiment #5 was previously reported as passing synthetic
screening. That result was not independently reverified during this preparation;
it does not establish real-provider latency, production Atlas memory overhead or
approval of the production Docker image. The production Dockerfile still installs
and packages MiniLM, while the experiment uses a separate image definition.
Remaining work includes exact deployment-snapshot validation, production-image
selection, retrieval-quality acceptance, a reviewed migration/rollback plan and
storage operational approval. Preserve the existing local MiniLM option.

Standard answerability safeguard: generation instructions require evidence for
all requested entities/attributes and question parts, with refusal for missing,
explicitly unavailable or unresolved contradictory evidence. Citation IDs must
still refer to server-selected context. Empty context bypasses generation even
for direct provider callers. Provider errors remain sanitized errors, not evidence
refusals. No extra provider request or similarity-threshold change is introduced.
This is a prompt-based safeguard, not independent entailment verification: a model
can still assert support incorrectly. Mocked tests verify handling and prompt
construction, not live factual reliability. The experimental verifier remains
separate and disabled by default.
