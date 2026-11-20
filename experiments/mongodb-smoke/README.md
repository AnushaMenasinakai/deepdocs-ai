# Isolated MongoDB replica-set smoke harness

Run **Actions → Isolated MongoDB replica-set smoke tests → Run workflow** only
after reviewing the selected branch. There are no push/PR triggers, inputs,
repository secrets, production environment files, image publishing or deployment.
The D3B recovery extension has not been executed against MongoDB; static checks
are not a live pass. Earlier harness executions do not validate this extension.

The workflow uses Ubuntu 24.04 AMD64, a 20-minute job limit and a resource preflight
of 4 GiB available memory / 10 GiB disk. MongoDB 8.0.38-noble is pinned to its
Linux AMD64 manifest `sha256:9b73c38e39783bd21335f603a9f08be60fe385f5c28933ea32bb4a9fa1fd6d23`.
Docker Hub registry manifest bytes were independently SHA-256 checked during
preflight review (2026-11-12). The parent image-index digest was
`sha256:76a93cc399d05709baeea4e55385749657aa0085ad5b497c851380778eccea2c`.
The tester reuses the repository's digest-pinned Python 3.13.15 base and pins
PyMongo 4.18.1 and dnspython 2.8.0. Only a small allowlisted build context is sent.
Python package versions are pinned, but wheel hashes are not locked in this
isolated harness. Public registry availability remains a build prerequisite.

Each run/attempt gets its own database, replica-set identity, labeled containers
and image tag. MongoDB data lives only in disposable tmpfs. MongoDB has networking
disabled and listens on loopback; the tester shares that exact network namespace.
Neither runtime container has an external route or host-published port. Public
image/package downloads occur only before runtime. There are no external service
tests or provider credentials.

MongoDB error 14031 means `OutOfDiskSpace`. The original 512 MiB dbPath tmpfs
left insufficient free space after initialization for MongoDB 8.0's default
500 MiB index-build free-space safeguard. dbPath is now bounded at 1 GiB; this
is a maximum, not a preallocation. The existing 2 GiB container memory/no-swap
limit remains unchanged, and tmpfs usage counts against that memory limit.
After bootstrap, a container-local disk check requires 600 MiB available before
starting schema tests. No server safeguard is lowered or disabled. Host free-disk
checks alone cannot detect insufficient capacity inside tmpfs. The failed run
did not record exact free bytes; the next manual run will report this measurement.

Authoritative references:
- https://www.mongodb.com/docs/v8.0/reference/parameters/#mongodb-parameter-param.indexBuildMinAvailableDiskSpaceMB
- https://github.com/mongodb/mongo/blob/r8.0.0/src/mongo/base/error_codes.yml
- https://github.com/mongodb/mongo/blob/r8.0.0/src/mongo/db/index_builds_coordinator.cpp

A temporary CA verifies the server's localhost SAN. Authentication uses a random
keyfile and generated passwords. The localhost exception initializes the empty
replica set and creates a bootstrap administrator; it then creates a readWrite user
restricted to the uniquely named test database. The test container receives only
that scoped credential and CA, not the bootstrap password or server private key.
Secret files remain within a private runner-temporary directory and are not
uploaded as artifacts. The disposable hosted runner discards them after the job.
Both mongosh invocations use writable /tmp as HOME. The generated certificate is
checked for server purpose and both localhost/IP identities before startup.
TLS client certificates are not required: SCRAM credentials authenticate clients;
server certificate and hostname validation remain mandatory.

Before schema/data writes, the tester rejects unexpected database names, replica
identities, advertised members and application roles. The connection host is
hardcoded localhost, direct discovery is enabled and TLS verification is never
disabled. Schema helpers are invoked explicitly; GRIDFS_UPLOAD_ENABLED is not
changed and no application lifespan is started.

Smoke coverage: TLS-authenticated ping, writable primary/replica identity,
acknowledged majority write, snapshot transaction commit, explicit rollback,
required schema setup/verification and repeat-setup index equality. The actual
production schema module is tested, not a duplicate manifest.

Only one voting member is used: majority acknowledgement here means one member.
This does **not** test replicated durability, failover, concurrent admission,
GridFS streaming, interrupted writes, lost acknowledgements, recovery/fencing,
Atlas permissions or deployment suitability. No fault injection is performed.

