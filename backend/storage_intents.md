# Disconnected storage intents and quotas

No imports from production routes, startup hooks or GridFS adapter activate this
module. Local storage and its 10 MiB limit are unchanged. Constructor takes a
caller-owned synchronous database; run methods in owned worker threads. It does
not create a client or indexes. `ensure_indexes` is explicit optional setup only;
it delegates to the complete manifest in `storage_schema.py`. See
[storage_schema.md](storage_schema.md) for setup, read-only verification and
sanitized transaction error categories. Neither setup nor verification runs in
local-mode startup or activates GridFS.

Each `storage_operation_intents` record holds an immutable server-created identity,
schema version, expected exact bytes and SHA256, reservation reference, worker ID,
current/previous/last acknowledged stage, timestamps, error category and reconciliation
status. IDs come from trusted orchestration, not clients. The built-in unique `_id`
index rejects collisions. Duplicate creation verifies identity and payload rather
than overwriting. Heartbeats do not grant takeover authority or release capacity.

Upload stages: prepared -> write_dispatched -> file_confirmed -> published.
Before dispatch, prepared -> failed_before_dispatch is allowed. Separate deletion
intents reference the original upload reservation: prepared -> delete_dispatched ->
delete_confirmed. These are trusted attestations from future orchestration, not
verification of GridFS bytes. `last_acknowledged_stage` acknowledges the recorded
stage; dispatch does NOT assert successful remote mutation. No API here performs
storage deletion or infers success from absence.

One `storage_quota_ledger` document contains at most 20 active entries, with exact
bytes, owner, KB, document, upload operation ID and reserved/committed state. Totals
are derived from this bounded list, avoiding divergent duplicated counters. Released
entries leave the ledger; the separate durable intent keeps the released tombstone
so an old request cannot reopen its reservation. Do not remove terminal intent
records without a separately designed idempotency retention policy.

All metadata changes use explicit snapshot/majority transactions. Ledger writes
serialize competing reservations through MongoDB write-conflict detection. A losing
transaction fails closed; there is no application callback retry. Reservation +
intent state, commitment and release each commit atomically. Policy is persisted in
the ledger and mismatching policy instances are rejected. No transaction contains
GridFS IO. `_id` uniqueness is sufficient for the singleton ledger; explicit setup
provides lookup indexes for intents, with no TTL index.

Default test policy: 50 MiB globally, 10 MiB per user, 5 MiB per file, 20 documents
globally and 5 per user. Counts include both reserved and committed entries. These
are payload limits, NOT a limit on total Atlas usage, chunks, indexes or history.
Another application sharing Atlas remains outside this ledger's authority.

Quota commit requires published upload evidence. Release requires a pre-dispatch
failed upload or separately confirmed deletion with matching immutable identity,
size and checksum. An uncertain source or intent blocks both transitions. Repeated
transitions are idempotent; conflicting stages/identities fail. Database/network
errors become sanitized StorageUncertain (the existing reservation-retention error
category). Cancellation propagates; neither cancellation nor timeout triggers
release. A lost commit acknowledgement may represent a committed transaction:
inspect/reissue only the same idempotent metadata operation, never repeat file IO.

No stale-heartbeat expiry, worker takeover, automatic reconciliation, quota reset,
or orphan deletion exists. Deadlines bound client waiting, not remote completion.
The driver can perform its documented internal commit handling; this module does
not use with_transaction or retry whole application transactions.

Offline tests use a serialized transactional double with rollback and injected
lost acknowledgements. They do not establish real MongoDB snapshot/write-conflict
behavior, Atlas M0 transaction compatibility or majority durability. Those require
approved isolated integration tests. Missing transaction support must block use,
never fall back to separate unguarded increments. Further work: reconciliation,
client ownership, native-work cancellation integration, policy approval, cluster-wide
headroom checks, bounded intent retention and real GridFS verification.
