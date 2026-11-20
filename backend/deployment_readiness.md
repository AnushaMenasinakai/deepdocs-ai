# Safeguard readiness and bulk outcomes

`GET /api/health` remains process liveness. `GET /api/ready` returns only
`ready`/`not_ready` with HTTP 200/503 and no-store caching. It never reports secret
values, database documents, raw exceptions or internal schema details.

Readiness requires valid application/policy configuration, successful existing
startup index groups and MongoDB ping. The cached shared probe retains its
three-second caller deadline and two-second result cache. Optional safeguard
inspection has a two-second total deadline, reads only fixed index manifests and
one indexed lookup, and performs no repair or data scan.

- Budgets enabled: verify intrinsic unique `_id` and the exact zero-delay TTL
  `gemini_usage_expiry` index on `expires_at`. Reject sparse/partial/hidden,
  unexpected uniqueness and collation variants. Startup still attempts the existing
  budget index setup; a missing/incompatible result keeps readiness false.
- Growth enabled: verify `resource_usage` intrinsic `_id`, owner/history ordering,
  owner/document ordering and owner/KB ordering indexes. Startup creates only the
  missing resource_usage collection under a five-second bound; no records are
  inserted, old indexes replaced or quotas released.
- GridFS enabled: verify the existing full storage_schema manifest, including
  files/chunks, intent indexes and quota identity. Existing explicit operator schema
  setup remains required; readiness never creates GridFS collections. When both
  features are enabled, both manifests must pass. The upload, processing and deletion
  runtime services must also exist.
- Growth/GridFS transactions: require writable replica-set primary, logical
  sessions and sufficient wire version, then perform one snapshot-transaction
  indexed read and abort. No write probe or transaction commit is performed.

Disabled optional features skip their schema and transaction checks. Any unknown
or failed inspection fails closed. Successful subsequent inspection may recover
readiness after a transient failure without a mutation retry.

Readiness no longer calls Qdrant or Gemini. It does not promise provider availability,
unused quota, real write permissions, majority commit success, adequate memory or
crash recovery. The snapshot read verifies read-transaction support only; isolated
real-MongoDB write/concurrency/failure tests remain deployment prerequisites. Qdrant
diagnostics remain a separate explicit endpoint. Do not use provider availability
as a container liveness condition.

## Bulk operations

Bulk deletion and existing-chunk indexing/re-indexing retain earlier per-document
results when a later policy error occurs. The failed item has one of these additive
public codes:

- `budget_exhausted`: daily Gemini application budget exhausted.
- `growth_limit_reached`: growth policy rejected the operation.
- `accounting_unavailable`: policy admission/release could not be confirmed.

Later items are `not_attempted`; no automatic provider or mutation retry occurs.
Existing busy/not-found/requires-processing/service-unavailable behavior remains.
Earlier successes are not rolled back. A failed deletion may already have removed
remote data before accounting became uncertain: refresh status and follow reviewed
reconciliation procedures instead of assuming the failed item was untouched.


## Deployment integration

Set `CORS_MODE=production` with `CORS_FRONTEND_ORIGINS` containing only the exact
HTTPS frontend origins (no trailing slash). Missing origins, HTTP, wildcards and
invalid modes fail startup. Default `development` retains the existing localhost
origins. CORS does not enable credential cookies; bearer authentication is unchanged.

`/api/health/database` and `/api/health/qdrant` require the existing authenticated
account dependency, including approval/enabled checks in private mode. Anonymous
requests are rejected before database authentication lookup or diagnostic dispatch.
The dashboard sends its bearer token. Lightweight `/api/health` and internal
`/api/ready` behavior are unchanged. Authorized diagnostics still use bounded,
coalesced probes; this does not add a general request-rate policy.

For Vercel select `frontend` as the project root, Vite, `npm run build`, output
`dist`, and an HTTPS `VITE_API_URL`. `frontend/vercel.json` supplies BrowserRouter
fallback for direct navigation/refresh of `/login`, `/documents` and `/ask`.
Local contract tests verify configuration; hosted routing still requires validation.
