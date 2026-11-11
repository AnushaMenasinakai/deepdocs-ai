# Optional GridFS upload integration — disabled by default

`GRIDFS_UPLOAD_ENABLED=false` preserves the existing local upload path and its
10 MiB limit. Local mode does not construct a synchronous client, GridFS bucket,
storage workers or storage collections. Do not enable this in production yet.
No existing environment file is changed by this implementation.

When explicitly enabled, the application lifespan owns one bounded synchronous
MongoDB client, using the existing MONGODB_URI/MONGODB_DB_NAME configuration,
verified TLS, bounded timeouts and disabled automatic driver retries. Metadata
writes use majority acknowledgement. The worker pool permits one active and one
queued operation. Streams are opened, consumed and closed inside their worker.
The existing admission service drains upload ownership before the storage client
and other database dependencies close.

## Upload sequence

1. Existing authentication and owner/KB checks run before upload admission and
   multipart parsing. GridFS selection occurs only after that existing boundary.
2. Allocate new server-owned document/operation IDs; the immutable GridFS file ID
   equals the new document ID. Reuse the upload stream's SaveLifetime ownership.
3. In one bounded storage worker, validate PDF filename, MIME, signature and size
   using DocumentStorage in a private temporary directory; compute SHA-256.
4. Recheck owner/KB atomically while reserving the document against KB deletion.
5. Persist the intent, then reserve quota through the existing transactional
   ledger. Defaults: 50 MiB global, 10 MiB/user, 5 MiB/file, 20 documents global,
   5/user. Concurrent transaction conflicts fail closed, without automatic retry.
6. Persist write_dispatched before invoking scoped GridFS save. Read back the
   finalized file with owner/KB metadata validation and verify size and SHA-256.
7. Record file_confirmed, publish documents metadata, record published, and commit
   the reserved quota. Public DocumentResponse fields are unchanged.

The adapter still uses the dedicated pdf_blobs bucket. No application request may
provide an existing file ID or select another owner's storage reference.

## Failure policy

Validation or worker admission rejection before any metadata mutation leaves no
remote data. Confirmed quota exhaustion records failed_before_dispatch and releases
the KB reservation; it never creates a quota entry or file. Other pre-dispatch
uncertainty conservatively retains reservations rather than attempting cleanup.

Cancellation sets a worker-visible flag but never cancels native IO. SaveLifetime
prevents stream closure and upload-slot release until actual worker completion.
At checkpoints the worker stops publication and marks reconciliation required.
A cancellation racing successful publication may leave a committed document;
it never authorizes deletion. Timeouts, lost acknowledgements, invalid readback,
metadata publication uncertainty and quota-commit failures do not delete files,
release capacity or retry writes. Diagnostics contain static categories only.

If recording uncertainty also fails, the last durable stage remains authoritative:
all nonterminal intents and published-but-uncommitted reservations require review,
even when reconciliation_status has not been updated. A KB-reservation write or
intent-creation acknowledgement can also be lost before a confirmed intent exists;
the KB document ID must then be reconciled against intents/files/metadata. Never
infer absence or safe release from a timeout. There is no automatic reconciler,
orphan deletion, stale-worker expiry or client retry endpoint in this phase.

## Remaining activation blockers

- Processing/reprocessing now resolve persisted GridFS identities through the
  trusted parser lease service (see gridfs_processing.md). Deletion and atomic
  quota release use the separate service in gridfs_deletion.md. Local storage
  rejects explicit non-local references; existing
  local documents stay local even if the upload flag is enabled. The flag alone
  does not establish production readiness for the GridFS document lifecycle.
- Validate transaction support, collection/index setup, majority durability and
  quota policy on an isolated real replica set before any production activation.
  Offline transaction doubles do not establish Atlas compatibility.
- Add scoped reconciliation, restart recovery, backup and
  operational capacity monitoring. Existing local PDFs are not migrated or counted
  by this new PDF quota ledger; shared-cluster metadata headroom remains separate.
- Temporary-file cleanup failures need operational review. No ambiguous remote
  file is removed automatically and no deployment is approved by these tests.

Offline coverage exercises the real adapter, intent ledger and HTTP route using
synthetic PDFs, a transactional database double and an in-memory GridFS bucket.
