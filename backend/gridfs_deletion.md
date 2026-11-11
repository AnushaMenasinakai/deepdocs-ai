# Optional GridFS deletion

The existing GRIDFS_UPLOAD_ENABLED flag remains false. Its optional lifespan now
also provides GridFS deletion using the same bounded synchronous client/workers.
Local documents (including records without a backend marker) retain their existing
deletion path. Unknown or unavailable backends never fall back to filesystem deletion.
Single and bulk routes use the same service after existing authorization/admission.

The existing exclusive document claim prevents conflicting processing/deletion.
Its token is also the durable deletion intent ID. Every destructive metadata action
uses that token, owner, KB and document scope. The original file ID must equal the
document ID; the referenced upload intent must be published, committed and certain,
with matching size/checksum/owner/KB/file identity. Uncertain uploads require review
before deletion, rather than being repaired by a deletion request.

Stages:

`prepared → delete_dispatched → vectors_deleted → file_deleted → delete_confirmed`

Intent creation precedes vector invalidation and remote file deletion. Existing
vector cleanup remains authoritative. GridFS deletion runs off-loop and accepts
only DeletionOutcome.DELETED. Missing file metadata is not proof of orphan-chunk
absence; it retains quota and the document claim without another delete attempt.

After vectors and the exact GridFS original are confirmed deleted, one snapshot /
majority MongoDB transaction removes owned chunks, KB reservation and exact-token
document metadata, records delete_confirmed, and releases the upload quota entry.
Failure rolls all these metadata changes back. No callback/transaction retry is
performed. The final transaction may commit despite acknowledgement loss: in that
case durable state contains both completed cleanup and released quota atomically.
The API reports uncertainty, never invents rollback or replays remote deletion.

Request cancellation marks an application-owned operation abandoned. It does not
cancel an active storage worker or release document admission before native work
finishes. Checkpoints prevent later stages after abandonment. Cancellation racing
the final transaction cannot undo a commit; consult durable state. Unexpected
direct cancellation of the owned task or unknown dispatch retains ownership.

Partial deletion retains the claim, intent and quota unless the entire final
transaction committed. Static error categories are best-effort; if recording them
also fails, nonterminal intents still require review. No automated orphan deletion,
blind retry, claim expiry, recovery endpoint or quota-release sweep is introduced.
Repeated successful HTTP deletion returns the existing not-found response; failed
deletions remain busy until independently reconciled.

Remaining validation: real isolated MongoDB transaction/rollback and GridFS fault
testing, reconciliation tooling, restart recovery, backup procedures and operational
monitoring. Offline doubles do not establish Atlas compatibility or production
readiness. No live services or deployment are needed by these tests.
