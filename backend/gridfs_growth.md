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

This intentionally does not recover dispatched writes, partially deleted files,
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
