# Trusted parser-lease boundary (not activated)

ParserLeases is a per-application, single-job component. It does not modify the
existing document_processing/CPUWorkers/local-storage path, create clients, or
activate GridFS. Its private supervisor is the existing PDFSupervisor; private
factory injection exists only for deterministic offline tests. Request callers
supply a trusted storage materializer and primitive parser settings, never futures.

Ownership starts in the registry before materialization is submitted to bounded
StorageWorkers. The worker places its newly materialized lease in the owned job
before returning. A shielded internal task survives request cancellation, and the
request marks itself abandoned. If materialization finishes after abandonment,
cleanup runs without dispatching the parser. Submission and acquisition of the
supervisor future contain no await/cancellation handoff gap.

Only PDFSubmissionRejected establishes no dispatch. Other submission errors retain
the lease and block admission. Once dispatched, the supervisor's future is the sole
completion authority. It is marked running by PDFSupervisor and is published only
after child join, process closure and IPC cleanup. A reaped crash allows cleanup;
unconfirmed start/join or unknown completion does not. Requests cannot cancel the
native future. Direct cancellation of the internal owned task is unsupported and
retains ownership conservatively rather than deleting a possibly active input.

Cleanup runs off-loop, and registry removal/result publication follows successful
cleanup. cleanup_complete prevents another lease owner from being mistaken for
completed cleanup. Cleanup errors retain the lease and a sanitized stage. No
automatic retry, timeout-based deletion or arbitrary temp-directory sweep exists.
unresolved() exposes stages only, not paths or document contents.

Shutdown seals admission, waits for registry drain and closes the supervisor off
loop. Repeated shutdown-waiter cancellation does not abandon drain. Unresolved work
can intentionally keep shutdown pending indefinitely; forced termination is not
proof of remote failure or parser reaping. Startup must never sweep ambiguous
directories without proving ownership and absence of old readers.

Future integration must register this component in the application lifespan and
retain DOCUMENT/CPU workflow ownership until its terminal cleanup finishes. Drain
parser leases BEFORE closing storage workers/client. Do not independently run this
supervisor alongside CPUWorkers' PDF lane in production: adapt the trusted handoff
to the existing lane/admission owner in a separate tested phase. Callers must not
close or reuse a transferred lease. Durable intents, quota state, abandoned worker
results outside this boundary, migration and real GridFS restart validation remain
separate integration work. The current routes remain untouched.
