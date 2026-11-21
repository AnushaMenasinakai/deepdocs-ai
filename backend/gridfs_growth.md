# GridFS and growth-policy coordination

Both flags remain false by default. No production configuration is changed.
All instances must use the same policies; drain old instances before activation.
Existing legacy originals count through metadata, while pending local/GridFS
uploads count through KB reservations. Do not activate during legacy in-flight work.

## Admission and deletion

GridFS validates the PDF privately, reserves its owner-scoped KB identity, and
creates its durable intent as before. The existing `StorageIntents.reserve`
transaction now optionally writes the owner's `resource_usage` revision and
document ID alongside the GridFS quota ledger and intent. Either both commit or
neither does. Distinct clients contend on those MongoDB records; conflicts fail
closed without replay. Existing growth reservations and metadata are unioned,
not added together. This upload's own KB reservation is excluded from prior usage.

Growth defaults to 10 documents per owner; GridFS retains its independent, stricter
5-document and 10 MiB per-owner allowances, 20-document/50 MiB global limits and
5 MiB per original. The stricter applicable allowance wins. Growth exhaustion
returns a sanitized 409; GridFS quota exhaustion remains 413. A confirmed rejection
records failed-before-dispatch and removes the KB reservation. No GridFS write is
attempted. Unknown transactions retain their reservations and reconciliation work.

The intent's `growth_reserved=true` marker persists the accounting obligation.
Publication does not allocate another growth slot. Checksum readback, owner/KB
identity checks, original immutability and parser leases are unchanged.
`finalize_deletion` releases growth capacity inside the same transaction that
removes metadata and releases GridFS quota, after confirmed vector/file deletion.
The marker, rather than the current feature flag, controls release. Legacy uploads
without this marker retain their existing deletion behavior.

## Operator-reviewed recovery (no public endpoint)

`gridfs_growth_recovery.review(database, identity)` and `apply(...)` are internal
synchronous operator interfaces, not HTTP endpoints. Run only in an approved
operator process or owned storage worker using the existing TLS-verified client.
There is no automatic scheduler, retry or cleanup. Never call these on the event loop.

Procedure:
1. Verify the intended database and full server-issued operation/document/owner/KB
   identity against durable intents. Obtain separate authorization for live writes.
2. Call `review` with that `IntentIdentity`. Inspect its stage and proposed action.
   It reads metadata, exact GridFS file ID and orphan chunks, document chunks,
   both ledgers and the owned KB. It prints no payloads or credentials.
3. Only a reserved upload still at `prepared` or `failed_before_dispatch`, with
   no document, file, GridFS chunk or processed chunk, can propose release. Missing
   file metadata alone is insufficient. Post-dispatch ambiguity is always blocked.
4. Explicitly approve the returned token, then call
   `apply(database, identity, review_token=token, operator='operator_audit_id')`.
   Apply repeats all reads in a transaction, rejects changed reviewed state, fences
   the intent at failed-before-dispatch, removes the KB reservation and releases
   both quotas atomically. The old worker cannot advance from prepared afterward.
   A bounded audit field records the operator and token; no payload deletion occurs.
5. If acknowledgement is lost, inspect again. A committed release reports
   `already_released`; never blindly repeat the old token. A failed/unknown read
   does not prove absence. Any identity conflict requires manual investigation.

The pre-dispatch release interface intentionally does not recover dispatched writes, partially deleted files,
unpublished committed originals or arbitrary legacy growth reservations. Those
states retain capacity. No worker is considered stopped merely because its
heartbeat is stale. Operators must not edit stages to manufacture eligibility.

## Remaining validation

Offline doubles test atomic rollback, independent worker admission, quota rejection,
interruption, metadata acknowledgement loss, exact deletion and recovery fencing.
A subsequent isolated combined MongoDB validation at commit 50ad04c was previously
reported as passing, but was not independently reverified during this documentation
preparation. Its cases exercise real combined transactions, separate-client admission,
write conflicts and reviewed release fencing. Lost acknowledgements are simulated
after real commits; cancellation is represented by retained intent states. This does
not prove network-partition recovery, full-route cancellation, worker takeover or
production Atlas readiness. Both flags remain disabled pending operational approval,
backup/restore verification and the remaining recovery procedures.

## D3B maintenance-only finalization (activation remains unapproved)

New explicit interfaces in `gridfs_growth_recovery` are `review_publication`,
`apply_publication`, `review_deletion`, and `apply_deletion`. Existing `review` /
`apply` pre-dispatch growth release remains unchanged. No route or scheduler calls
these functions. Use the synchronous owned worker/client and existing schema.

Every new call requires `maintenance` with exactly `operator`, `evidence_id`,
`writers_stopped=True`, and `inflight_resolved=True`. Operator/evidence identifiers
are bounded audit identifiers, not credentials. They attest external evidence:
stop ALL application writers, drain or establish termination of storage/parser
workers, and independently resolve in-flight database/provider commands before
review. Keep writers stopped throughout review/apply and acknowledgement readback.
A boolean is not an executable fence; if termination or in-flight completion cannot
be verified, DO NOT attest it. Quarantine instead. Stale heartbeats, expired claims,
service restart and missing GridFS metadata do not authorize mutations.

1. Verify database and exact persisted operation/document/owner/KB identity.
2. Obtain separately approved maintenance/write authorization. Retain evidence.
3. Call the appropriate review function with `IntentIdentity` and maintenance.
4. Approve its action and exact `review_token` out of band. Call the matching apply
   with the same maintenance and token. No PDF bytes/filenames are printed by review.
5. Unknown commit: do not replay writes or assume rollback. Review afresh. A matching
   completed operation reports `already_finalized`; apply is then a no-op. Changed
   state invalidates old tokens. Unavailable reads retain quota and obligations.

Publication checks a durable descriptor, full original checksum/chunk structure,
identity, accounting and absence of conflicting operations. Metadata insertion,
intent confirmation and quota commitment share one transaction. Legacy records
without the descriptor are quarantined; never guess original filenames.

Deletion requires persisted `vectors_deleted`/`file_deleted` acknowledgement (or
an already-completed transaction), exact claim/source identity, no file metadata
AND no GridFS chunks. It reuses transactional application chunk/metadata/KB and
quota cleanup. It never deletes vectors or originals. Missing vector evidence,
orphan chunks, overlapping operations and identity changes remain quarantined.

Limits: no worker takeover, no automatic retries, no payload deletion, no scans or
new indexes. Snapshot review cannot fence a writer that violates maintenance.
Offline doubles are not proof of real MongoDB/Atlas recovery. Disposable replica-set
validation with transaction conflicts and commit uncertainty is required next.
