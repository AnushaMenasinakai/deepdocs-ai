# Durable Gemini request budgets

Enable `GEMINI_USAGE_BUDGETS_ENABLED=true` for private deployment. Default false
preserves local development and existing offline fixtures. Defaults are global/user
embedding 100/30 and generation 30/10 calls per UTC day. Configure through
GEMINI_DAILY_GLOBAL_EMBEDDING, GEMINI_DAILY_USER_EMBEDDING,
GEMINI_DAILY_GLOBAL_GENERATION, GEMINI_DAILY_USER_GENERATION. Values are bounded
1..5000, with user <= global. These are request safeguards, not token/provider quota
or billing guarantees. Account sharing/multiple accounts cannot evade the global cap.

`gemini_daily_usage` has one intrinsic unique `_id` per UTC-day/kind. Its bounded
entries array holds only operation ID, owner ID and state; no prompts, documents,
credentials or provider responses. One conditional atomic push checks global count,
per-user count, policy equality and duplicate operation ID. Both limits are spent
in that single MongoDB write; no cross-document transaction or partial admission.
A second CAS marks reserved -> dispatched. IDs are server-generated per SDK call,
not caller retry keys. Reusing an ID fails closed rather than authorizing a second
provider call. Limits must match across workers; same-day policy changes fail closed.

Majority-acknowledged writes and five-second operation deadlines are used. Startup
creates the opt-in TTL index `gemini_usage_expiry` on expires_at; admission verifies
it and fails closed if missing/incompatible or DB unavailable. Retention is 32 UTC
days; TTL is housekeeping only, never a current-day refund. No scans or unbounded
per-user history. MongoDB storage and TTL behavior still need isolated real-server
validation. Driver retryable writes may retransmit an idempotent write; the provider
call itself is never automatically retried.

Every actual document batch (including re-indexing), query embedding and generation
call acquires admission just before SDK dispatch. Experimental verification, if ever
enabled independently, counts as another generation call. End-of-generator checks,
MiniLM and insufficient-context responses without generation incur no provider call.
The worker gets explicit database/owner/event-loop ownership, not a thread-global
client. Synchronous embedding workers wait on bounded asynchronous database admission;
no synchronous MongoDB IO runs on the event loop. Existing worker slots remain owned.

A failed provider response or timeout after dispatch remains consumed. A known
cancellation before admission spends nothing; a confirmed reserved-only cancellation
can remove that exact reserved entry. Lost acknowledgements, cancellation during an
await, release failure, or a crash between dispatch CAS and SDK call retain capacity.
Thus accounting conservatively upper-bounds attempts; it cannot exactly measure calls
whose dispatch acknowledgement is lost. Midnight-crossing queued dispatch fails closed.
No automatic repair, refund or provider replay is authorized by stale entries.

Exhaustion returns sanitized HTTP 429; accounting failure returns 503, distinct from
insufficient evidence. Indexing exhaustion can occur after earlier batches persisted;
existing generation-publication/error handling remains authoritative. Avoid repeatedly
re-indexing to work around exhaustion. A request budget does not bound tokens or total
history/vector storage. The feature does not activate GridFS or alter embedding models.

Offline doubles cover admission contracts, failures and provider dispatch integration.
The isolated MongoDB budget and combined-policy validations were previously reported
as passing; the combined run used commit 50ad04c. Those historical results were not
independently reverified during this documentation preparation. The harness exercises
real database accounting and separate-client contention, but simulates lost
acknowledgements after commits. It does not establish real network-partition, replica
failover, actual TTL-expiration or production Atlas behavior. Implementation and
reported isolated validation do not authorize deployment or enable these budgets.
