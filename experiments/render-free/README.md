# Render-sized runtime experiment (not deployment configuration)

Run **Render Free resource experiment** manually on GitHub after review/commit.
Do not run its runner script locally. No workflow has been executed by this change.
Only public repositories are admitted, avoiding private runner-minute billing.
No service is deployed, credentials supplied, or external application APIs accessed.

The unchanged backend Docker build obtains its existing pinned MiniLM revision.
An isolated child image adds onnx 1.20.1 and onnxruntime 1.24.2 (binary wheels only)
and exports that exact cached model offline to unquantized float32 ONNX. Both
runtimes run in the same child image: additional installed packages are not loaded
in the PyTorch reference. ONNX uses the exported tokenizer, original sequence
limit, attention-mask mean pooling, and L2 normalization. No production files change.

Four fresh containers run sequentially: PyTorch and ONNX calibration at 3 GiB/2 CPU,
then each at **512 MiB/0.1 CPU with no swap**. The host verifies actual cgroup limits.
Each has no network, no credentials, nonroot execution, read-only root, bounded tmpfs,
dropped capabilities, no-new-privileges and a 128-PID cap. Only backend tests are
mounted, read-only. All PDFs, metadata and fake vectors are synthetic and disposable.
The inherited Docker HTTP health check is disabled to avoid an extra polling process.
One real Uvicorn server runs on container loopback with one worker and no proxy
headers. Its actual FastAPI lifespan owns admission, CPU/PDF supervision and auth
protection; only the async MongoDB client boundary is mocked during startup.
Build downloads are public packages/model artifacts, not runtime provider traffic.

Stages: application import, Uvicorn/lifespan startup and idle, cold model initialization,
one 14-text embedding batch, 12-page upload, supervised extraction/chunking, indexing,
three sequential standard Ask requests, post-request idle, GridFS adapter upload,
materialization/reprocessing through the same trusted PDF lane, lease drain, final
idle and graceful shutdown. No forced garbage collection hides retained memory.
ASGI requests invoke the actual app in the same process; they do not measure the
HTTP socket transport. Authentication, MongoDB metadata/index acknowledgements,
Qdrant storage/scores and Gemini responses are deterministic doubles. Real query
embeddings, retrieval validation, context selection, citation checks and history
orchestration execute; fake Qdrant scores deliberately exercise generation, not
retrieval quality. The three answers must have citations and persisted fake history.
Production chunk/batch defaults are restored after fixture creation.

The GridFS stage uses the real storage adapter, bounded worker and parser lease,
with an in-memory bucket. It measures that path's overhead **after** the local journey,
not an isolated incremental cost. It excludes real MongoDB sockets/pools/TLS, quota
transactions and the optional upload-intent route. Production GridFS stays disabled.
The fake bucket and test framework also add memory not present in deployment.
This is a small sequential journey, **not worst-case 10 MiB PDF, authentication
hashing, maximum pages or concurrent-request testing**.

Linux cgroup memory includes children, native allocations, cache and tmpfs.
`memory.peak` is the exact **lifetime** peak observed before cgroup removal; per-stage
50 ms sampled peaks are lower bounds, not exact peaks. Stage endpoints also record
kernel lifetime peaks. Startup idle includes the lifespan and server but not connected provider clients. CPU throttling, OOM state, exit code, deadlines, stage timings and raw synthetic
vectors are retained in a three-day artifact. Calibration never establishes fit at
512 MiB. A limited probe gets eight minutes; calibration gets five. Timeout is a
failure to complete within this experiment, not necessarily an OOM.

Compatibility compares all 384 dimensions, finite values, unit norms, paired cosine,
top-five rankings and decisions at the unchanged 0.50/0.30 thresholds on a fixed
synthetic corpus (including Unicode and truncation). Scores and distance to thresholds
are reported, not tuned. This does not establish compatibility for existing stored
vectors, full context/citation validation or all near-threshold real documents.

