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
The inherited HTTP health check is disabled: this probe uses ASGI directly and has
no listening server. Production health-check configuration is unchanged.
Build downloads are public packages/model artifacts, not runtime provider traffic.

Stages: application import/idle, cold model initialization, one 14-text embedding
batch, 12-page PDF upload, supervised extraction/chunking and indexing. Real ASGI
routes and worker ownership are exercised through existing test fixtures; MongoDB,
Qdrant and authentication are replaced by their scoped test doubles. Production
chunk/batch defaults are restored after fixture creation. There is no Uvicorn server,
startup lifespan, live database client or Gemini call. This is a representative small
journey, **not worst-case 10 MiB PDF, maximum pages or concurrent-request testing**.

Linux cgroup memory includes children, native allocations, cache and tmpfs.
`memory.peak` is the exact **lifetime** peak observed before cgroup removal; per-stage
50 ms sampled peaks are lower bounds, not exact peaks. Stage endpoints also record
kernel lifetime peaks. Idle is application-import idle, not fully connected service
idle. CPU throttling, OOM state, exit code, deadlines, stage timings and raw synthetic
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
