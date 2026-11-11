# Optional GridFS processing and reprocessing

The existing disabled-by-default GRIDFS_UPLOAD_ENABLED switch also owns the
optional storage client and processing service. No client or GridFS access is
created in local mode. No environment values are changed by this phase.

Processing selects the backend from the claimed document, never a request field:
missing marker or `local` uses the unchanged local path; `gridfs` requires the
optional service and its immutable file ID. Unknown backends and unavailable
services fail closed. There is no fallback between backends. Existing local
documents remain local even when the optional service is available.

Before remote access, validate server-owned document, owner and KB ObjectIds,
file identity, expected size and SHA-256. GridFS stream metadata is checked against
the same scope. Materialization reads only the original into a private temporary
directory and verifies the bytes against upload metadata. Missing files, metadata
mismatches and corrupt originals never reach the PDF parser. No remote write,
overwrite, migration or deletion is performed by the processing adapter.

The existing processing session reserves DOCUMENT/CPU before claiming metadata.
ParserLeases borrows that session, registers a retained job before materialization,
and dispatches through CPUWorkers' sole existing PDFSupervisor. It does not reserve
a second slot. The processing session spans chunk publication after lease cleanup.

Request cancellation leaves the owned materializer/parser task running. An abandoned
materialization is cleaned without parser dispatch. After dispatch, only native
supervisor completion following child termination/reaping permits cleanup. Reaped
crashes clean the lease but preserve the existing uncertain-processing claim.
Unknown completion and cleanup failure retain ownership and capacity. They may
intentionally block shutdown; forced termination is not evidence of safe cleanup.

The existing chunk generation CAS, old-generation cleanup, vector invalidation,
and processing/reprocessing publication rules are unchanged. No successful state
is published before parsing and lease cleanup complete. Read/integrity failures
in the remote extraction path conservatively retain the processing claim for
review, rather than guessing that a retry or release is safe.

Application shutdown first drains admission ownership, which includes borrowed
lease holds. CPU workers then close their supervisor. The optional storage lifespan
drains its lease registry before closing the synchronous storage workers/client.
No extra native supervisor is created by the processing service.

Remaining blockers: GridFS deletion/quota release, reconciliation of retained
claims and intents, restart recovery of ambiguous temporary files, and isolated
real MongoDB/GridFS validation. Offline tests use synthetic PDF bytes and database
doubles; they do not establish Atlas readiness or approve deployment.