Interpretation: any OOM, incomplete journey or timeout makes that runtime unsuitable
for this workload as tested. Completion alone is insufficient for safe deployment:
require usable latency, memory headroom (suggested at least 20%), no material vector
or gate differences, then separate worst-case/concurrency/provider testing. Render's
CPU scheduling, cold starts and platform overhead can differ. PDF persistence,
free-tier/no-card eligibility and provider quotas remain separate unresolved concerns.
The ONNX image still contains PyTorch on disk and is not an optimized deployment image.
No success or ₹0 hosting guarantee can be inferred from static validation.

## Full-backend screening and offline checks

`assessment.py` requires every full-journey stage, successful exit, no OOM/timeout,
at least 20% headroom (102.4 MiB; peak at most 409.6 MiB) and each Ask under 60s.
A passing synthetic screen is explicitly **not deployment approval**. A failed
stage still records its endpoint; only the final success event establishes completion.
CPU throttling counters and per-stage wall latency are retained in the summary.
The four-container count and existing outer timeouts remain unchanged.
Missing exit/OOM/timeout evidence, cgroup limits, CPU counters, memory endpoints,
completion events or required stages cannot pass the screen. Invalid/duplicate
stage measurements also fail closed. Results are written before the runner exits
nonzero if either constrained runtime fails its screen; artifact upload and cleanup
still run. A red experiment can therefore be a valid negative resource finding.

