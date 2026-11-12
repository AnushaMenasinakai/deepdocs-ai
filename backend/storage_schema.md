# Optional storage prerequisites and transaction diagnostics

GridFS remains disabled by default. This interface does not load environment
files, create a client, activate routes, or run on application startup. The local
10 MiB upload policy is unchanged. No real database validation has been performed
by these offline checks.

## Explicit setup and verification

An operator must supply an authorized, isolated synchronous database using the
existing bounded TLS-verifying storage client. Execute in its owned worker, not
the event loop. Do not pass a production database for experimentation.

```python
from storage_schema import setup_storage_schema, verify_storage_schema

# database is an explicitly selected isolated synchronous database.
setup_storage_schema(database)    # optional DDL, outside a transaction
verify_storage_schema(database)   # read-only; raises if readiness is unconfirmed
```

`StorageIntents(database).ensure_indexes()` delegates to this complete setup.
Neither function enables GridFS. Application startup is not wired to create these
indexes: activation still requires an explicit setup and verification procedure.

Manifest (all indexes ascending):

| Collection | Required name | Keys | Unique |
| --- | --- | --- | --- |
| All five collections below | `_id_` | `_id` | Yes, intrinsic |
| storage_operation_intents | storage_intent_owner_document | owner_id, document_id, created_at | No |
| storage_operation_intents | storage_intent_reconciliation | reconciliation_status, updated_at | No |
| storage_quota_ledger | `_id_` | `_id` (singleton pdf_payload_v1) | Yes |
| pdf_blobs.files | filename_1_uploadDate_1 | filename, uploadDate | No |
| pdf_blobs.chunks | files_id_1_n_1 | files_id, n | Yes |
| document_chunks | chunks_owner_document_generation_order | owner_id, document_id, generation, chunk_index | No |

The normal application's users, knowledge bases, documents and history indexes
remain the responsibility of existing database initialization. This interface
verifies optional storage prerequisites, not the entire application schema.

Setup first inspects all existing required definitions, then creates missing
collections/indexes and verifies again. It never drops or replaces anything.
Wrong names, key ordering, uniqueness, partial/sparse/hidden/TTL indexes or custom
collation are rejected for required indexes. MongoDB's omitted intrinsic `_id`
unique flag is accepted. Collection existence alone is insufficient. Concurrent
DDL or a timeout may leave partial setup; inspect again before acting. Every call
has a five-second client deadline, which does not prove server work stopped.

## Transaction outcomes

Index creation diagnostics additionally identify the exact manifest collection
and index, an allowlisted driver category, numeric server code and command outcome.
Only explicit known server rejections are marked `command_rejected`; transport,
write-concern, deadline and unrecognized failures remain `unknown`. Rejection does
not authorize quota release, compensation or dropping an existing index.

The isolated harness performs one bounded read-only `list_indexes` inspection
after failed creation, outside setup's five-second deadline. Results distinguish
`confirmed_definition`, `incompatible_definition`, `not_observed` and `unavailable`.
Only complete successful inspection can report not_observed, and this is not proof
that an in-flight build will never finish. No mutation is retried and the original
failure still fails the job, even if an exact definition is now visible. Raw server
messages, codeName, command payloads and connection details are never printed.

The second hosted failure identifies create_index but contains neither an index
identity nor a numeric error code. Its actual root cause remains unconfirmed;
these diagnostics must precede any permissions, definition or timeout changes.

Schema failures now retain fixed `operation` and `reason` diagnostic codes.
`StorageSchemaError` inherits from OSError through the storage retention hierarchy;
it must be re-raised before wrapping generic OS/driver failures. Otherwise a real
index mismatch is replaced by a generic setup error. The smoke harness prints
only allowlisted codes, never provider messages or index/credential payloads.
Permission errors, timeouts, write-concern failures and index conflicts remain
fail-closed, with the original five-second deadline unchanged.

The initial hosted smoke failure recorded only the exception class. Its underlying
MongoDB command or mismatch cannot be reconstructed from that output. The error
wrapping defect is reproduced offline; a hosted run with these diagnostics is
still needed to identify the original setup failure. No role expansion, timeout
increase or relaxed index comparison is justified by the existing log.

`StorageTransactionError` retains `StorageUncertain` inheritance and the existing
sanitized message. Internal `category` and `phase` contain no provider text.

* `transaction_write_conflict`: code 112 during the transaction body.
* `duplicate_identity_conflict`: code 11000 during the body, including competing
  initial singleton creation. It is not evidence that a conflicting identity is safe.
* `retryable_commit_acknowledgement`: commit failure labeled RetryableWriteError.
* `unknown_commit_result`: UnknownTransactionCommitResult takes precedence; an
  unlabeled commit transport failure is also conservatively unknown.
* `pre_dispatch_failure`: the transaction body was never entered. This describes
  this metadata call only, not earlier remote writes in the workflow.
* `transaction_outcome_uncertain`: remaining driver/transport failures.

No category authorizes capacity release, remote retry or compensation. No new
application retry loop is introduced. The driver may retry commit/abort protocol
commands even with retryWrites=False; this is not transaction-body replay.
There is no with_transaction callback. Existing upload/delete uncertainty handling
and public API errors remain unchanged. Cancellation still propagates.

## Remaining validation

Offline doubles establish manifest checks and conservative exception handling,
not real MongoDB atomicity. Validate on an isolated authenticated TLS replica set:
actual index serialization and concurrent DDL; first-ledger races and write
conflicts; snapshot/majority behavior; duplicate identities; committed writes with
lost acknowledgements; primary changes; real driver commit retries; restart
inspection and quota retention. GridFS IO remains outside metadata transactions.
Worker fencing, authorized recovery, backups and deployment approval remain
separate requirements. Do not activate GridFS based on schema verification alone.
