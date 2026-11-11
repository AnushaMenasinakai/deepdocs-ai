# Disconnected reconciliation inspection

ReconciliationInspector is read-only and is not wired to routes, application startup,
timers or the GridFS feature flag. It creates no client, collections or indexes.
GRIDFS_UPLOAD_ENABLED remains disabled by default. Production/local behavior is unchanged.

An operator must supply an explicit IntentIdentity: operation, document, owner, KB,
operation type and backend. Execute `inspector.inspect(identity)` through an
operator-owned bounded StorageWorkers.run callable, never on the event loop. There
is no automatic scan or batch-discovery API. Inspect one known ID at a time. The
operator must obtain identities through an independently authorized procedure;
this internal service is not a replacement for authentication.

## Read bounds and consistency

Each call requires a snapshot-enabled synchronous session, a five-second overall
driver deadline and one-second server query limits. There is no mixed-time read
fallback. Existing index names are explicitly hinted for related intents, GridFS
chunks and application chunks. Missing indexes or unsupported snapshots return
inspection_unavailable_or_bound_exceeded; the service never builds indexes.
Verify these requirements on an isolated real replica set before operational use.

Reads use exact IDs or owner/document index prefixes, narrow projections, at most
21 related intents (20 plus an overflow sentinel), and at most 81 GridFS chunks.
Chunks arrive one at a time and must match the application's 64 KiB layout. Total
accepted PDF bytes are bounded to 5 MiB; SHA-256 is computed without decoding,
logging or writing PDF text. A malformed individual MongoDB response is still
subject to MongoDB's BSON size limit before client validation. Missing GridFS
metadata permits only a projected chunk-presence inspection, never reading orphan
contents or assuming chunks are absent.

## Classifications

| Classification | Meaning and allowed next step |
| --- | --- |
| confirmed_consistent | A published upload or finalized deletion agrees with scoped metadata, file/chunk observations and quota in one snapshot. This is not proof that a future/stale worker cannot change it. |
| incomplete_safely_resumable | A prepared upload has no document/file/chunks at the snapshot and consistent retained capacity. Only read-only inspection is safe to resume. No upload, publication or release is authorized. |
| ambiguous_manual_review | Interrupted dispatch, publication uncertainty, missing metadata/orphan chunks, retained reservations, overlap, read failure, or incomplete cleanup. Preserve obligations and inspect independently. |
| identity_conflict | Ownership, KB, immutable file/document identity, checksum/length references, claim token, schema or quota identity disagrees. Fail closed; do not repair by guessing an identity. |

Every report explicitly sets mutation_authorized=false and
resume_scope=read_only_inspection. No current persisted state provides fencing or
proof of old-worker termination sufficient to approve mutation resumption.
Heartbeats older than five minutes are marked stale; this is diagnostic only.
Recent heartbeats also do not prove a worker is alive. Future/missing timestamps
require review. Multiple incomplete scoped operations are reported as overlapping
ownership; the service never elects a winner.

## Restart scenarios

- Before upload dispatch: inspect prepared intent, KB reservation and quota; keep
  capacity. Even absent file/chunks at this snapshot cannot exclude a delayed write.
- During upload: missing metadata with chunks indicates possible partial storage.
  No abort, delete or retry is attempted.
- File committed but metadata unpublished, or metadata ACK lost: compare scoped
  checksums/lengths and records; report publication uncertainty and retain capacity.
- During deletion: retain the document claim, intent and quota even when file
  metadata is absent. File deletion is not equivalent to completed cleanup.
- Lost final quota-release ACK: a delete_confirmed intent plus absent document,
  application chunks, GridFS metadata/chunks, removed KB reservation and released
  quota can confirm consistent completed state at the snapshot. No retry is needed
  or authorized by this inspector.
- A historical upload after later deletion is conservatively reviewed through its
  deletion operation ID; upload records alone cannot prove completed deletion.
- Claim/reservation mutations that happened before an intent was persisted cannot
  be discovered automatically here. A missing requested intent is manual review,
  not permission to drop a KB reservation or reclaim quota.

Reports contain only a trusted operation ID, validated stage/quota categories,
heartbeat/file-state categories and fixed reason codes. They exclude owner/worker
strings, filenames, checksum values, PDF bytes, exceptions, credentials and URLs.
No logging, public recovery endpoint, quota release, remote mutation, TTL deletion,
filesystem sweep or retry implementation is present.

Remaining work: isolated real-database snapshot/index verification, bounded operator
discovery tooling, worker fencing/termination evidence, explicit reviewed recovery
plans with fresh conditional writes, and backup/restore validation. Offline doubles
do not prove Atlas compatibility or safe automated recovery.