On failure, fixed-stage output and a sanitized exception class identify the
boundary. No raw database exceptions, container logs, environment dumps or secret
artifacts are printed. Do not weaken isolation or index checks to obtain a pass.
The always-run cleanup validates resource ownership labels before removing only
these containers and the unique tester image; there is no global prune. For the
shared network namespace, cleanup removes the tester before MongoDB. Base images
are preserved. Temporary credentials are not uploaded; runner disposal is their
final cleanup boundary, including interruption cases. As with
any hosted job, runner loss can prevent cleanup; resources are runner-local.

Offline validation from repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest -q experiments/mongodb-smoke/test_contract.py backend/tests/test_storage_schema.py backend/tests/test_storage_transaction_errors.py backend/tests/test_storage_intents.py backend/tests/test_storage_reconciliation.py
```

Bootstrap follows MongoDB's documented localhost-exception procedure:
https://www.mongodb.com/docs/manual/core/localhost-exception/
Official image version metadata:
https://github.com/docker-library/official-images/blob/master/library/mongo

## Prepared GridFS lifecycle extension (not yet executed remotely)

The existing manual job now also calls `lifecycle.py` after the identity, schema
and transaction smoke checks. It reuses real GridFSPDFStorage, StorageIntents,
ReconciliationInspector and PDF extraction/chunking. Two PDFs are generated in
memory. Uploads are checksummed, published into disposable metadata, read back,
and re-read after closing/recreating the authenticated client. The new client
repeats replica identity and exact scoped-role checks before database access.

The isolated ledger policy permits two documents (production policy unchanged).
After one deletion, two concurrent reservations compete for one remaining slot.
Exactly one must succeed; a quota rejection or classified transaction conflict
is accepted for the other. Unknown outcomes fail the test; no retry is performed.
Deletion invokes the adapter and transactional finalizer, checks files/chunks
are absent, and checks accounting. A deliberately staged write_dispatched intent
is marked uncertain and inspected read-only. Its one-byte reservation must remain
while both real PDFs and their accounting are removed. Database/container teardown
removes this intentionally unresolved test record, not an application recovery sweep.

Limits: this is service/adapter integration, not HTTP-route or asynchronous worker/
parser-supervisor integration. Extraction is synchronous inside the bounded tester.
No vectors exist, so vector deletion is a vacuous prerequisite; no Qdrant is called.
The interruption is a staged durable state, not a network fault or a killed writer.
Client recreation is tested, not MongoDB restart/failover. Concurrent admission
uses the shared global/user count boundary; exhaustive byte-limit tests remain
in the existing offline suite. No automatic recovery or fencing is proven.

### One-time remote execution

1. Review these harness-only changes and offline results. No new workflow is needed.
2. With separate approval, commit/push the reviewed files to a branch in the same
   repository. A hosted runner cannot execute uncommitted laptop files. Include
   any source prerequisites that are not already on that branch; do not include
   unrelated pending files or secrets.
3. A repository user with Actions write permission selects the existing
   **Isolated MongoDB replica-set smoke tests** workflow, the reviewed branch,
   and **Run workflow** exactly once. Workflow-dispatch availability and Actions
   policy must permit this existing workflow on the repository/default branch.
4. Confirm runner preflight reports at least 4 GiB available RAM and 10 GiB free
   disk. Existing Ubuntu 24.04 AMD64 runner, 20-minute job, bounded 2 GiB MongoDB
   and 256 MiB tester remain unchanged. Actual hosted availability, account
   minutes/spending controls and organization policies must be checked before run;
   no paid runner or billing change is required by this plan.
5. Review every PASS checkpoint, concurrency outcome, retained-reservation message
   and always-run ownership-checked cleanup. Any missing stage is incomplete.

No repository secrets are supplied. Generated credentials and TLS keys are runner
local and never uploaded. Runtime networking cannot reach Atlas or AI providers.
Public package/image downloads occur at build time only. Runner disposal removes
private temporary files; existing cleanup removes only labeled test resources.
This preparation neither triggers Actions nor approves deployment/GridFS activation.

### Durable Gemini budget validation (Phase 7G5D)

The existing manual workflow now runs `budgets.py` after the GridFS lifecycle
checks. No workflow changes or secrets are required. Two independent asynchronous
PyMongo clients authenticate to the same disposable database and recheck replica
identity and scoped roles before any budget writes. Network isolation, TLS,
resource limits, ownership labels and cleanup remain unchanged.

The actual `usage_budgets.Ledger`, `admit` and index setup are exercised with a
process-local synthetic policy (global 3, per-user 2 for each request type).
No environment configuration is loaded: the settings loader is patched only
inside the experiment. The image contains the allowlisted config source, never
an environment file. No provider SDK is installed or called.

Prepared real-database assertions:
- Both embedding/generation global and per-user exhaustion, duplicate operation
  rejection, and UTC midnight rollover with a synthetic clock supplied to reserve.
- Eight simultaneous reservations across two clients, including same-owner and
  mixed-owner races. Cold first-ledger conflicts may fail closed with 503; tests
  require at least one admission, never over-admission, and matching stored counts.
- Idempotent confirmed pre-dispatch release and retained dispatched consumption.
- Reservation and dispatch acknowledgement loss injected after the real write
  returns. The actual admission function must reject and retain exactly one entry;
  replay of the same operation must not reach the simulated provider counter.
- Intrinsic unique `_id`, exact expiry index and 32-day timestamp, repeated setup.

TTL *configuration* is checked, not eventual background deletion. Future synthetic
days prevent the TTL monitor from deleting rollover fixtures. These tests do not
simulate a real network partition, process kill, failover, or provider request.
All cases share one disposable database; no synthetic user accounts are created,
only ObjectId owner identities. The existing harness timeout still bounds the
whole test container; the budget validation additionally has a 60-second limit.
A failure or a missing PASS checkpoint means validation is incomplete. Raw driver
messages and documents are not printed. Cleanup destroys only owned resources.

## Combined policy validation (Phase 7G5L)

The existing manual workflow is unchanged. It runs the original lifecycle and
budget cases plus `combined.py`, in the same disposable database with the same
scoped credentials. Exactly two synthetic PDF byte sequences are generated once
and reused. The allowlisted build context now includes growth/recovery/readiness
source modules. No production configuration file, provider SDK or route module
is copied. Policies are patched in this test process only; no settings loader
reads an environment file. TLS, role/replica guards, network isolation, resource
thresholds and ownership-checked teardown remain unchanged.

New prepared real-database checks:
- Combined quota admission and rollback, retaining the original lifecycle's
  uncertain reservation. The original isolated 2-document GridFS policy is reused;
  a 1-document growth limit exercises exhaustion independently.
- Two independently authenticated clients race for the remaining global slot.
  Both ledgers must agree, exactly one admission succeeds, and no mutation retries
  occur. An explicitly held competing transaction also forces a real write conflict.
- An acknowledgement is deliberately lost **after** a real admission commit.
  Both reservations must remain. This is not a real network partition.
- Pre-dispatch cancellation is represented by a retained prepared intent, not an
  actual request cancellation. A failed release rolls back real transactional
  writes; an explicit operator review then releases both quotas. Changed metadata,
  an actual stored original or a changed KB revision fences the reviewed action.
  Released operations cannot be dispatched by the old worker.
- A real original is saved and deleted, followed by atomic document metadata and
  combined quota release. No vectors exist; Qdrant cleanup is deliberately not tested.
- Post-dispatch interruption is represented by an uncertain intent: both quotas
  remain and reconciliation requires manual review. No orphan deletion is performed.
- Separate async clients insert/prune history concurrently. Conflicting requests
  may fail closed; successful requests retain exactly the newest two records.
  An injected failure after insertion verifies rollback of history and owner revision.
- Actual enabled-policy readiness fails before ownership/history indexes exist,
  then succeeds after explicit isolated setup, including a snapshot transaction read.
  Existing budget cases continue verifying the budget TTL and identity indexes.

The whole container has a bounded 180-second timeout (previously 120 seconds) to
allow the additional sequential cases. The async budget/history sequence retains
its 60-second bound. MongoDB memory, CPU and disk safeguards are unchanged.
A failed assertion stops the run; raw exception messages and documents are not
printed. Unresolved synthetic reservations are intentionally retained until
ownership-checked destruction of the disposable MongoDB container.

### Historical combined-policy candidate change set (superseded for D3C-R3)

Harness files (8):
- experiments/mongodb-smoke/Dockerfile
- experiments/mongodb-smoke/run.sh
- experiments/mongodb-smoke/smoke.py
- experiments/mongodb-smoke/lifecycle.py
- experiments/mongodb-smoke/budgets.py
- experiments/mongodb-smoke/combined.py (new)
- experiments/mongodb-smoke/test_contract.py
- experiments/mongodb-smoke/README.md

Required application prerequisites (6):
- backend/storage_intents.py: combined growth reserve/release additions
- backend/growth_limits.py (new)
- backend/gridfs_growth.py (new)
- backend/gridfs_growth_recovery.py (new)
- backend/policy_readiness.py (new)
- backend/config.py: **only** GrowthSettings and load_growth_settings additions

Do not stage all of config.py: it also contains private-access and PDF processing
configuration changes unrelated to this focused harness. Existing dataclass,
dotenv and os imports already support the selected additions. The committed
usage_budgets.py/config budget settings and storage_schema.py are prerequisites,
not new changes. No additional packages or workflow changes are required.
The current complete working tree is not an isolated commit candidate; selective
config staging and review of the resulting snapshot are required before committing.
Do not include route, frontend, provider-integration or deployment changes.

### Limits of this validation

Offline contract tests cannot establish real MongoDB results. A later explicitly
authorized manual workflow run is still required. These tests call the actual
storage adapter, transaction ledger, recovery service, history retention and
readiness helper; they do not execute FastAPI routes, real request cancellation,
PDF subprocess shutdown, provider calls, active worker takeover, failover or
actual TTL expiration. Readiness here verifies database prerequisites, not a full
application lifespan. Production GridFS remains disabled. This phase authorizes
no Docker execution, staging, commit, push or workflow dispatch.

## D3C-R3 recovery validation preparation

Current transfer set: the twelve reviewed D3B modified files plus these five
harness files: `combined.py`, `run.sh`, `Dockerfile`, this `README.md` and
`.github/workflows/mongodb-replica-smoke.yml`. All paths here are relative to
`experiments/mongodb-smoke` except the workflow. No selective config changes or
new dependencies are required for this extension. The historical file list above
must not be used as the current staging list.

Twenty named scenario groups run before the existing combined-policy checks.
Four cover atomic preparation rollback, missing/foreign KBs, real concurrent KB
deletion conflicts, and simulated lost preparation acknowledgement. Eight groups
run in each of growth-off and growth-on modes: preparation/duplicate rejection;
corrupt/incomplete chunks and identity conflicts; maintenance and changed review;
publication rollback; publication/matching metadata/idempotency; deletion
orphan/vector/claim evidence; deletion rollback; deletion acknowledgement loss
and exactly-once release. Each group has multiple assertions, not separately
counted tests. A failure stops execution; subsequent groups are `not_run`, never
passed or silently skipped. Existing lifecycle, budgets, history and readiness
cases remain in the original execution path.

The same two synthetic PDF fixtures, database, scoped user and persisted test
quota policy are reused. Recovery receives that same policy via a constructor
patch; its transaction and recovery methods are the production implementations.
Duplicate operation IDs fail during preparation; a second operation for the same
document is denied at quota admission. The fixture removes only that proved
never-dispatched duplicate intent before publication review. Production does not
perform this fixture cleanup. No existing uncertain reservation is released.

Concurrency is a genuine server write conflict: one client holds an uncommitted
KB deletion while another authenticated client attempts preparation. It must
receive the classified write conflict, with no orphan intent after deletion
commits. Rollback injection raises inside a real transaction after its mutations.
Acknowledgement injection raises **after a real successful commit**; it is not a
network partition, driver-generated unknown commit result, failover or failpoint.
No mutation is automatically retried. Review is repeated explicitly to recognize
an already-finalized operation. PDF readback is compared byte-for-byte after
publication; adapter save/delete calls are forbidden during recovery finalizers.
There are no Qdrant dependencies or vector calls. The vector-cleaned stage is an
affirmative synthetic fixture assertion because these documents never had vectors.

This validates StorageIntents, GridFS and recovery primitives, **not**
GridFSUploads, HTTP routes or upload cancellation orchestration. Existing focused
mocked tests cover uncertain preparation preventing remote dispatch. Full lifecycle
integration remains a D5/D6 requirement. Maintenance attestation is valid only
after this sequential fixture's writers have completed; it is not worker fencing.

The tester deadline increases from 180 to 300 seconds for the additional real
transactions and bounded conflict waits. The job remains 20 minutes and the main
step 18 minutes. Build/pull/startup delays can still exhaust that deadline: failure
must be reviewed, not automatically rerun. Memory (4 GiB available), disk (10 GiB)
and separate dbPath (600 MiB) preflights are unchanged.

Evidence is allowlisted to `sources.json`, `resources.json`, `recovery.json`,
`execution.json` and `cleanup.json`, retained for three days. It includes source
SHA-256 hashes and candidate commit, preflight measurements, verified MongoDB
version/topology, named outcomes/durations, counts and cleanup status. No raw logs,
database documents, credentials, private keys or entire temporary directories are
uploaded. `recovery.json` covers the new twenty groups; `execution.json` covers the
whole harness. Both must succeed, and cleanup must succeed. An absent/incomplete
report is not a pass. Artifact upload success cannot override a failed step.

Only the evidence directory is writable from the nonroot tester. Container/image
cleanup uses exact names and verified ownership labels. Data is tmpfs and is
destroyed with the owned MongoDB container. Abrupt runner loss or job cancellation
may prevent cleanup/artifact steps; missing evidence must be reported as unknown.
Generated credentials remain in runner-private temporary storage, never artifacts,
and disappear with the ephemeral GitHub runner. Do not run this harness on a
persistent shared runner.

After separate approval, export baseline HEAD to an isolated checkout outside the
working tree, overlay exactly the twelve D3B and five harness files, compare their
hashes, and commit on `codex/gridfs-recovery-validation`. Preserve the dirty main
checkout and five unrelated untracked files. Review branch diff and external
deployment webhook policies before pushing. Dispatch the existing manual workflow
once against that branch and verify the run SHA. Do not open a PR or merge as part
of validation. Confirm the repository/account Actions allowance, artifact budget,
spending controls and standard-runner eligibility first; free execution is not
assumed. Do not select paid/larger runners or activate billing.

### R5 acceptance evidence and cleanup gate

After cleanup, the workflow runs `bash experiments/mongodb-smoke/run.sh
--verify-evidence`. This host-only Python standard-library validator requires all
five allowlisted JSON files (each at most 256 KiB), rejects duplicate JSON keys,
and checks the actual checkout SHA against `GITHUB_SHA`. It recomputes the exact
allowlisted source hashes, including all twelve D3B files and the harness README.
The three resource thresholds, MongoDB 8.0.38/single-member topology, explicit
execution completion and independently recorded cleanup must all pass.

The gate checks the exact twenty unique scenario identifiers, not just their
count. Every outcome must be `passed`; failed/skipped/not-run counts must be zero,
and counts, durations and coverage labels must agree. Missing or malformed
evidence, unexpected identifiers and unknown cleanup fail acceptance. Upload runs
even after failure, with `if-no-files-found: error`; its five-file allowlist and
three-day retention are unchanged. Upload success never changes earlier failures.
The gate has a one-minute step bound within the unchanged twenty-minute job.

Cleanup inventories containers/images through successful bounded Docker API
commands. Only an affirmative inventory can establish prior absence. Present
resources require exact identity and matching ownership before removal, followed
by a fresh inventory proving absence. Tester, MongoDB, and tester image remain in
that order. A failed inventory/inspection, ownership mismatch, failed removal or
unconfirmed post-removal absence stops cleanup and records failed/unknown state;
later resources remain unknown rather than being blindly removed. Evidence
contains sanitized phase/category values, never Docker error text. Each Docker
command has an eight-second bound; no retries or broad pruning are performed.

Publication and deletion rollback checks now compare exact scoped persisted
participants: current/linked intents, KB, metadata, full bounded quota ledger,
owner growth record, application chunks and original file metadata/chunks.
Deletion includes one owned synthetic application chunk and verifies its rollback
and final removal. Matching publication metadata must remain exactly unchanged:
the production finalizer does not update an existing matching document. Repeated
publication/deletion finalization compares the complete post-finalization snapshot
and checks capacity again. Original byte and identity preservation remains checked.

Synthetic validator and simulated Docker-response tests exercise the host helpers
without contacting Docker or MongoDB. Their success is evidence about acceptance
reporting only, not a real-database test pass. Final Bash syntax validation must
refer to the corrected scripts; prior-version syntax evidence is not sufficient.
