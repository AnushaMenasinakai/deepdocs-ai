# Optional PDF storage foundation (not activated)

`storage_contract.PDFStorage` is a synchronous internal worker interface. The
existing `get_document_storage()` still returns the unchanged `DocumentStorage`.
`LocalPDFStorage` is an opt-in owner/knowledge-base-scoped facade; closing its materialization never deletes
the original. No routes, configuration defaults or upload limits have changed.

`GridFSPDFStorage` requires a caller-supplied synchronous PyMongo database, trusted
owner/knowledge-base ObjectIds and an existing private temporary root. It creates
no client and makes no request at construction. GridFS manages `pdf_blobs.files`
and `pdf_blobs.chunks` on first use. Do not supply the application's AsyncDatabase
to this synchronous adapter. Scoped fake buckets are injectable for offline tests.

Save reuses local validation/staging, then streams bounded chunks under a PyMongo
client-side deadline. The document ObjectId is the immutable file ID; callers must
allocate it server-side, never reuse it, and retain it in an operation record before
dispatch. Remote exceptions are uncertain, including finalization timeouts. No
automatic abort, orphan deletion, retry or quota release is performed. A thread
deadline is not proof that a remote operation stopped or failed to commit.

`reference()` is part of the protocol: it validates an internal reference without
performing remote IO; its return value is backend-specific, not necessarily a path.
References require typed ObjectIds matching owner, knowledge base, backend and file ID; download
metadata must agree. `exists` confirms valid file metadata, not full chunk integrity.
Materialization verifies bounded length and PDF signature. The common deletion
contract is `delete_confirmed()`: returns DeletionOutcome.DELETED on acknowledged
deletion, or ABSENT only where absence can be established (local unlink missing).
GridFS metadata absence raises DeletionUncertain with outcome UNCONFIRMED; it never
authorizes orphan deletion or quota release. Remote deletion failures likewise raise.
Outcomes reject boolean conversion to prevent truthiness-based success handling.
GridFS `delete()` delegates the same semantics; the local inherited `delete()` retains
its original None return and idempotent missing-file behavior for compatibility.

`compensation_document()` builds full scope from trusted authorized IDs plus a
confirmed save receipt, rejecting conflicting IDs. Both facades expose `compensate()`
with the same explicit deletion contract. This helper does not authorize cleanup:
the caller must first establish that compensation is safe. It must never compensate
an uncertain upload or metadata publication merely because the request failed.

StorageUncertain derives from the existing StorageCleanupError retention category.
The current upload lifecycle therefore retains the KB reservation if this exception
reaches it, even when the worker finished. No production lifecycle code was changed.
Existing deletion code still ignores return values and must be updated before wiring
the new protocol. Exceptions must propagate; catching StorageError and continuing
cleanup/reservation release is forbidden. Runtime typing cannot prove an ObjectId
was server-generated: authenticated caller ownership checks remain mandatory.

MaterializedPDF is an explicit lease, intentionally not a request-scoped context
manager. Attach the supervisor's concurrent.futures completion Future using
`retain_until` before exposing the lease to cancellation cleanup, then request
`close`. Cleanup waits for all attached native completions. An asyncio request Task
is rejected. The caller must own the submission/attachment sequence and confirm
that completion means child reaping/IPC closure. This is not yet integrated with
CPUWorkers or document_processing. Do not wrap current `run_pdf` in a temporary-file
context: cancellation can return while the native worker still uses the path.

Still required before activation: global/per-user atomic quotas, durable operation
records and reconciliation, integration with owned worker cleanup, a bounded client
lifecycle, migration policy for existing local PDFs, checksums, private temporary
directory deployment permissions, isolated real-GridFS tests and restart testing.
The adapter's 5 MiB ceiling applies only to explicitly constructed GridFS instances;
the active local backend retains its existing 10 MiB default. This foundation is
not approval for Render deployment or proof of live Atlas compatibility.
