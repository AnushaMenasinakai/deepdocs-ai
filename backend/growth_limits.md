# Optional private-deployment growth limits

`GROWTH_LIMITS_ENABLED=false` preserves local development behavior. Enable only
after isolated real-MongoDB transaction testing and review of existing records.
No actual environment files are changed. Defaults when enabled:

| Variable | Limit |
| --- | ---: |
| GROWTH_MAX_KNOWLEDGE_BASES | 5 per user |
| GROWTH_MAX_DOCUMENTS | 10 per user across knowledge bases |
| GROWTH_MAX_PAGES | 100 per PDF |
| GROWTH_MAX_CHUNKS | 500 per PDF |
| GROWTH_MAX_HISTORY | 100 retained Ask entries per user across knowledge bases |

Each value accepts 1–1000. Existing upload limits (10 MiB locally), PDF text/byte
limits, worker/admission limits and Gemini budgets still apply independently.
Processing uses the smaller of the existing PDF_MAX_PAGES/PDF_MAX_CHUNKS values
and this policy. It rejects oversized PDFs/chunk output before embedding. Existing
processed originals are checked again before indexing/reindexing; they are not
silently truncated or deleted. Above-policy originals require explicit user action.

## Durable admission

The `resource_usage` collection uses the intrinsic unique `_id` for the owner ID.
Its documents hold a revision and bounded document/knowledge-base reservation ID
arrays. A five-second snapshot transaction writes the owner revision before
checking existing owner-scoped metadata and pending reservations. A majority
commit reserves the new immutable server-generated ID before creation/storage.
All enabled workers contend on the same owner document. No independent count-and-
insert race, process-local lock or automatic mutation replay is used.

Existing documents, existing KBs and legacy KB upload reservations count toward
admission. Over-limit existing resources remain untouched. Duplicate reservation
IDs fail closed. Transaction conflicts, unavailable databases and unknown commits
return sanitized 503 errors; exhausted capacity returns 409. A lost commit may
consume capacity even if creation did not start. Reservations have no TTL.

Only confirmed compensation or completed deletion releases reservations. Local
document deletion retains the existing ordering: mark vectors stale, delete exact
scoped Qdrant vectors, delete the original, chunks and metadata, then release this
policy's capacity. Vector failures prevent final successful deletion. Uncertain
cleanup and failed accounting release may leave retained capacity; a restart does
not erase it. Do not release IDs merely because metadata is currently absent:
an earlier write or worker could still complete. No automatic reconciliation or
new recovery endpoint is introduced. Operator inspection and proof of terminal
cleanup are required before repairing retained capacity.

## Ask history

When enabled, insertion and deletion of older history entries share the same
owner-serialized transaction. Retain the newest entries by created_at and _id,
across all of that owner's KBs. Only history snapshots are pruned, never documents,
KBs, chunks or vectors. A new owner/created_at index supports the query. Up to 500
obsolete entries are removed in one transaction. Larger legacy overflow fails
closed without inserting or pruning and requires an explicitly reviewed migration.
Concurrent inserts cannot exceed the retained limit after successful commits.
Transaction failure rolls back insert and pruning together; an uncertain commit
is reported as unavailable rather than retried. Existing post-insertion KB
existence checks and history deletion APIs remain in place. History expiry is
irreversible; users must export anything they need before enabling retention.

## Activation and limitations

All workers must use the same enabled policy; stop admissions/drain old workers
before changing it. An old process with limits disabled can bypass new admission
checks. No global owner-count, account-count, byte quota or Qdrant collection-wide
quota is added. Bounds assume a small operator-provisioned account population.
Database headroom, daily API budgets and private access remain separate controls.

GridFS remains disabled by default. When both policies are enabled, its existing
quota-reservation transaction also reserves per-owner growth capacity. The same
document ID is counted once within each policy. GridFS's stricter limits still
apply independently (5 documents/10 MiB per user, 20 documents/50 MiB globally,
5 MiB per file). Confirmed deletion releases both reservations in the existing
finalization transaction. See `gridfs_growth.md` for coordination and recovery.

Offline ledger tests use a serializing transaction double. The subsequent isolated
combined MongoDB validation at commit 50ad04c was previously reported as passing,
but was not independently reverified during this documentation preparation. It
exercises real combined admission, accounting release, concurrent history retention
and rollback. Acknowledgement loss is simulated after commits, not a real network
partition. Local-document service handoff tests in test_upload_safety.py reuse
temporary storage and observe the ledger boundary; they do not repeat real-database
validation. These results do not establish full application cancellation/restart
recovery or production Atlas readiness. Keep growth and GridFS disabled until
separately approved.
