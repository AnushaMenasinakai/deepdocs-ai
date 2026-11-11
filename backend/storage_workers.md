# Optional storage worker ownership

This module is disconnected: main.py, routes and the active local storage dependency
do not import or invoke it. The disabled lifecycle does not construct any client.
An enabled lifespan owns exactly one synchronous client and one bounded executor.
The default permits one running operation and one queued operation. Admission and
registration occur on the owner event loop before submission without an await.
Unexpected submission failures seal admission and retain unresolved ownership.

Callables must open, use and close streams in their worker. Return only completed
values or an explicitly owned materialization, never a live stream. Request
cancellation does not cancel submitted work, including queued work; it does not
change intents, mark remote failure, release quota or retry mutations. Callers must
persist intents before dispatch and attach their workflow ownership before calling
run. Worker exceptions propagate; adapters remain responsible for sanitized errors.
Abandoned exceptions are observed without logging their contents.

Shutdown seals admission, waits for all registered native futures, joins the
executor off-loop, then closes the client off-loop. Repeated cancellation of the
shutdown waiter does not abandon cleanup. `unresolved()` reports a safe count for
future shutdown diagnostics. Stuck work or ambiguous submission intentionally keeps
shutdown pending and the client open. Forced process termination proves nothing
about remote outcomes; durable intents must be reconciled after restart.

No credentials are logged, no settings are read, and no clients are passed to PDF
children. TLS validation is mandatory; connection/socket/selection/pool waits and
client operation timeouts are bounded. No application mutation retries are added.

Remaining work: connect to admission-lifecycle ownership and shutdown diagnostics;
handle abandoned materialization results with explicit cleanup; attach parser leases;
bind durable operation updates to native completion; validate real client behavior
on an isolated MongoDB instance. Generic callables cannot enforce stream confinement
or prove that callers persisted intents. This foundation is not production-ready.