The public Ubuntu runner is nominally 4 CPUs/16 GB RAM/14 GB SSD according to
[GitHub's runner specification](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Actual free capacity is not guaranteed: the existing 5 GiB available-RAM and 14 GiB
free-disk guards must pass before sequential builds. The disk guard can reject a
nominal 14 GB runner; it is intentionally not reduced without build-space evidence.

Offline checks (no model, provider or Docker):

```powershell
& .\backend\.venv\Scripts\python.exe experiments/render-free/test_static.py
& .\backend\.venv\Scripts\python.exe experiments/render-free/test_application_probe.py
```

The wiring test substitutes Uvicorn's socket-serving wrapper and the embedding model;
it still executes the lifespan, routes and real supervised synthetic PDF extraction.
It is not a resource measurement. Hosted measurements must be obtained by manually
running the **existing** workflow after review; no additional workflow is needed.

## Gemini-mode full-backend screen (Phase 7G5N-A)

**Prepared offline only; no Render feasibility result or deployment approval.**
Use the existing manual workflow's `mode: gemini` input only after authorization.
The default `local` mode and its MiniLM/ONNX comparison remain available. The
combined MongoDB validation is not rerun by this experiment.

Reused components: run.py's cgroup-v2 monitoring, probe.py's stage measurements,
existing artifact/cleanup steps, production FastAPI lifespan and routes, CPU/PDF
supervisor, password executor, storage workers and existing database/GridFS/Qdrant
unit-test doubles. No new testing framework or workflow is introduced.

The Gemini experiment image uses the pinned Python base and existing CPU wheel
hashes/dependency specifications. It deliberately skips model packaging and ONNX
export; neither a local model nor weights are downloaded. Local-model source and
dependencies remain installed, but an import guard rejects torch, transformers
and sentence_transformers. This is an experiment image, not a new deployment
image. The production Dockerfile is unchanged. Build context is an explicit
source/dependency allowlist, with no environment files or repository credentials.

Configuration is process-local: Gemini embeddings (gemini-embedding-001, 768),
private access, budgets, growth limits and GridFS are enabled. JWT signing material
and the synthetic user's password are generated in memory. The Gemini key is a
noncredential placeholder; SDK operations are replaced before startup. Config
file reads are replaced with an empty fixture. Production defaults are unchanged.

### Workload and boundaries

One Uvicorn worker serves actual loopback HTTP requests inside `--network none`:
- Startup, liveness and readiness, with index and transaction responses simulated.
- Approved-user login and JWT-authenticated requests; no authentication overrides.
- Two concurrent real Argon2 verifications through the existing password executor
  (64 MiB per operation, unchanged hashing parameters); overlapping operations checked.
- KB creation and one bounded, three-page synthetic PDF, under 32 KiB.
- Actual GridFS upload service, intents and combined quota code using the in-memory
  bucket and transaction doubles; private materialization and supervised PDF child.
- Gemini SDK-simulated indexing at 768 dimensions, real vector payload validation,
  fake Qdrant retrieval, Search and Ask with citation validation.
- Seven Ask responses with actual history-retention/accounting code; every fake
  Gemini dispatch must have a dispatched budget entry.
- Concurrent login/PDF reprocessing, reindexing, then three rounds of two Ask
  requests, each followed by an idle measurement, and graceful shutdown.

MongoDB collections, sessions, indexes and GridFS bytes are in-memory doubles.
They share synthetic identities and records, but do not model real MongoDB socket
pools, TLS buffers, conflicts, server latency or durability. Actual policy functions
are not replaced. Client factories, bucket and provider SDK operations are the
mocked boundaries. This screen does not replace the prior real MongoDB validation.
The in-process HTTP client, fixture data and Mock call histories also consume
container memory, unlike a production external client; report this overhead.

Offline wiring uses ASGITransport and a lifespan-only server substitute, avoiding
local listening sockets. Remote execution uses real single-worker Uvicorn/HTTP.
No actual cloud requests are ever needed.

### Measurements and pass conditions

The existing runner requires 5 GiB available RAM and 14 GiB free disk before
building, and verifies Linux AMD64. Gemini mode runs only one constrained case:
512 MiB memory, 512 MiB memory+swap (zero additional swap), 0.1 CPU, 128 PIDs,
read-only filesystem, bounded tmpfs, nonroot, dropped capabilities and no network
or host-published ports. Limits are checked against cgroup files before screening.
The total container deadline remains 480 seconds, individual requests/startup
60 seconds, and idle shutdown 30 seconds. Limits are never relaxed on failure.

Host samples memory.current every 50 ms; memory.peak and memory.events capture
kernel peak/OOM evidence, including child processes and charged tmpfs/cache.
Stages report current memory, lifetime peak, elapsed time and cpu.stat deltas;
host samples also provide per-stage sampled peaks. A sampled peak is not an exact
per-stage kernel high-water mark. Lifetime peak is used for the headroom decision.
OOMKilled, exit status, deadline expiry and sanitized request/failure events are
retained in artifacts. Startup idle and post-round idle readings show retention.

Passing requires complete unique stage evidence, all enabled-policy markers,
768 dimensions, no local ML, successful requests, verified limits, no OOM/failure,
and peak <=409.6 MiB (102.4 MiB headroom). Screening rejects >8 MiB growth from
first to third warmed idle sample, or steadily increasing three samples totaling
>1 MiB. These declared short-workload heuristics do not prove absence of leaks.
Import/startup, PDF and password latency under throttling remain important risks.
No screen result constitutes production or live-provider approval.

### Remote dependency and commit review

New experiment files: Dockerfile.gemini, gemini_application_probe.py,
gemini_fixture.py, gemini_assessment.py, test_gemini_probe.py.
Modified: run.py, this README and the existing resource workflow only.
All Python backend source and test-source inputs are allowlisted, never .env.
No new package specification is added; httpx is already available through the
existing SDK dependency set. The normal CPU dependencies remain unchanged.

A commit containing only these experiment files is **not independently runnable
at HEAD 50ad04c**. The workload requires pending private-access, Gemini embedding,
budget-dispatch and growth/history/route integrations. In particular the current
backend changes to config, auth/auth_routes/users, database/main/readiness,
ask_history, documents/knowledge_bases, gridfs_uploads, document_embeddings,
embedding_execution/embeddings/gemini_embeddings/gemini_provider, rag/retrieval
and bulk/schema integrations must be reviewed together or explicitly isolated.
The fixture imports the currently untracked test_gridfs_growth.py,
test_growth_limits.py and test_usage_budgets.py, and their existing helper imports.
Their runtime import closure and the corresponding pending provider test helpers
must be included in a later staged-snapshot check. Do not stage all pending work
without review. No staging, commit, push or workflow execution is authorized here.
