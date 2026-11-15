# Optional Gemini embeddings

Local MiniLM remains the default. This adapter has mocked/offline validation only;
it is not live-provider, retrieval-quality, or Render memory approval.

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
must provision/select a dedicated collection and explicitly re-index documents.
Do not reuse an unrelated existing 768-dimensional collection either.

Indexing uses `RETRIEVAL_DOCUMENT`; queries use `RETRIEVAL_QUERY`. Each SDK request
contains at most 16 inputs, further limited by `EMBEDDING_BATCH_SIZE`. Each input
is capped at 1,024 UTF-8 bytes: a deliberately conservative byte budget below the
model's 2,048-token limit, leaving room for tokenization overhead without loading
a local tokenizer or issuing counting calls. This is not an exact token counter.
Long inputs (including Unicode-heavy chunks) fail with 409, never silently truncate.
Chunking is unchanged; some documents therefore need a separately reviewed input
packing policy before broad deployment. A later invalid batch may follow earlier
acknowledged vector writes; existing indexing failure/publication rules apply.

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

Remaining work before opt-in deployment: approved live SDK/model validation, strict
daily request/token budgets and invite-only access, evaluation of retrieval gates
with the new vector space, explicit re-indexing/rollback plan, and constrained
memory/latency tests. Existing Docker packaging still installs and packages local
MiniLM; this phase does not change production dependencies or deployment images.
