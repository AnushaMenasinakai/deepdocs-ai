# DeepDocs AI

DeepDocs AI is a document intelligence application with a React frontend and FastAPI backend. Users register and sign in, manage private knowledge bases, upload and process PDFs, index their text with MiniLM embeddings in Qdrant, and ask questions with validated source citations and persistent question history.

MongoDB stores users, document metadata, chunks and history. PDFs live in local storage. CPU embedding work uses a bounded dedicated worker, PDF extraction uses a supervised spawned process, and expensive workflows have non-waiting admission limits. Authentication uses Argon2id passwords and signed JWTs with owner-scoped access.

## Prerequisites and setup

Run commands from your checkout root unless a command explicitly changes directory. The verified Python version is 64-bit Python 3.13.15. Node.js 24.11.1 was used for acceptance; installed Vite 8.3.0 requires Node 20.19+ in the 20.x line or Node 22.12+. Use npm with the committed frontend lockfile.

Prepare a MongoDB database with credentials permitted to create the application's indexes and read/write its collections. MongoDB connections require TLS with valid certificate and hostname verification, including for local instances. A private CA can be supplied through the URI's tlsCAFile option. Isolated MongoDB 8.2.6 was verified locally. Use a dedicated database for demonstrations.

Prepare a Qdrant server or Cloud instance and dedicated collection name. The installed client is pinned to 1.19.1. The application uses a URL-based client; the embedded client used in integration verification is test-only injection. Configure an API key where required. A live demo also needs a Gemini API key with access to the configured model and available quota; requests may incur provider charges.

Create a virtual environment only for a fresh checkout. Reuse a valid existing environment.

Windows PowerShell:

```powershell
python --version
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
npm.cmd --prefix frontend ci
if (!(Test-Path backend/.env)) { Copy-Item backend/.env.example backend/.env }
if (!(Test-Path frontend/.env)) { Copy-Item frontend/.env.example frontend/.env }
```

macOS/Linux shell:

```sh
python3.13 --version
python3.13 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
cp -n backend/.env.example backend/.env
cp -n frontend/.env.example frontend/.env
```

Copy environment examples only when the destination does not exist. Preserve existing private configuration. requirements-dev.txt includes runtime requirements and pytest; runtime-only installations can use backend/requirements.txt.

## Configuration

Edit the untracked backend/.env locally. Process environment variables take precedence over that file.

| Setting | Purpose |
| --- | --- |
| MONGODB_URI | Your TLS-enabled MongoDB URI and dedicated database credentials |
| MONGODB_DB_NAME | Dedicated application database |
| JWT_SECRET_KEY | Cryptographically random secret of at least 32 bytes; replace the rejected example placeholder |
| QDRANT_URL | Server origin, HTTPS for remote services or HTTP on loopback |
| QDRANT_API_KEY | Private instance/Cloud key where required |
| QDRANT_COLLECTION_NAME | Dedicated vector collection, default deepdocs_chunks |
| GEMINI_API_KEY | Private generation API key |
| GEMINI_MODEL | Configured provider model, default gemini-3.1-flash-lite; verify account access before a live demo |
| EMBEDDING_MODEL_NAME | Default sentence-transformers/all-MiniLM-L6-v2, 384 dimensions |
| CORS_FRONTEND_ORIGINS | Optional comma-separated explicit additional frontend origins |

The examples document upload, PDF, JSON-body, authentication and expensive-operation limits. Their defaults are validated. Admission is process-local and active on the integrated routes. Use the existing single-worker deployment configuration.

MiniLM loads lazily from backend/.cache/embeddings. First use can download weights if the cache is absent. Prepare the cache before an offline demo, then set HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 in the backend process environment. A cache miss will fail instead of downloading. The Dockerfile packages a pinned model snapshot and enables offline loading.

In frontend/.env, set VITE_API_URL=http://127.0.0.1:8000 for local development. Every production build requires an explicit API origin. Remote deployment origins require HTTPS. Omit the /api suffix, credentials, query strings and fragments. VITE_ values are public build-time data; keep secrets in the backend only. Rebuild after changing the API origin.

CORS allows http://127.0.0.1:5173 and http://localhost:5173 by default. For another port, add its exact origin to CORS_FRONTEND_ORIGINS and restart the backend. Retry-After is exposed for browser error handling. Configure allowed origins, not wildcards.

## Start the application

Start your configured MongoDB and Qdrant services first. Their data directories and credentials belong to your chosen environment. Compose runs only the backend and expects those services to be reachable separately.

Backend on Windows, from the checkout root:

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

On macOS/Linux, use backend/.venv/bin/python with the same arguments. The direct-access command disables forwarded-header rewriting. A future reverse proxy needs a separately verified trust policy.

Frontend in a second terminal:

```sh
npm --prefix frontend run dev
```

PowerShell can use npm.cmd. Open http://127.0.0.1:5173. The API reference is at http://127.0.0.1:8000/docs and its schema at /openapi.json. GET /api/health is basic liveness; /api/health/database and /api/health/qdrant report dependency connectivity. /api/ready provides the application's readiness checks. Liveness alone does not establish readiness for ingestion or generation.

Startup initializes users, knowledge-base, document, chunk and history indexes when MongoDB is available. Registration requires confirmed unique-user-index initialization. Logs sanitize initialization failures. PDF storage defaults to backend/storage/documents; preserve it together with MongoDB metadata and Qdrant data. compose.yaml mounts persistent PDF storage and a read-only backend environment file, runs nonroot, and uses one worker. Avoid deleting persistent volumes during cleanup.

## Portfolio demonstration

1. Start the configured services, backend and frontend. Confirm dependency health, model availability and Gemini access before presenting.
2. Register a synthetic account and log in.
3. Create a knowledge base and select it in Documents.
4. Upload a small text-based PDF with a distinctive factual statement. Uploaded confirms storage only. Scanned/image-only documents need OCR, which is outside the implemented scope.
5. Use the document action menu's Process PDF action and wait for Processed. Then select the processed document and use the Re-index selected action, confirm it, and wait for Indexed. Upload does not automatically process or index a document. Indexed reflects the last confirmed synchronization; inspect status if an operation is uncertain.
6. Open Ask DeepDocs, select the same knowledge base and ask a question answered by the PDF. Retrieval uses indexed active chunks and relevance gates; insufficient evidence produces an explicit abstention.
7. Review the answer's citation references, source filename and page metadata. Citations validate provenance, while factual correctness still deserves review.
8. View the saved question and answer in history. History is persistent Q&A snapshots, not multi-turn conversational memory supplied to generation.
9. Delete the document with confirmation. Successful deletion removes its PDF, chunks, vectors and metadata. Existing history remains a snapshot until separately deleted. Refresh the document list to confirm removal.

Busy admission returns a generic 503 with retry guidance. Authentication throttling returns 429 and oversized input can return 413. Mutations are not automatically retried. After an ambiguous timeout or interruption, refresh or inspect state before attempting another operation.

## Testing and build

Install requirements-dev.txt and run from the checkout root with the virtual-environment Python:

```powershell
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -q -p no:cacheprovider
node --test frontend/tests/reliability.test.mjs frontend/tests/api-configuration.test.mjs
```

Use backend/.venv/bin/python on macOS/Linux. Automated backend tests use mocked service dependencies and synthetic fixtures; tests include spawned workers and loopback HTTP checks. They are distinct from live-service acceptance. Evaluation procedures are documented in backend/evaluation/README.md.

The 19 browser suites are standalone Node scripts using the pinned playwright@1.64.1 package and an installed Microsoft Edge browser (channel msedge), headless. They mock API requests. Start only Vite for this acceptance run:

```sh
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

In a second terminal, run sequentially with no automatic retries. PowerShell:

```powershell
$env:FRONTEND_TEST_URL = 'http://127.0.0.1:5176'
$env:FRONTEND_TEST_ARTIFACT_DIR = Join-Path ([IO.Path]::GetTempPath()) ('deepdocs-browser-' + [guid]::NewGuid())
Get-ChildItem frontend/tests/*.browser.cjs | Sort-Object Name | ForEach-Object {
    node $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "Browser suite failed: $($_.Name)" }
}
```

macOS/Linux with Microsoft Edge installed:

```sh
export FRONTEND_TEST_URL=http://127.0.0.1:5176
export FRONTEND_TEST_ARTIFACT_DIR="$(mktemp -d)"
for suite in frontend/tests/*.browser.cjs; do node "$suite" || exit 1; done
```

The verified browser platform is Windows/Edge. Other platforms/browsers require separate verification. Each script manages its browser contexts and closes the browser. Stop Vite with Ctrl+C afterward. Screenshots go to FRONTEND_TEST_ARTIFACT_DIR or an OS temporary directory by default. Preserve failure console output when diagnosing a run. Keep Vite's normal dependency cache location because component harnesses inspect /node_modules/ dependency URLs. Run one suite at a time on low-memory machines.

For a production build, configure frontend/.env first, then:

```sh
npm --prefix frontend run build
```

Output is frontend/dist. The API URL is embedded at build time. A deployed frontend also needs backend CORS configured for its origin.

### Latest verification evidence

- Phase 5D-C7 passed all 19 Playwright suites and 524 browser scenarios, plus 35 frontend Node tests and the production build. Four initial component-harness failures caused by a custom Vite cache path passed unchanged after restoring normal cache configuration.
- Phase 5D-C6 passed real registration/login, KB creation, PDF upload/extraction/chunking, offline 384-dimensional MiniLM inference, embedded Qdrant storage/retrieval, citations, history and deletion cleanup. It used actual FastAPI routes through an in-process ASGI client, isolated TLS MongoDB 8.2.6 and deterministic mocked Gemini generation.
- Phase 5D-C5 independently verified offline MiniLM inference and embedded Qdrant insertion/search/deletion. These reports were generated in isolated temporary workspaces and are not bundled as a reusable production integration runner.

### Known limitations

Live Gemini generation was not verified in the final integration journey. Embedded Qdrant does not verify server HTTP, authentication or payload-index behavior. Frontend mocked-API acceptance and backend real-dependency integration ran separately; a browser connected to those real dependencies was not part of final acceptance.

Memory requirements depend on document size, model, workload and concurrent services. The small backend journey observed about 579 MiB peak backend working set and 155 MiB MongoDB working set; these are observations, not minimum system specifications or load-test guarantees. Concurrency limits apply per process. Interrupted cross-service operations may retain claims or partial state and require manual review; no automatic retry, claim unlocking or crash recovery is provided. Shutdown can wait for native work that has not finished.

## Architecture and implementation reference

The phase labels below preserve the project's implementation history and detailed API/lifecycle rationale. Use the current setup, demo and verification sections above for running the completed application. Historical benchmark results describe their recorded datasets and do not establish live-provider behavior.

## MongoDB configuration (Phase 2A)

Install the updated requirements into the existing virtual environment using the setup command above, in a terminal that can execute the project Python. Do not recreate the environment.

Create a local, untracked `backend/.env` based on `backend/.env.example`, and privately set `MONGODB_URI` to your MongoDB Atlas connection string. No working URI is supplied by this project. `MONGODB_DB_NAME` defaults to `deepdocs_ai`; the example sets that name explicitly. Use an Atlas database account with the permissions needed for that database and allow your machine's network address in Atlas. Never put credentials in tracked files.

Configuration reads `backend/.env` relative to the module, independent of the shell's working directory. Process environment variables override file values. Empty or missing `MONGODB_URI` raises a configuration error; an explicitly blank database name is invalid. Restart the backend after changing configuration. URI values are excluded from the settings representation, and driver errors are not returned or logged.

One official PyMongo `AsyncMongoClient` is created per FastAPI application lifespan (per worker), with TLS certificate and hostname verification enabled. It selects the configured database and attempts a bounded ping on startup. Requests reuse this client, which is awaited closed in lifespan cleanup. Health checks have a five-second deadline; connection, socket and server-selection timeouts are also configured. Startup also initializes the application indexes described above. A ping alone does not verify collection permissions.

`GET /api/health` remains a liveness check and preserves its original 200 response, independently of MongoDB readiness. Configuration errors are logged as safe messages, while the application stays available. A database health request returns:

- **200:** `{"status":"ok","message":"MongoDB is reachable"}`
- **503, missing URI:** `{"detail":"MONGODB_URI is required. Set it in the environment or backend/.env."}`
- **503, connection failure:** `{"detail":"MongoDB is unavailable. Check configuration and Atlas connectivity."}`

The separate endpoint is `GET /api/health/database`. Concurrent diagnostic callers share a bounded in-flight probe; a failed startup connection can recover through the same client without restarting. Invalid configuration requires fixing the settings and restarting. Ping verifies reachability, not collection read/write permissions. Future backend routes can obtain the shared database through `Depends(get_database)` from `database.py`.

After starting the backend using the existing command above, verify locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/health/database
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

The unittest suite uses mocked clients and does not connect to Atlas. It covers missing settings, secret-safe errors, unchanged liveness, shared-client reuse, shutdown, and connection recovery.

The final isolated MongoDB and backend integration results are recorded in the verification section above.

## Registration API (Phase 2B)

Install the updated requirements into the existing virtual environment from a terminal where project Python is accessible. Start the backend with the existing Uvicorn command. The frontend includes registration, login, protected routes and logout.

`POST /api/auth/register` accepts a JSON object with required `name`, `email`, and `password` fields:

- Name is trimmed and must contain 1–100 characters afterward.
- Email is trimmed, validated by Pydantic/email-validator, and stored lowercase (maximum 254 characters). No provider-specific dot removal or plus-address rewriting is performed.
- Password must contain 8–128 characters. It is not trimmed or subjected to character-composition rules. Extra request fields are rejected.
- Passwords are hashed with Argon2id using argon2-cffi (64 MiB, three iterations, four lanes, random salts). Hashing runs in a worker thread, not the event loop.

The `deepdocs_ai.users` documents contain `_id` (ObjectId), `name`, normalized `email`, `password_hash`, and a timezone-aware UTC `created_at`. MongoDB stores datetime values as UTC BSON dates. Plaintext passwords are never included in insert documents. The response model explicitly exposes only `id` (string), `name`, `email`, and `created_at`.

Startup creates/confirms the named unique index `users_email_unique` on `email`, once per application lifespan. Concurrent duplicate inserts are enforced by MongoDB, not a find-then-insert pre-check. If the initial database connection or index creation fails, registration remains unavailable until a successful restart. Existing data/indexes are not automatically repaired or deleted. The existing health endpoints keep their response contracts; database reachability alone does not mean registration initialization succeeded.

Registration responses:

- **201:** Public user fields, with no password, password hash, session, or token.
- **422:** Invalid input. Validation errors omit raw input and context to avoid echoing submitted passwords.
- **409:** A duplicate key conflicts with an existing account.
- **503:** Database unavailable or unique-index initialization incomplete.
- **500:** Password hashing could not complete, with a generic message.

CORS permits the methods used by the completed frontend and exposes Retry-After for admission handling.

Offline verification from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

The suite patches configuration and MongoDB clients, never loads the real `backend/.env`, and never modifies Atlas. Registration tests cover input validation, normalization, safe responses, duplicate-key handling (including simulated concurrency), unavailable database/index, lifecycle index setup, and real local Argon2id hashing. Mocked concurrency is not a substitute for manual Atlas index verification.

Earlier implementation-time execution restrictions were superseded by offline regression and isolated integration verification.

## Login and current user (Phase 2C)

Set `JWT_SECRET_KEY` privately in the runtime environment or local `backend/.env` to a cryptographically random secret of at least 32 bytes. The example contains a rejected placeholder, not a usable key. `JWT_ALGORITHM` defaults to and permits only `HS256`; `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30 (allowed range 1–1440). Environment variables override file settings. JWT configuration is validated on authentication use; invalid configuration returns a safe 503 and does not disable registration or health checks.

- `POST /api/auth/login`: JSON `email` and `password`, with the same normalization/validation as registration. Returns `access_token` and `token_type: "bearer"`. Unknown accounts, wrong passwords, and corrupt stored hashes share a generic 401; invalid request structure/format returns 422.
- `GET /api/auth/me`: requires `Authorization: Bearer <access_token>`. Returns only `id`, `name`, `email`, and `created_at`. Invalid, expired, or deleted-user tokens return 401 with `WWW-Authenticate: Bearer`; database failures return safe 503 responses.

Tokens contain only `sub` (MongoDB user ID), `iat`, and `exp`. They are signed, not encrypted. Frontend authentication and logout are implemented; refresh tokens are outside the current scope. Install the new dependency and run offline tests locally:

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -v
```

## Knowledge Base API (Phase 3A)

All five endpoints require `Authorization: Bearer <access_token>` and operate only on the authenticated user's resources:

| Method | Endpoint | Success |
| --- | --- | --- |
| POST | /api/knowledge-bases | 201: created Knowledge Base |
| GET | /api/knowledge-bases | 200: array ordered by updated_at descending, then ID descending |
| GET | /api/knowledge-bases/{id} | 200: one Knowledge Base |
| PATCH | /api/knowledge-bases/{id} | 200: updated Knowledge Base |
| DELETE | /api/knowledge-bases/{id} | 204: no content |

Create accepts `name` (trimmed, 1–100 characters) and optional `description` (trimmed, maximum 500 characters). Missing, null, and blank descriptions normalize to null. PATCH accepts at least one of these fields; null names and all unknown/server-owned fields are rejected. Responses contain only `id`, `name`, `description`, `created_at`, and `updated_at`. Timestamps are server-controlled UTC values.

Malformed IDs/input return 422; missing and other users' resources share the same 404. Database failures return sanitized 503 responses. Ownership comes exclusively from the existing authentication dependency, and every individual read/update/delete filters by both resource ID and owner ID.

Startup creates the `knowledge_bases_owner_updated` index on owner_id ascending, updated_at descending, and _id descending. Failure is logged without driver details and retried on restart; this performance index is not required for ownership enforcement. Document workflows implement scoped cleanup; delete a knowledge base only after its documents are removed.

Run the complete offline backend suite with `.ackend.venvScriptspython.exe -m pytest backend/tests -v`. Tests use isolated in-memory/mocked database operations, never production Atlas.

## PDF upload and document metadata (Phase 3C)

Install the updated backend requirements using the existing setup command; this phase adds only python-multipart==0.0.32. All document endpoints require Bearer authentication:

| Method | Endpoint | Success |
| --- | --- | --- |
| POST | /api/knowledge-bases/{knowledge_base_id}/documents | 201: metadata; multipart field named file |
| GET | /api/knowledge-bases/{knowledge_base_id}/documents | 200: metadata array, newest first |
| GET | /api/documents/{document_id} | 200: metadata only |
| DELETE | /api/documents/{document_id} | 204: file and metadata removed |

Uploads/listing require an owned Knowledge Base. Individual document operations filter by document ID and owner ID. Missing and other users' resources share 404 responses.

Original PDFs are stored only in the ignored backend/storage/documents/ directory, under generated ObjectId filenames. MongoDB stores metadata, not PDF bytes. API responses expose only id, knowledge_base_id, filename, content_type, file_size, status, created_at, and updated_at; initial status is uploaded. The displayed filename is the client filename's basename, never a storage path. The local directory is not mounted for public download. Back up both metadata and local files together; deployments need durable shared storage before using multiple hosts.

DOCUMENT_MAX_UPLOAD_BYTES defaults to **10485760 (10 MiB)** and accepts 1–104857600 bytes through backend environment configuration. Uploads must be non-empty, have a .pdf extension, declare application/pdf, and begin with %PDF-. This is signature validation, not full PDF validation or malware scanning. The multipart stream is capped at the file limit plus 64 KiB for MIME overhead, with one file and no extra fields. File copying uses bounded chunks and checks the exact file size. Invalid input returns 415/422, excessive size 413, and storage/database failures sanitized 503 responses.

Knowledge Base deletion returns **409** until its owned documents are deleted. Internal document-ID reservations and a revision guard prevent concurrent uploads from being orphaned by deletion; these fields are never client-controlled or public. The documents index covers owner, Knowledge Base, creation time descending, and ID descending.

If metadata insertion fails, upload cleanup removes the newly written file. Deletion removes the file first; a file cleanup failure keeps metadata for retry. A missing file is treated as already removed when retrying deletion. Storage references must match the document's generated name and remain within the storage root; unsafe references are rejected without deleting anything.

MongoDB and the local filesystem are not one transaction. A process crash, uncertain database acknowledgement, or cleanup failure can require manual reconciliation of files, metadata, and internal Knowledge Base reservations (_document_ids, _document_revision). Stale reservations block Knowledge Base deletion rather than silently orphaning uploads. Do not clear reservations while uploads are active. There is no automatic cascade or destructive repair.

Offline tests use temporary directories under ignored .verification/ (created by the suite when absent) and mocked MongoDB; they do not connect to Atlas. Extraction, chunking, indexing, retrieval, RAG and their frontend workflows are implemented.

## Document library UI (Phase 3D)

The protected Documents page now lists the selected Knowledge Base's PDFs and supports single-file upload and confirmed deletion. It automatically selects the first available Knowledge Base and links to Knowledge Bases when none exist. The UI checks file extension, available MIME type, non-empty content, and the 10 MiB limit; backend validation remains authoritative. Status "Uploaded" confirms storage only. Processing and indexing are explicit follow-up actions before retrieval and RAG.

## PDF processing pipeline (Phase 4)

Processing is available through the Documents UI and the API:

- POST /api/documents/{document_id}/process processes an owned stored PDF and returns document metadata including page_count, chunk_count, processed_at, and a safe processing_error.
- GET /api/documents/{document_id}/chunks returns the owned document's active chunk set in chunk_index order.

Both endpoints require Bearer authentication. Cross-user resources return 404. Invalid IDs return 422; concurrent process/delete operations return 409; parser/no-text failures return 422; unavailable storage/database services return safe 503 responses.

Install pymupdf==1.28.2 using backend/requirements.txt in the existing environment. PyMuPDF extracts text one page at a time in sorted reading order; human-facing page numbers begin at 1. Empty pages count toward page_count but create no chunks. Image-only/no-text and password-protected or unreadable PDFs fail safely. No OCR is implemented.

Normalization only standardizes newlines and horizontal whitespace, reduces excess blank lines, and trims whitespace. It preserves line/list structure, punctuation, and numbers; it does not join ambiguous wrapped lines or rewrite text. Chunks stay within one page, prefer paragraph then sentence then whitespace boundaries, and overlap at whole-word boundaries. Defaults: PDF_CHUNK_TARGET=1000 and PDF_CHUNK_OVERLAP=150 characters. Small tails can extend a chunk up to target + overlap; an indivisible long word may exceed the target rather than being cut. Indices begin at 0 across the document. Character counts describe cleaned chunk text, not byte offsets in the original PDF.

The document_chunks collection stores ObjectId document/Knowledge Base/owner references, source filename, text, chunk order, page_start/page_end, character_count, and UTC created_at. A private generation identifier separates staged and active data. Neither owner IDs nor generation/storage internals are exposed by the chunk API. A compound index supports owner + document + generation + chunk order.

Lifecycle: uploaded → processing → processed, or failed. Reprocessing preserves the last successful generation and its counts/timestamp until all replacement chunks have been inserted and a single metadata update activates the replacement. Failed extraction/insertion marks the attempt failed and preserves the old active chunks; failed-stage chunks are cleaned up where possible. Inspection returns only the active generation, including the last successful one after a failed reprocessing attempt. Deleting a document removes all its owned chunk generations and its PDF. Knowledge Base deletion still requires deleting documents first.

No cross-collection transaction or background queue is used. An uncertain activation acknowledgement preserves generations and the operation lock for manual reconciliation; metadata may remain processing until reviewed. Cleanup failures can leave inactive generations, which inspection never returns. Process termination can leave a stale operation lock. Reconcile only when no operation is running; do not blindly delete the active generation. An interrupted deletion can leave retained claims and partial cleanup requiring manual review.

Native PyMuPDF extraction runs in a supervised spawned process with bounded CPU/PDF admission and completion ownership. Configurable limits are PDF_MAX_PAGES=1000, PDF_MAX_TEXT_CHARACTERS=5000000, and PDF_MAX_CHUNKS=20000. They bound accepted output but are not a hard memory/time sandbox for native PDF decompression.

The frontend supports processing, indexing, inspection, Ask, citation rendering and history.


## Embeddings foundation (Phase 5)

POST /api/documents/{document_id}/embeddings requires Bearer authentication and ownership. It accepts no model/path/body parameters. The document must be processed with a nonempty, complete active chunk generation. It returns document_id, chunk_count, embedding_model, embedding_dimension, and status (generated), never full vectors or internal ownership/storage fields. Missing/cross-user documents return 404, malformed IDs return 422, unprocessed/empty documents and concurrent operations return 409, and configuration/model/vector/database failures return sanitized 503 responses.

The dependency is sentence-transformers==6.1.0. A pip binary-only dry run resolved it together with the existing backend requirements on Windows Python 3.13.15, including compatible PyTorch wheels; this verifies dependency resolution, not real inference. Install the updated backend/requirements.txt locally before live verification. Unit tests use a fake model and never download model weights or contact Atlas.

The default model is sentence-transformers/all-MiniLM-L6-v2 (384 dimensions). EMBEDDING_MODEL_NAME configures a server-controlled Hugging Face repository ID; EMBEDDING_BATCH_SIZE defaults to 32 (allowed 1–128). Dimensionality is read from the model rather than hardcoded. The model is lazily imported/loaded on the first embedding request, reused per process, CPU-only, with remote custom code disabled. Changing the configured model replaces the single cached model. Downloads go to ignored backend/.cache/embeddings; first use needs network access and sufficient memory/disk space. Normal startup does not import Sentence Transformers or load weights.

Embedding chunk texts in chunk_index order preserves their existing document/page provenance for later storage. Each bounded batch produces normalized dense vectors; every vector must be nonempty, have the model's exact dimension, and contain only finite real numbers (no strings, booleans, NaN, or infinity). Batch output counts must match inputs. Validated batches are written to Qdrant; full vectors are not stored in MongoDB or returned by the document API. The model's tokenizer length limit still applies (the default MiniLM model truncates inputs beyond 256 word pieces); character-based Phase 4 chunks do not guarantee a token limit. Stored text and page provenance remain unchanged.

A single documents.embedding object records the outcome for all chunks of its chunk_generation: status, model, dimension, chunk_count, embedded_at (UTC), and a safe error. Missing metadata means not_generated. The embedding subdocument records generation; vector_index metadata separately records confirmed Qdrant synchronization. States are not_generated, generating, generated, and failed. No per-chunk writes or extra MongoDB records/indexes are needed: a chunk inherits success only when its generation matches that complete document-level result. Repeated calls regenerate and replace this metadata; they never duplicate chunks. Starting Phase 4 reprocessing resets embedding metadata, including when that reprocessing later fails.

Embedding generation shares the existing atomic document-operation claim with processing/deletion. Each batch is validated before its Qdrant write; guarded final publication follows vector count verification. Confirmed local failures may publish a safe failed state. Uncertain mutations retain claims and may leave partial vectors for review. Document deletion naturally removes this metadata with the document and still removes owned chunks/PDFs. The Knowledge Base deletion policy is unchanged.

Model loading and encoding run on a bounded dedicated thread. Sessions retain capacity across batches and native completion. Uncertain mutation acknowledgements can retain claims and require inspection; no automatic replay or unlocking is provided. Offline real inference and embedded-Qdrant indexing passed the final isolated journey.

Qdrant integration, semantic retrieval, Gemini generation and the Ask UI are implemented. Reranking remains evaluation-only.


## Qdrant vector persistence (Phase 6)

Phase 6 extends the Phase 5 endpoint; it replaces the earlier in-memory-only outcome. Install backend/requirements.txt (adds qdrant-client==1.19.1). A binary-only pip dry run verified compatibility with Python 3.13.15, sentence-transformers==6.1.0, and the other pinned backend dependencies. The async official client is created lazily, reused per application process, and closed at shutdown. No Qdrant connection or model download is required at normal startup.

Set QDRANT_URL and, for Cloud, QDRANT_API_KEY privately in backend/.env or the runtime environment. QDRANT_COLLECTION_NAME defaults to deepdocs_chunks. HTTPS Cloud URLs and HTTP localhost/127.0.0.1/IPv6 loopback are supported; embedded credentials, query parameters, fragments, and URL paths are rejected. Real configuration is never returned/logged. Restart the backend after changing Qdrant configuration. The example contains no usable credential.

The collection uses one unnamed dense vector, cosine distance, and the dimension supplied by the embedding provider (384 for all-MiniLM-L6-v2). Missing collections are created; existing dimensions/distance/named-vector mismatches return safe 503 without deleting or recreating the collection. Keyword payload indexes cover owner_id, knowledge_base_id, document_id, and chunk_generation for Cloud-compatible filtered cleanup/counting.

Each point has a deterministic UUIDv5 derived from owner, Knowledge Base, document, chunk generation, and chunk ID. Payload fields are owner_id, knowledge_base_id, document_id, chunk_id, chunk_generation (all strings), chunk_index, source_filename, page_start, page_end, and text. Text is included in retrieval payloads; retrieved hits are checked against authoritative MongoDB chunks. No paths, credentials, authentication data, or full environment configuration enters payloads. MongoDB remains authoritative for application/chunk lifecycle; Qdrant stores vectors and retrieval payloads, not application ownership policy.

POST /api/documents/{document_id}/embeddings requires JWT/ownership and a successfully processed, nonempty active chunk set. It claims the existing operation lock, marks indexing in MongoDB, generates/validates ordered batches, checks collection compatibility, removes previous owned document points, upserts deterministic IDs with wait=True, and checks exact total/current-generation point counts before publishing success. The response preserves status=generated and adds vector_status=indexed, vector_store=qdrant, and collection_name. Full vectors never enter MongoDB or API responses.

The documents.vector_index object tracks not_generated/indexing/indexed/failed/stale, model, dimension, active chunk generation, count, collection, and indexed_at. A private endpoint fingerprint prevents accidental cleanup against a different Qdrant deployment. Repeated indexing replaces owned document points rather than accumulating generations. Changing collection on the same endpoint cleans the old recorded collection first; moving endpoints requires restoring the old configuration for cleanup before migration. The lightweight Phase 5 embedding metadata remains, but success is now published only after persistence verification.

Reprocessing marks vector state stale immediately. After replacement chunks have been built/staged, it deletes all old document points before activating the new chunk generation. Cleanup failure returns 503, preserves the last active chunk set, records failed processing/stale vector state, and keeps the cleanup location for retry. Successful reprocessing leaves no old points and resets vector state to not_generated. Failed extraction can retain old physical vectors, but they are explicitly stale and must never be treated as current.

Document deletion marks vector state stale and confirms scoped Qdrant deletion before removing the PDF, chunks, reservation, and MongoDB document through the existing retryable deletion flow. If Qdrant is unavailable, documents with recorded vector state remain in MongoDB with their files/chunks for retry; documents never sent to Qdrant can still be deleted. Every count/delete filter includes authenticated owner, document, and Knowledge Base. The Knowledge Base nonempty conflict rule is unchanged.

GET /api/health/qdrant is a lightweight connectivity check returning status=ok or a sanitized 503; it does not load the embedding model or promise collection compatibility. GET /api/documents/{document_id}/vector-status requires JWT/ownership, uses the document-operation lock, and returns document_id, status, collection_name, expected_chunk_count, stored_vector_count, and synchronized. Synchronization requires indexed metadata for the active generation plus matching current and total counts. It returns neither payload text nor vectors and performs no similarity search.

There is no distributed transaction across MongoDB, Qdrant, and files. Partial/uncertain upserts can leave points with failed/indexing MongoDB state; retry, reprocess, or deletion cleans them using the retained location. A lost final MongoDB acknowledgement can return 503 despite valid indexed state. Crashes, persistent MongoDB failures, or delayed remote operations require reconciliation while no operations are running; do not blindly clear operation locks. Exact counts and completed write acknowledgements are verification, not a distributed atomicity guarantee or a point-by-point content audit. Future retrieval must enforce ownership and MongoDB active-generation/indexed-state eligibility rather than treating every raw Qdrant point as usable. The chosen retry strategy does not preserve continuous availability of an older vector set.

Tests fake the Qdrant client and embedding provider; an additional official-client in-memory test checks IDs/upsert/count/delete without a server. Real Cloud credentials/connectivity, real-model indexing into Cloud, reprocessing cleanup, and deletion still require manual live verification. The completed frontend and subsequent API sections include semantic search, query embeddings, RAG and persistent Q&A history.


## Semantic chunk retrieval (Phase 7)

POST /api/knowledge-bases/{knowledge_base_id}/search requires Bearer authentication and ownership of the Knowledge Base. Swagger exposes this endpoint directly; no debug endpoint or frontend search UI is needed. Send a JSON object containing query and optional top_k. Query must be a string, trimmed and nonempty, with at most 1000 characters after trimming. top_k defaults to 5 and must be an integer from 1 to 20; booleans, strings, fractional values, unknown fields, owner IDs, and arbitrary filters are rejected with 422.

The route validates MongoDB Knowledge Base ownership before retrieval; missing and other users' Knowledge Bases return the same 404. A dedicated retrieval service selects processed/indexed documents with no active operation and matching model, dimension, collection, endpoint, and chunk generation. It reuses the existing embedding provider and cached Sentence Transformer model (default all-MiniLM-L6-v2, 384 dimensions) to encode the normalized query once, then validates the vector. Query embeddings and queries are not persisted or logged. No processing/indexing is triggered.

VectorStore.search uses the pinned official client's query_points API with an explicit numeric vector, cosine collection validation, with_vectors=False, and limit=top_k. Its server-controlled Qdrant filter requires both owner_id and knowledge_base_id, plus an OR of eligible document_id/chunk_generation pairs. Filtering is enforced in Qdrant, not merely applied after retrieving other users' points. Search never creates collections, payload indexes, points, operation locks, or MongoDB records. A missing collection or no eligible documents/points returns 200 with an empty results array. Documents with obsolete model/configuration metadata are not searched and need explicit re-indexing.

The response contains query and results. Each result exposes rank (starting at 1), finite numeric score, document_id, chunk_id, chunk_index, text, source_filename, page_start, and page_end. Results are sorted by descending cosine retrieval score, with deterministic tie ordering. Scores are similarity values, not accuracy or answer-confidence percentages. No minimum threshold is applied: an unrelated query can still return weakly related chunks. There are no raw vectors, owner IDs, internal point IDs, storage paths, or credentials in the response.

Individual malformed payloads, non-finite/nonnumeric scores, duplicates, and unrecognized points are skipped. Required IDs/types/page ranges are checked, and point IDs/text/provenance must match the authoritative owned active MongoDB chunk. MongoDB document/index state is rechecked after the search to exclude changes during retrieval. Rejected/stale hits are not backfilled, so fewer than top_k results (including zero) may be returned. A malformed whole response or infrastructure/configuration failure returns sanitized 503; malformed individual points never produce raw diagnostics. An entirely malformed hit set returns an empty result set and requires operator investigation, not an invented answer.

Current limits: a maximum of 1000 eligible documents per Knowledge Base search bounds the Qdrant generation filter; larger scopes return safe 503 pending a scalable eligibility design. MongoDB is read for candidate eligibility and up to top_k chunk verifications. Rechecks reduce races but do not constitute an atomic snapshot across MongoDB/Qdrant; state may change after the final check. Synchronous inference can block the worker, and the model's existing tokenizer truncation limit still applies to long queries/chunks. There is no query history or relevance guarantee.

Offline tests use fake inference/Qdrant plus an official-client in-memory filtering test. Live Atlas/Qdrant ranking still requires manual verification with an indexed multi-topic PDF, contrasting queries, unrelated queries, and a second Knowledge Base. Phase 7 retrieves chunks only: no LLM-generated answer, Gemini, RAG, citations UI, hybrid/BM25 search, reranking, or Phase 8. Ask DeepDocs remains a placeholder.


## Grounded question answering (Phase 8)

POST /api/knowledge-bases/{knowledge_base_id}/ask requires Bearer authentication and MongoDB Knowledge Base ownership. Request JSON contains only question: a required string, trimmed, nonempty, maximum 1000 characters. Unknown fields (including owner, filters, model, prompt, key, and top_k) return 422; missing/foreign Knowledge Bases return the same 404. Swagger supports API verification; the Phase 9 Ask DeepDocs frontend is described below.

This phase uses the maintained official google-genai==2.25.0 SDK, not google-generativeai. A binary-only pip dry run resolved the SDK alongside every existing requirement on Python 3.13.15. Install backend/requirements.txt locally. Set GEMINI_API_KEY privately in backend/.env or the runtime environment; GEMINI_MODEL defaults to gemini-3.1-flash-lite, the configured default model; confirm availability and pricing with your provider account. No fallback model is used. Model documentation: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite . The SDK/client is loaded only after usable context exists, reused per process, and closed at shutdown. Restart to apply changed Gemini settings.

The RAG service calls the existing Phase 7 retrieval service with server-controlled top_k=5. Owner/Knowledge Base filters, active-generation checks, authoritative chunk validation, and existing query embedding validation are unchanged. No processing or indexing occurs. Query vectors and prompts are not persisted or application-logged. Phase 10 now persists the normalized question and validated public answer snapshot after a completed ask, as described below. Only the question and selected document reference data are sent to the configured Gemini service; runtime credentials are never part of the prompt. Google's service-side data handling is separate from application persistence.

RAG_MIN_RELEVANCE_SCORE defaults to 0.50 (configurable finite range 0–1). This is an intentionally conservative, initial positive cosine-similarity heuristic for the existing normalized MiniLM retrieval, not a calibrated probability or a conclusion drawn from one live sample. It may reject answerable questions and admit irrelevant ones; Phase 11 must evaluate recall, unsupported-answer rates, and domain/language behavior before calibration. Phase 7 search remains threshold-free. Only hits meeting this guard can enter the prompt; no qualifying hits means deterministic insufficient_context without creating/calling Gemini, even when its key is absent.

RAG_MAX_CONTEXT_CHARS defaults to 12000 (allowed 1000–30000) and bounds the complete serialized reference-data array including provenance overhead. A deterministic context builder preserves relevance order, deduplicates chunks, and includes complete chunks that fit; oversized chunks are skipped, never rewritten or sliced. JSON encoding preserves Unicode/source text, and malformed records are skipped. Document/chunk IDs, filename, and page range are retained internally. Phase 9 exposes only the document ID, filename, and page range for included context sources. The separate question is bounded to 1000 characters in addition to this context budget.

The fixed system_instruction requires answers only from retrieved context: no outside knowledge, invented facts, unsupported claims, claimed actions, or document-injected instructions. A JSON user message explicitly separates USER_QUESTION and RETRIEVED_DOCUMENT_CONTEXT; document strings cannot replace the system_instruction field. All document/question content is untrusted data, including fake role markers or requests to reveal keys. There are no tools, function calls, external grounding, agents, or streaming. This is defense in depth, not proof that a generative model will always obey; malicious-content tests verify structural separation, not immunity to every prompt injection.

Gemini is requested to return structured supported/claims JSON with citation references. A completed, unblocked single candidate is required. Empty, malformed, tool-call, truncated, blocked, oversized, invalid-Unicode/control-character, and known-key/internal-metadata responses are rejected safely. Common absolute filesystem-path patterns and prompt-envelope echoes are also rejected; these guards are not a general-purpose sensitive-data classifier. Maximum output is 1024 tokens and the validated answer is capped at 4000 characters. Calls have a 30-second deadline, a single SDK attempt, and no fallback. Provider/network/key/quota/model/response errors map to sanitized 503, never raw SDK exceptions.

Successful public responses include status=answered, answer (plain text), retrieved_chunk_count, citation_version, claims and sources. If no usable context exists, status=insufficient_context, retrieved_chunk_count=0, and answer is always: I couldn't find enough relevant information in this Knowledge Base to answer that question. Gemini can also declare the supplied context insufficient; that returns the same fixed message with the number of chunks it considered, not a provider-written refusal. Relevance alone is never treated as proof of answer support.

Current limitations: generation is instructed to be grounded but there is no independent entailment verifier; fluency, schema validity, or the model's supported flag cannot prove factual support. Evaluate live answers against PDFs, especially on relevant-but-unanswerable and malicious-content questions. Retrieval/answering is not an atomic cross-service snapshot, and a document may change after context retrieval. Existing model token truncation and bounded embedding capacity still apply. Very long complete chunks can exhaust the budget and cause abstention.

Automated verification uses fake Gemini, fake inference/Qdrant, synthetic PDFs, and the existing offline regressions. Live Google API/model access and answer grounding still require manual Swagger verification. Answers are generated under a context-only instruction; validated claim citations, citation UI and persistent Q&A history are implemented. Hybrid search, BM25 and production reranking remain outside the current scope. The current citation contract is detailed in Validated claim citations below.


## Supporting sources and Ask DeepDocs (Phase 9)

The protected React `/ask` page loads the signed-in user's Knowledge Bases, selects the first available one, and accepts one question at a time (trimmed, nonempty, maximum 1000 characters). It uses the existing authenticated Axios client and `/api/knowledge-bases/{knowledge_base_id}/ask`. Loading, empty, retry, provider/network failure, and insufficient-context states are distinct. A missing Knowledge Base can be reconciled by refreshing the selector. Authentication failures use the existing logout mechanism. Switching Knowledge Bases or leaving the page aborts and invalidates pending answers. Requests are not retried automatically.

The existing Phase 8 retrieval, ownership/active-generation validation, relevance threshold, complete-chunk context budget, Gemini provider, and grounding instructions remain in use. Sources are built exclusively from the final validated `Context.chunks` actually supplied to Gemini. Low-relevance, malformed, stale, and over-budget retrieval hits cannot become sources. Filename references reject path separators, drive separators, and control characters. Source text is not rewritten.

Answered response example (illustrative):

```json
{
  "status": "answered",
  "answer": "JWT authentication verifies the token signature and expiration.",
  "retrieved_chunk_count": 2,
  "sources": [
    {"document_id": "507f1f77bcf86cd799439011", "source_filename": "guide.pdf", "page_start": 1, "page_end": 1}
  ]
}
```

Sources contain only `document_id`, `source_filename`, `page_start`, and `page_end`. Identical document/page-range references are deduplicated in first-context-occurrence order. Different ranges remain separate; no adjacent ranges are merged or unsupplied pages inferred. The chunk count counts supplied chunks, not deduplicated source cards. No chunk IDs, owner IDs, vectors, scores, storage paths, prompts, or credentials are added to the public source schema.

Insufficient-context response when retrieval supplies no usable context:

```json
{
  "status": "insufficient_context",
  "answer": "I couldn't find enough relevant information in this Knowledge Base to answer that question.",
  "retrieved_chunk_count": 0,
  "sources": []
}
```

A Gemini abstention also always returns `sources: []`; its existing chunk count still records how many context chunks were considered. Abstention is a valid result, not an infrastructure error. Empty or all-low-relevance retrieval still skips Gemini.

The UI renders answers and filenames as React text, preserves answer paragraphs, and labels backend-derived cards **Supporting sources**, with Page N or Pages N–M. Cards are not links. These references prove only that the pages were supplied as model context; they do not prove every sentence is entailed by, or attributable to, a particular page. There are no invented inline citation numbers, sentence-level attribution guarantees, PDF viewer, or highlighting. Model-generated filenames/page numbers are never used as source metadata.

The browser validates response shape and public page metadata before presentation. Backend/provider exception details are never displayed. Only the question is sent by the form; clients cannot control ownership, retrieval filters, model, prompt, threshold, or context budget. Existing secrets remain backend-only. Phase 10 adds persisted Q&A snapshots below. No conversational memory, streaming, hybrid search, BM25, or reranking is added.

Offline UI verification: with the frontend running on port 5176, run `node frontend/tests/ask.browser.cjs`. Supply `PLAYWRIGHT_MODULE` for an existing Playwright installation if it is not locally available, and optionally `FRONTEND_TEST_URL`. The suite uses Edge headless, mocks all APIs, and writes ignored screenshots under `.verification`; it does not contact Atlas, Qdrant, or Gemini. Live answer/source correctness still requires comparing the real UI's answers and page references against the uploaded PDF.


## Persistent Ask history (Phase 10)

Ask DeepDocs now displays **Previous questions** for the selected owned Knowledge Base, separate from the current result. It loads the newest 20 records, supports keyboard-accessible expansion of full saved answers and source cards, and confirms individual deletion. Completed answered and insufficient-context requests refresh this area without a page reload. History has independent loading/empty/retry states; a failed history refresh does not remove the current answer. Switching Knowledge Bases unmounts the old history and cancels pending reads/deletes; stale responses cannot populate the new selection. The Phase 9 animated loader and reduced-motion support are unchanged.

MongoDB collection `ask_history` stores `_id`, `owner_id`, `knowledge_base_id` (ObjectIds), normalized `question`, `status`, validated public `answer`, `retrieved_chunk_count`, minimal Phase 9 `sources`, and UTC `created_at`. Source document IDs are retained as public string snapshot references. No vectors, scores, Qdrant points, prompts, raw provider responses, runtime credentials, or storage paths are added to history. Public records expose `id`, `question`, `status`, `answer`, `retrieved_chunk_count`, `sources`, and `created_at`; ownership fields are omitted.

Endpoints (all require JWT and MongoDB Knowledge Base ownership):

- `GET /api/knowledge-bases/{knowledge_base_id}/ask-history?limit=20`: full public records, newest first by `created_at` descending and `_id` descending; default 20, accepted range 1–100. Empty history returns `[]`. No pagination or bulk deletion.
- `GET /api/knowledge-bases/{knowledge_base_id}/ask-history/{entry_id}`: one complete owned snapshot.
- `DELETE /api/knowledge-bases/{knowledge_base_id}/ask-history/{entry_id}`: removes only that history entry; success is 204 with no body. Missing/foreign entries return the same safe 404. Invalid IDs/limits return 422.

All history queries include server-derived owner and Knowledge Base IDs; detail/deletion additionally scope by entry ID. An index on `(owner_id, knowledge_base_id, created_at desc, _id desc)` supports bounded lists. Clients cannot create/update records directly or supply ownership. The frontend reuses its existing Axios/token/logout path and renders historical text as React text, never trusted HTML. Shared supporting-source headings have unique accessible IDs when multiple answers are expanded.

History creation occurs **after** the normal RAG result is finalized and validated, and **before** `/ask` returns success. Insertion failure returns sanitized 503 without automatically rerunning retrieval or Gemini. Invalid, unauthorized, foreign-KB, retrieval/provider failures, and cancelled generation before a completed result create no history. Every successful answered request normally creates one record. For insufficient context, the saved snapshot always uses `retrieved_chunk_count=0` and `sources=[]`, including provider abstention; the existing immediate Phase 9 response still reports context considered by the provider when applicable. The existing `/ask` response schema otherwise remains unchanged.

There is no distributed transaction across MongoDB, Qdrant, Gemini, and the browser. Generation may already have incurred work/cost before a save fails. A lost MongoDB acknowledgement or interrupted response can leave a saved record despite a client error; inspect history before intentionally retrying. Client cancellation does not guarantee server cancellation: a request that completes on the server may still be saved. Each attempt allocates one ObjectId and performs one insert; no automatic application retry is made. Repeated intentional submissions may create separate records; this phase does not implement distributed idempotency.

Knowledge Base deletion preserves its existing document/reservation protection. After the conditional KB deletion succeeds, owner/base-scoped history cleanup runs. A cleanup failure returns 503; the deleted KB's history is immediately inaccessible because all history endpoints first require the owned KB to exist. Retrying DELETE for the same KB ID retries scoped history cleanup and then returns the normal 404 for an absent KB. Operators can use this retry for interrupted cleanup. Ask insertion rechecks the owned KB and removes its newly inserted record if the KB disappeared during generation. This handles ordinary deletion/insertion races, but crashes or persistent MongoDB failure between these operations may leave inaccessible records requiring cleanup; it is not transactional cascade deletion. No documents/vectors are deleted by history operations.

Deleting/reprocessing a supporting document **does not delete or rewrite historical Q&A**. History source cards are non-clickable snapshots, explicitly marked as potentially changed/deleted documents. They retain the original filename/page ranges and the Phase 9 limitation: supplied context is not validated sentence-level attribution or proof of current source availability.

History is never read by the retrieval service, context builder, or Gemini provider. Every new question still uses only its own question and current eligible document chunks. There are no history embeddings, history Qdrant points, previous-answer context, follow-up rewriting, conversational memory, chat sessions, streaming, agents, or Phase 11 work.

Verification uses offline MongoDB doubles, fake retrieval/Gemini/Qdrant, and browser API mocks. Run the complete backend tests and `frontend/tests/ask.browser.cjs` plus `frontend/tests/history.browser.cjs` with the existing Playwright setup documented above. Real Atlas persistence, reopening the application, cross-account isolation, and cleanup against the live service remain manual checks.


## RAG quality evaluation (Phase 11)

Developer-only evaluation now lives in [backend/evaluation](backend/evaluation/README.md). It includes a versioned synthetic 35-question / 20-chunk benchmark, Hit@1/3/5 and MRR@5, threshold confusion metrics and sweep, category/score distributions, context-budget/source checks, and mocked RAG decision evaluation. Default runs use explicitly authored regression scores; optional cached-model runs use local-only MiniLM and in-memory Qdrant. Neither contacts Gemini, Atlas, or production Qdrant, nor changes Ask history.

The measured local-model baseline retrieves expected evidence well (Hit@1 95.45%, Hit@3/5 100%) but 0.50 has TP/TN/FP/FN = 14/11/2/8 and incomplete multi-page context. Threshold tradeoffs do not justify a production change from this small benchmark: **0.50, top_k=5, and 12000 context characters remain unchanged**. Full metrics, observed weaknesses, commands, machine-readable reports, and limitations are in the evaluation document. No reranking, hybrid/BM25 retrieval, LLM judge, evaluation UI/API, or Phase 12 functionality was added.

## Dashboard and system status (Phase 12)

The protected Dashboard now loads real workspace statistics through `GET /api/dashboard/summary`. The authenticated user supplies no owner ID: all counts, recent Knowledge Bases, and their document counts are scoped by the existing JWT/current-user dependency. The response contains:

- `knowledge_base_count`: owned Knowledge Base records.
- `document_count`: owned document metadata records, including uploaded/unprocessed records.
- `indexed_document_count`: processed documents with a current ObjectId chunk generation, no active operation lock, and successful matching embedding/vector-index metadata. The active generation, positive chunk count, model, and positive embedding dimension must agree; vector metadata must include its destination and successful indexing timestamp. Embeddings alone, stale/failed states, and in-progress operations do not count.
- `processing_document_count`: records with processing status `processing`; this does not count embedding/indexing operations as PDF processing.
- `failed_document_count`: records with a failed processing, embedding, or vector-index state, counting each document once.
- `ask_history_count`: owned persisted Q&A snapshots, including insufficient-context outcomes, excluding deleted history.
- `recent_knowledge_bases`: at most five `{id, name, document_count, updated_at}` records, sorted by `updated_at` descending then `_id` descending. Per-KB document counts require both the owner and KB relationship.

**Indexed is the last confirmed synchronization recorded in MongoDB, not a fresh Qdrant audit.** This summary does not load an embedding model, inspect live points, or certify compatibility after an external configuration/collection change. The existing protected vector-status endpoint remains the live document verification path. A manual Qdrant change can make recorded metadata out of date.

The summary uses two owner-filtered counts, one owner-filtered document aggregation, and one bounded Knowledge Base aggregation with a document-count lookup. It does not load complete collections into Python or issue one query per recent KB. Existing owner-prefixed indexes are reused. Separate reads are not a transactional snapshot: concurrent uploads, deletions, or asks can briefly produce counts from different instants; Refresh overview reconciles them. Database/invalid-result failures return sanitized 503 responses, without infrastructure details.

Recent KB dates reflect creation or edits to the Knowledge Base itself, **not** document uploads or questions. Manage links go to the existing Knowledge Bases page, since there is no KB detail route. Quick actions link to Knowledge Bases, Documents, and Ask DeepDocs. The four main cards, secondary processing/failure counts, and recent list have loading skeletons, a new-account onboarding state, and recoverable errors; no placeholder counts are shown. Requests are cancelled on refresh/unmount, and existing 401 handling signs the user out.

System status reuses the existing API liveness, MongoDB connectivity, and Qdrant connectivity endpoints. Each independently shows Checking, Operational, or Unavailable, with manual refresh and a bounded browser timeout. Operational means the connectivity check succeeded; it does not certify indexing completeness or answer quality. Health failure does not hide a successful workspace summary. **Gemini is omitted**: loading or refreshing the Dashboard never performs generation requests. No new health/debug endpoints or sensitive configuration fields are exposed.

The current teal/neutral layout, semantic stat labels, visible text statuses, keyboard-accessible links/buttons, and reduced-motion skeleton support are retained. Documents now explicitly distinguishes its processing status from vector indexing. Knowledge Base CRUD and Ask/history UI behavior are unchanged.

Offline verification: run `python -m pytest backend/tests -v` and `python -m pytest backend/tests/test_evaluation.py -v`. With the existing frontend running on port 5176, run `node frontend/tests/dashboard.browser.cjs` (set `PLAYWRIGHT_MODULE` to an existing Playwright installation if needed). It intercepts all API calls, checks errors/owner-auth handling/navigation, and verifies 1440/768/390 widths. Screenshots are ignored under `.verification`. Run the existing Ask/history and auth/KB/Documents regression checks plus `npm --prefix frontend run build` as well. No new dependencies are required.

Phase 11 baseline reports, relevance threshold **0.50**, embedding model, top-k **5**, context budget **12000**, prompts, and retrieval behavior remain unchanged. The context policy described in Phase 13 below is implemented. Live manual verification should compare summary counts and recent KBs against the signed-in user's real records, verify API/database/vector health, exercise quick actions, and check a new account and mobile layout. Offline tests do not replace an Atlas/Qdrant verification.

## Evidence-preserving context policy (Phase 13)

Phase 13 changes only RAG context selection. Search ranking, owner/Knowledge Base/generation filtering, MongoDB authority checks, embedding model, Gemini model/prompt, source mapping, and history persistence are unchanged. The public search endpoint has no secondary threshold.

Diagnosis from the preserved Phase 11 measured rankings: all required evidence for all 22 answerable cases is present **after authoritative retrieval validation** in top five. Baseline context retains complete evidence for only 10/22. Eight answerable questions fail the primary 0.50 gate; four others pass that gate but lose a required lower-scoring page. No benchmark chunk is excluded by the 12000-character budget. Of five multi-chunk questions, four lose secondary evidence and one fails primary relevance. This identifies filtering, rather than top-k/MongoDB validation/context size, as the observed bottleneck in this synthetic corpus. It does not establish that every real PDF has the same bottleneck.

The new policy separates two decisions:

1. **Primary relevance:** the strongest valid hit must score at least `RAG_MIN_RELEVANCE_SCORE=0.50`. Otherwise, no Gemini call occurs, regardless of secondary scores.
2. **Evidence retention:** after that gate passes, valid top-five chunks scoring at least `RAG_MIN_EVIDENCE_SCORE=0.30` may enter context. Existing relevance order, complete-chunk inclusion, 12000-character serialized budget, oversized-chunk skipping, deduplication, and source derivation remain in force. If nothing fits, abstain.

Configuration enforces `0 <= evidence <= primary <= 1`, rejects non-finite/invalid values with the existing sanitized configuration failure, and reads environment values only on the backend. The safe `.env.example` includes the new setting; real `.env` is not modified. To reproduce the old policy, explicitly set evidence to 0.50. Primary remains 0.50, top-k remains 5, context budget remains 12000, and the model remains `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions).

[Phase 13 comparison](backend/evaluation/reports/phase13-comparison.md) and its [JSON](backend/evaluation/reports/phase13-comparison.json) cover secondary thresholds 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, and 0.50. **0.30 is selected as the highest tested threshold retaining the maximum recovered evidence:** 14/22 complete answerable cases (63.64%, previously 45.45%) and 4/5 complete multi-chunk cases (80%, previously 0%). Lower values add non-required passages without recovering more required evidence. At 0.35 completeness falls to 13/22 and 3/5. Ranking remains Hit@1 95.45%, Hit@3/5 100%, MRR 0.97727. Every candidate has unchanged primary decisions TP/TN/FP/FN 14/11/2/8, precision 0.875, recall 0.63636, F1 0.73684.

Tradeoffs are visible rather than hidden: selected context grows from 1.25 to 3.9375 chunks per primary-accepted question (maximum 2 to 5), with four recovered required chunk inclusions and **39 additional non-required inclusions** across the benchmark. Seven of those extras enter the two already falsely accepted unsupported cases. Non-required means outside the case's expected supporting pages, not necessarily harmful. PDF provenance is benign background for a storage-location question; South API-key expiry is potentially misleading for a JWT signing-secret rotation question. These examples are classified from corpus meaning, not scores alone. Primary false positives staying constant does **not** prove that Gemini's unsupported-answer rate stays constant; grounded provider abstention remains necessary. No LLM judge or live-answer claim is made.

Historical reports `local-model.json/.md` and `fixture.json/.md` are preserved byte-for-byte. The original runner and context probes now explicitly pin the historical 0.50/0.50 policy so changing application defaults cannot rewrite the baseline interpretation. Phase 13 has separate reports and deterministic regression gates. Replay reconstructs unique synthetic document/page chunks using the committed measured scores rounded to eight decimals and checks the corpus version/digest. A fresh cached MiniLM CPU + in-memory Qdrant run also reproduced the baseline and candidate results; it used no Cloud services or downloads.

Run from the repository root using the existing backend environment:

```powershell
python -B backend/evaluation/phase13.py
python -m pytest backend/tests -v
python -m pytest backend/tests/test_evaluation.py backend/tests/test_evidence_policy.py -v
```

Optional cached-model confirmation (explicit only, offline weights required, fails without downloads):

```powershell
python -B backend/evaluation/runner.py --mode local-model --output .verification/phase13-local-check
python -B backend/evaluation/phase13.py --rankings-report .verification/phase13-local-check/local-model.json --output .verification/phase13-fresh-comparison
```

The first command writes only the new Phase 13 comparison files by default; it never replaces the four historical baseline artifacts. Ordinary tests and comparison replay require no model inference, internet, Gemini, Qdrant Cloud, or Atlas. Evaluation remains developer tooling with no public endpoint. Query vectors and prompts are not returned or persisted by this change.

Supporting sources still represent only the final context supplied to Gemini, not sentence-level attribution. New Ask history saves the actual final source snapshot; old entries are neither rewritten nor used as context. Frontend production code is unchanged and supports additional source cards already.

Remaining limitations: eight answerable primary rejections remain, including the multi-cloud case. The earlier three-topic manual question may still abstain if its strongest score is below 0.50; this policy cannot safely promise to fix it. Re-test the same direct JWT, paraphrased JWT, unsupported key-rotation, unrelated irrigation, and three-topic questions against the real application. In particular, confirm grounded abstention on key rotation despite the known benchmark overlap. Use held-out documents to evaluate distractor impact before claiming general improvement. Query decomposition is a possible next experiment, not implemented; current ranking gives no immediate reason to add reranking or hybrid/BM25. Validated claim citations are implemented as described below.


## Validated claim citations (Phase 14)

Phase 14 supersedes the context-only source semantics described in the earlier Phase 8–13 sections for **new answers**. It preserves their retrieval, ownership, failure, and history behavior. No package/configuration change is required: primary relevance stays **0.50**, secondary evidence **0.30**, top-k **5**, context budget **12000**, MiniLM **384 dimensions**, and the existing Gemini model/SDK remain unchanged. Semantic `/search` is unchanged. Phase 11/13 committed reports remain historical artifacts.

After the existing context builder has selected complete eligible chunks within its budget, `rag_citations.citation_context` assigns request-local integer IDs 1–5 in first-included order. Exact `(document_id, page_start, page_end)` matches share an ID; filenames alone never establish identity. Adjacent/overlapping but unequal ranges remain separate. Internally the mapping retains the included chunk IDs. The Gemini projection contains only `citation_id`, `source_filename`, `page_start`, `page_end`, and original `text`. It removes document/chunk IDs rather than enlarging the selected context; its serialized length cannot exceed the already-budgeted original. There is no second selection or extra evidence allowance.

The existing lazy Gemini provider requests strict structured JSON:

```json
{"supported":true,"claims":[{"text":"The server verifies the token signature.","citation_ids":[1]}]}
```

The server accepts 1–12 short claims for a supported answer, each with nonblank text and 1–5 integer references. Booleans, numeric strings, unknown/out-of-map IDs, unexpected fields, empty claims, missing citations, malformed JSON, blocked/truncated output, and excessive lengths fail safely. Exact repeated IDs within a claim normalize in first-occurrence order; using the same ID in different claims is valid. The public plain answer joins the trimmed claim texts with two newlines and retains the **4000-character total limit**, including separators. A model cannot supply or overwrite source objects, filenames/pages/URLs as citation metadata. No inline-marker parser trusts model prose.

`POST /api/knowledge-bases/{knowledge_base_id}/ask` still accepts only the existing question body. Successful responses add versioned structured fields:

```json
{
  "status": "answered",
  "answer": "The server verifies the token signature.",
  "retrieved_chunk_count": 2,
  "citation_version": 1,
  "claims": [{"text":"The server verifies the token signature.","citation_ids":[1]}],
  "sources": [{"citation_id":1,"document_id":"507f1f77bcf86cd799439011","source_filename":"guide.pdf","page_start":1,"page_end":1}]
}
```

`retrieved_chunk_count` continues to count chunks actually supplied to the provider, **not** source cards or claims. `sources` contains only supplied page references used by validated claims, in context order. Unused sources are omitted without renumbering, so a valid answer may cite `[2]` without `[1]`. All public provenance is resolved from the server map, never model-generated metadata. No owner IDs, internal chunk IDs, vectors, scores, paths, prompts, or credentials are added to the response.

Primary-gate rejection, empty/oversized unusable context, and genuine provider abstention retain the deterministic `insufficient_context` message, with `citation_version:1`, `claims:[]`, `sources:[]`. Pre-provider rejection has count 0; a provider abstention retains the supplied count in the current response and the existing history policy stores count 0. A provider must return `supported:false, claims:[]` to abstain. Invalid citations are **503 service failures**, not silent citation dropping, fabricated replacements, legacy fallback, or insufficient-context answers. They create no history and cause no automatic generation retry. Existing provider timeout, blocked/empty output, safe error handling, and history-persistence-before-success policy continue.

New `ask_history` entries snapshot version 1, deterministic answer, claims, and trusted cited sources alongside the existing fields. No migration or rewriting occurs. Missing version means legacy version 0: old answer text and context-only sources remain unchanged, no claim attribution is inferred. History reads expose empty legacy claims and optional null source IDs; malformed version-1 records fail with sanitized history errors instead of downgrading. Deletion, owner/KB isolation, snapshot behavior after document deletion, and exclusion of history from retrieval/Gemini context remain unchanged.

The shared React `AnswerContent` renders current and historical answers as text. Version-1 claims have accessible numbered buttons beside them; each button focuses its matching numbered page card within that answer instance. Unique React IDs isolate current answers and multiple open history entries. No HTML/Markdown execution, model-generated links, PDF viewer, downloads, or page highlighting is introduced. Legacy records keep the context-only explanation. Source cards themselves remain non-clickable snapshots. The API parser validates version, claim/source membership, unique mappings, page ranges, counts, and deterministic answer agreement before rendering; malformed version-1 responses use existing safe error states. Existing cancellation, loading animation/reduced motion, history refresh/delete, and KB switching behavior are preserved.

**Citation limitation:** a citation verifies that the referenced page was part of validated context supplied to the model and was selected by the model for that claim. It does **not** independently prove semantic entailment or factual correctness. A valid ID can still be attached to a poorly supported claim. Prompt instructions treat source text as untrusted data, prohibit document instructions from redefining IDs, and require grounded abstention; these are defenses, not a semantic proof or absolute prompt-injection guarantee. There is no LLM judge, conversation memory, retrieval optimization, or Phase 15 work.

Offline verification commands (from the repository root, using the existing environment):

```powershell
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -v -p no:cacheprovider
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_citations.py backend/tests/test_evaluation.py backend/tests/test_evidence_policy.py -v -p no:cacheprovider
npm.cmd --prefix frontend run build
# With the frontend already running on 127.0.0.1:5176 and an existing Playwright installation:
node frontend/tests/citations.browser.cjs
node frontend/tests/ask.browser.cjs
node frontend/tests/history.browser.cjs
```

Browser scripts accept `PLAYWRIGHT_MODULE` and `FRONTEND_TEST_URL`; all backend/provider responses are mocked. Tests never need Gemini, Atlas, Cloud Qdrant, or model downloads. Synthetic screenshots remain ignored under `.verification`.

Manual live verification remains necessary: repeat the direct JWT, paraphrased JWT, unsupported signing-key rotation, unrelated irrigation, and three-topic JWT/MongoDB/cloud questions. Compare each cited claim against the actual PDF page text, especially multi-source claims. Verify new history after reload, an old pre-citation record, history deletion, keyboard focus, and mobile rendering. Live response reliability under the unchanged 1024-output-token cap and semantic accuracy are not established by mocked tests; truncated structured responses fail safely. Do not use an online provider merely to run regressions.

## Phase 17A — management browsing

Knowledge Bases and Documents now use server-side name/filename search, allowlisted sorting, and pagination. These are literal management searches, not semantic or chunk-text search. The existing array list endpoints remain unchanged for compatibility; in particular, Ask and Documents still load every owned Knowledge Base into their selectors rather than silently stopping at 20. Dashboard aggregation, Ask/history/citations, and all production RAG settings are unchanged.

Authenticated management endpoints:

- `GET /api/knowledge-bases/browse`
- `GET /api/knowledge-bases/{knowledge_base_id}/documents/browse`

Both return `{items, page, limit, total, total_pages}`. Items retain the existing public KB/document fields; document browse items additionally expose `indexed` and `failed` booleans derived from authoritative stored state. No owner IDs, internal vector state, paths, or credentials are exposed. Document browsing checks KB ownership before the database query; foreign KBs return the existing safe 404.

| Parameter | Behavior |
| --- | --- |
| `search` | Trimmed, case-insensitive substring match on KB `name` or document `original_filename`; maximum 200 trimmed characters. Empty means no search. Regex punctuation is escaped and matched literally. |
| `page` | Positive whole number, default 1, maximum 1,000,000. |
| `limit` | Positive whole number, default 20, range 1–100. |
| KB `sort` | `updated_at` (default), `created_at`, `name`. |
| Document `sort` | `created_at` (default), `updated_at`, `filename`. No computed status sort. |
| `order` | `desc` (default) or `asc`; `_id` provides the same-direction deterministic tie-break. Name sorting uses MongoDB's normal binary ordering, independently of case-insensitive search. |
| Document `status` | `all` (default), `indexed`, `processing`, `failed`. |

Invalid/unknown parameters use the existing sanitized 422 response. Out-of-range pages return an empty item list with accurate totals; an empty collection has `total_pages: 0`. The UI presents that as Page 1 of 1.

**Status definitions:** Indexed uses exactly the Dashboard's last-confirmed synchronization expression: processed document, active chunk generation/count matching successful embedding and vector-index metadata, matching model/dimension, valid indexed timestamp/target collection, and no active operation. It is not a live Qdrant check and does not mean merely that an embedding exists. Processing means the stored document processing state is `processing`. Failed means document processing, embedding generation, or vector indexing is `failed`. These independent categories follow Dashboard semantics. When multiple fields disagree, a Failed badge takes precedence over Indexed and processing labels. Uploaded and Processed remain visible under All.

**Database work and limitations:** A single owner-scoped aggregation uses `$match`, allowlisted `$sort`, and `$facet` for total count plus `$skip`/`$limit` items (at most 100), with a five-second database time limit. Documents additionally make one KB ownership read. No per-item queries or full-collection Python pagination are used. No indexes are added: existing `(owner_id, updated_at desc, _id desc)` KB and `(owner_id, knowledge_base_id, created_at desc, _id desc)` document indexes support the default ordering and its reverse. Additional name/date sorts and computed status filters can require scoped scans/sorts. Unanchored case-insensitive substring matching is **not** fully index-efficient. Deep offset pages and total counts can be expensive; timeouts return a safe retryable error. Stable tie-breaking is not snapshot pagination: concurrent inserts/deletes/edits can move items between requests. Future index additions should follow observed workloads rather than speculative indexes for every combination.

**UI behavior:** Search is debounced for 300 ms. Search/status/sort changes reset page to 1. URL query parameters preserve browsing state across refresh/back/forward; Documents also stores the selected KB as `kb`. Previous/Next controls show result/page counts and disable unavailable/pending actions. Abort signals and guarded updates prevent old responses from replacing current results; KB switching remounts the document collection. Background updates retain existing cards with a busy state and “Updating results…”; initial loading, initial empty, no-match, and safe error/retry states are distinct. Clear filters clears search/status while retaining sort. Successful create/edit/upload/delete refreshes the server list, preserving filters and ordering; uploads are not forcibly inserted into a filtered page. An impossible page after deletion is replaced with the last valid page. Controls are labeled, keyboard usable, have visible focus, and wrap on mobile; async page status is announced and no new motion is introduced.

Offline checks (existing Python/Playwright installations; Vite running on port 5176):

```powershell
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests/test_management.py -q
.\backend\.venv\Scripts\python.exe -B -m pytest backend/tests -q
node frontend/tests/management.browser.cjs
node frontend/tests/knowledge-bases.browser.cjs
node frontend/tests/documents.browser.cjs
node frontend/tests/auth.browser.cjs
node frontend/tests/dashboard.browser.cjs
node frontend/tests/ask.browser.cjs
node frontend/tests/history.browser.cjs
node frontend/tests/citations.browser.cjs
npm.cmd --prefix frontend run build
```

Browser scripts accept `PLAYWRIGHT_MODULE` and `FRONTEND_TEST_URL`. All API traffic is intercepted using synthetic fixtures; no real backend/cloud verification is implied. Management tests cover URL state, debounce/stale requests, >20-KB selector compatibility, filter-aware upload refresh, last-page deletion, safe rendering, and 1440/768/390 overflow checks. Live manual verification should compare real counts/filter states, including newly uploaded PDFs and last-confirmed Indexed metadata, against the current account before committing.

## Phase 17B, Part 1 — backend bulk document deletion

`POST /api/knowledge-bases/{knowledge_base_id}/documents/bulk-delete` requires the existing JWT authentication and an owned Knowledge Base. The strict JSON body is `{"document_ids":["<document-id>"]}`: 1–100 ObjectId strings, no unknown fields. IDs are canonicalized to lowercase; duplicates (including differently cased copies) are rejected. Malformed requests return sanitized 422 before deletion starts. Missing and foreign Knowledge Bases share the existing 404 response.

Documents are processed **sequentially in request order**, using the existing `documents.delete_document()` cleanup service. An optional expected KB scope is applied inside the atomic document claim and its busy check. A missing document, foreign-owner document, or document in another KB produces the same `not_found` result without mutation. An existing operation claim produces `busy`; it is never stolen or expired.

Completed batch reports use HTTP 200, including mixed outcomes:

```json
{
  "operation": "delete",
  "requested": 3,
  "succeeded": 1,
  "failed": 1,
  "not_attempted": 1,
  "results": [
    {"document_id": "<id-1>", "outcome": "succeeded"},
    {"document_id": "<id-2>", "outcome": "failed", "code": "service_unavailable"},
    {"document_id": "<id-3>", "outcome": "not_attempted"}
  ]
}
```

Per-item failure codes are restricted to `not_found`, `busy`, and `service_unavailable`. Not-found/busy items do not stop the batch. A known vector, database, filesystem, or timeout failure stops execution: the current item is failed and every remaining ID is explicitly not attempted. Infrastructure failure before execution uses the existing safe 503 handling. Null codes are omitted. Results contain no exception text, storage locations, vector internals, or credentials.

Cleanup order and recovery behavior remain authoritative in the single-delete service: claim, invalidate vector metadata, clean vectors, remove PDF, clean chunks, release KB reservation, delete document metadata. There is no transaction across these systems and no rollback of earlier successes. A failed item may already be partially cleaned; a lost response can leave the client uncertain whether deletion completed. Refresh before retrying, and do not automatically replay a whole batch. Existing missing-file retry behavior is preserved. Cancellation is propagated rather than converted into a false completion report; existing claim recovery limitations remain, with no lock-expiry mechanism added. Ask history snapshots are untouched.

Offline verification: run `backend/tests/test_document_bulk.py`, the existing document/processing/embedding/vector tests, and the complete backend suite using the existing Python environment. Bulk tests use temporary synthetic files and a fake vector client; no model downloads or live services are required. This section documents **Part 1 (delete)**; Part 2 is below. Frontend selection/bulk controls are documented in Part 3 below. RAG, retrieval, citation validation, and production settings are unchanged.

## Phase 17B, Part 2 — backend bulk re-indexing

`POST /api/knowledge-bases/{knowledge_base_id}/documents/bulk-reindex` accepts the same strict `{"document_ids":["<document-id>"]}` body, with **1–5 documents** rather than 100. It inherits Part 1's ObjectId canonicalization, duplicate rejection, unknown-field rejection, authenticated KB ownership checks, safe errors, and request-ordered report format. The response uses `"operation":"reindex"`. HTTP 200 describes a completed report, not universal success.

Re-indexing is **request-scoped and sequential**, with encoding on the dedicated worker: existing successfully processed active chunks → regenerated embeddings → rebuilt Qdrant vectors → successful metadata publication. It never calls PDF processing, extraction, or chunk creation. It reuses `document_embeddings.generate_embeddings()` for chunk inspection, embedding batches, vector validation, destination validation, cleanup, deterministic point IDs, upsert/count verification, and generation-guarded publication. It reuses the existing bounded embedding worker without parallel batches, a waiting admission queue or automatic retries.

Eligibility requires `status=processed`, a valid ObjectId chunk generation, and active chunks accepted by the existing indexing validation. Uploaded-only, processing-failed, or missing/invalid-generation documents return `requires_processing`; no automatic processing occurs. Clearly ineligible scoped documents are rejected by a read-only preflight, with busy claims taking precedence. Authorization and eligibility are rechecked at/after the atomic owner + KB + document claim, so the preflight is never permission to mutate a subsequently changed resource. Chunk inspection happens under that claim; an empty valid chunk set returns `requires_processing` and releases it without changing embedding/vector metadata. Inconsistent chunk counts/order are sanitized service failures, not silently repaired data.

Results use `succeeded`, `failed`, or `not_attempted`. Safe failure codes are `not_found`, `busy`, `requires_processing`, and `service_unavailable`. Missing, foreign-owner, and wrong-KB documents remain indistinguishable. Not-found/busy/requires-processing items allow subsequent items to run. A model, vector, database, or other recognized service failure stops execution and explicitly marks all remaining IDs not attempted. No vectors, model/cache details, collection configuration, or raw exception text are included in the report.

**Availability and recovery:** re-indexing is duplicate-safe, but is not zero-downtime. The operation claim excludes the document from retrieval and old vectors may be removed before replacement finishes. Existing failed-state and retry cleanup behavior is preserved; partial Qdrant points may remain until retry/recovery, without successful indexing metadata. Previous successful items are not rolled back. Cancellation propagates rather than becoming a normal batch result. Before writes begin, the service attempts to release its claim; if that release fails, cancellation is preserved and recovery is required. After writes may have begun, existing state/claim is retained for recovery rather than guessing the outcome. If publication already committed before acknowledgement was cancelled, successful metadata remains intact. Cancellation/lost responses therefore require inspection before retry; there is no automatic lock expiry. A five-document cap bounds count, not total duration.

Run `backend/tests/test_bulk_reindex.py` together with Part 1 tests, existing document/processing/embedding/vector/search tests, and the complete backend suite. Tests fake inference/vector IO, check that processing is never invoked, and cover scope races, repeated indexing, result ordering, shared failures, and cancellation before writes, after writes, during unlock, and after publication. No live services are required. This section covers the backend only; Part 3 below documents the frontend integration.


## Phase 17B, Part 3 — page-scoped document actions

Documents now supports individual checkboxes, an indeterminate **Select current page** checkbox, a selected count, and Clear selection. Selection includes only IDs from the currently displayed authoritative browse response—never all matching records across pages. Selected IDs are local UI state and are not added to URLs. Search input (including the debounce interval), status/sort/order/page/KB changes invalidate selection. Explicit refresh, upload refresh, and completed mutations also clear it. Retained cards from an older query cannot be selected or acted on while the next result is loading.

Both bulk actions require confirmation of a frozen selection snapshot. Delete supports at most 100 documents, matching the server page maximum. Re-index supports at most 5; above that limit its button is disabled with an explanation, and requests are never silently split. Re-index regenerates embeddings/vectors from existing processed chunks and **does not process or re-extract PDFs**. The backend determines eligibility and reports unprocessed documents as `requires_processing`. Its request-scoped execution remains sequential, and replacement may temporarily make documents unavailable to retrieval.

During submission, selection, upload, single delete, browsing controls, and conflicting bulk actions are disabled. A synchronous pending guard prevents duplicate requests. Completed reports show either concise complete success or explicit succeeded/failed/not-attempted counts with safe filename-based explanations. HTTP 200 alone is not treated as indexing success. The UI validates operation, requested IDs/order, allowed outcomes/codes, stop semantics, and all counts before rendering. It refreshes authoritative server state after confirmed results, clears submitted selection, and reuses last-valid-page recovery after deletions. No failed or unattempted items are automatically retried or reselected.

Lost, timed-out, or malformed responses show an uncertain-outcome message and require a document refresh before selection or further document mutations. There is no automatic replay. These two bulk calls use a bounded five-minute client timeout to accommodate synchronous work; timeout, browser navigation, or abort does **not** prove the backend stopped or rolled back. Scope changes/unmount abort local requests and discard stale results. Cancellation/recovery limitations from Parts 1–2 still apply.

Checkboxes have filename-specific labels, controls are keyboard operable with visible focus, the page checkbox exposes native indeterminate state, and confirmations/status reports use existing accessible page patterns. Filename/error rendering is React text. Controls wrap at 1440/768/390 layouts, and no new animation or dependencies are introduced. Existing Ask/history/citations, Dashboard, RAG settings, and unpaginated KB selectors remain unchanged.

Offline frontend check: `node frontend/tests/document-bulk.browser.cjs`, with Vite running and the existing `PLAYWRIGHT_MODULE`/`FRONTEND_TEST_URL` settings. Run the existing auth, KB, Documents, management, Dashboard, Ask, history, and citation browser suites, the production frontend build, and the complete backend suite as regressions. Tests intercept all API calls and use synthetic records; they do not establish live Atlas/Qdrant verification. Before committing, manually verify a small owned batch, partial outcomes, processed versus uploaded re-index eligibility, page recovery, and refreshed server status against the real application.

## Phase 18B, Part 1 — read-only operation inspection

`GET /api/documents/{document_id}/operation-status` requires JWT authentication and document ownership. Invalid IDs return 422; missing and foreign documents return the same 404; database failures return sanitized 503 responses. It uses one bounded owner-scoped MongoDB aggregation, without acquiring or releasing a claim. It does not access Qdrant, PDF storage, chunks, or KB reservations, and triggers no mutations or recovery.

The explicit response contains only `document_id`, `operation_state`, `document_state`, `attention`, and `recommended_action`. Example: `{"document_id":"<id>","operation_state":"idle","document_state":"uploaded","attention":"needs_processing","recommended_action":"process"}`.

| Metadata snapshot | Document state | Attention | Recommended action |
|---|---|---|---|
| Any existing claim, including legacy or malformed tokens | Recorded lifecycle state, or unknown; never Indexed | outcome_uncertain | refresh |
| Unclaimed, matching the exact Dashboard/management Indexed expression | indexed | none | none |
| Unclaimed uploaded document without generation/index activity | uploaded | needs_processing | process |
| Processed with valid generation/count metadata, not indexed | processed | needs_reindex | reindex |
| Processed generation with embedding/vector failure | failed | needs_reindex | reindex |
| Document status failed | failed | requires_review | contact_support |
| Missing/inconsistent metadata or unlocked in-progress state | unknown | requires_review | contact_support |

`operation_state` is `idle` only when `_operation` is absent; otherwise it is `claimed_unknown`. A claim may represent ongoing work or an outcome requiring verification. Its age does not establish abandonment, and inspection never recommends retrying a claimed document. Document-level failure can also mean incomplete deletion, so it conservatively recommends review rather than guessing that processing should be retried.

This is a point-in-time metadata view, not a certificate of live vector contents, PDF existence, active-chunk integrity, worker liveness, or recovery eligibility. Even an `idle` response may become stale immediately; mutation endpoints retain their existing claim and eligibility checks. Suggested process/reindex actions are workflow guidance, not automatic execution or permission to bypass those checks. No tokens, errors, private configuration, or storage paths are returned. Existing vector-status and management behavior is unchanged. Recovery, automatic unlocking, and Phase 18 completion are not implemented.

Offline tests: `python -B -m pytest backend/tests/test_operation_inspection.py -q -p no:cacheprovider`, followed by the complete backend suite. No live services or model downloads are required.

### Phase 18B, Part 2 — document inspection panel

Each visible document now has an **Inspect status** action. The read-only panel shows its name, recorded document state, operation state, attention, and advisory next action. The receipt timestamp uses the user's device clock and is explicitly a point-in-time snapshot, not a server observation timestamp or live-health guarantee. A claimed/uncertain result warns against retrying or deleting until verified; no unlock, recovery, or automatic retry controls are provided.

Inspection uses the existing authenticated client and validates the public response before displaying it. Loading/re-inspection clears the prior snapshot; errors use safe wording, and expired authentication follows existing sign-out behavior. **Inspect again** performs one explicit read; there is no polling. Opening another document, changing the management view/Knowledge Base, refreshing the list, starting a mutation, or leaving the page invalidates the old panel/request. Inspection itself does not change selection, management URL state, or document data. Source names and messages are rendered as React text.

The panel has a labeled region, keyboard focus on opening, focus return on explicit close, loading/status announcements, and responsive wrapping. Offline coverage: `node frontend/tests/document-inspection.browser.cjs` using the existing Vite/Playwright browser-test setup. Manual authenticated verification against the real application is still required; Phase 18B is not declared complete by these mocked checks.

## Phase 18C-A, Part 1 — claim acquisition hardening

Document claims retain the same preallocated ObjectId token, owner/document scope, optional KB scope, and requirement that `_operation` be absent. No existing claim is replaced. After a recognized database/IO/timeout acquisition failure, the helper performs one application-level verification read, bounded to five seconds, using primary read preference and majority read concern on a collection view. Global MongoDB retry/read/write settings are unchanged. Unacknowledged write configuration is rejected before acquisition dispatch.

Only a document matching the exact scope **and the original token** can resume the request. Its detached business-state snapshot omits the newly acquired token, preserving the existing pre-update snapshot contract for claim-respecting callers. A missing record, absent/different token, mismatched scope, timeout, or unavailable/unsupported verification returns the existing sanitized service failure. There is no application-level acquisition replay, new-token retry, or unlock on an inconclusive read. Error classes and retryable-write labels alone are not proof that every write attempt failed. Known local setup failure occurs before dispatch; once the driver write is invoked, the helper conservatively verifies recognized failures rather than inferring non-commit.

Cancellation during acquisition or verification propagates immediately and never starts processing, indexing, deletion, or another bulk item. The helper does not unlock on cancellation: a cancelled local await cannot prove the remote command has stopped. An adapter returning after a still-pending cancellation request is also prevented from handing ownership downstream. This can retain claims for service review, intentionally; it is not automatic recovery. Exact-token release for normal lifecycle operations remains unchanged. Bulk uncertainty uses `service_unavailable` and marks later items `not_attempted`; ordinary busy/not-found semantics remain unchanged.

Limits: hard crashes, failed verification, and delayed writes can still leave retained claims. Majority verification requires deployment support and does not restore an unknown historical pre-image if out-of-band writers bypass claims. Existing acknowledged-write durability assumptions remain; this change does not certify the deployment's failover behavior. Upload insertion/compensation, Phase 18B inspection, frontend behavior, and RAG are unchanged. No claim expiry, worker, lease stealing, or force-unlock operation is added.

Offline fault injection: `python -B -m pytest backend/tests/test_document_claims.py -q -p no:cacheprovider`, plus document/processing/embedding/vector/bulk/inspection regressions and the complete backend suite. Live failover and acknowledgement-loss behavior has not been verified against Atlas.

## Phase 18C-A, Part 2 — upload acknowledgement uncertainty

The upload API and PDF validation remain unchanged. A document ID is allocated before its owner-scoped KB reservation and reused for the PDF filename and metadata insert. A reservation write error triggers one primary/majority verification read, bounded to five seconds. Saving starts only after the exact ID is confirmed reserved in the owned KB. Missing or unavailable verification retains uncertainty without releasing the reservation or replaying the write. Unacknowledged write configuration is rejected before reservation acquisition.

After an insert error, the same bounded verification checks document ID, owner, KB, original filename, stored filename/storage reference, content type, and size. Lifecycle fields may have changed legitimately and are not compared or overwritten. A confirmed matching record returns the existing success response for an active request. Duplicate-key and network errors are not proof of non-commit. Absence, mismatched metadata, or unavailable verification returns a sanitized 503 while preserving the PDF and reservation. No insert is retried with a new ID. A client must not interpret an uncertain response as proof that no upload exists.

Compensation is limited to failures known to precede insertion dispatch, including PDF validation/storage failures and local BSON serialization rejection. File deletion uses the existing validated document-specific reference; reservation release follows successful cleanup. Failed/uncertain cleanup retains the reservation. Once insertion has been invoked, driver/server exception classes or retry labels alone never authorize destructive compensation.

Cancellation propagates during reservation, insertion, and verification without destructive cleanup. A cancelled PDF save waits at most five seconds for its existing shielded thread task. If it finishes successfully within that interval, pre-insertion compensation remains possible. A repeated cancellation, timeout, or uncertain worker failure preserves resources. Stream finalization uses an explicit, lock-protected worker lifecycle: only the actual save function's `finally` marks a running worker finished. A cancelled async wrapper is never completion evidence. Closure requested during saving is performed by that same worker after saving exits, including failure paths; no event-loop callback or new worker is needed. If closure precedes worker dispatch, the lifecycle is sealed so a later dispatch cannot use the closed stream. Closure is attempted once, with a sanitized warning on failure. This finalizer does not perform recovery or insert metadata. Cancellation during compensation never releases a reservation before file cleanup is confirmed.

These changes are **not full crash safety**. Crashes, delayed writes, lost responses, failover, or unavailable verification can retain orphan PDFs/reservations requiring review. Once closure has been requested, worker-side finalization survives event-loop shutdown, but hard process termination, a worker that never returns, or a failing stream close can prevent explicit finalization. A reservation left by an ambiguous acquisition may have no PDF yet. Primary/majority verification needs deployment support; global retry/read/write settings are unchanged, and deployment durability/failover guarantees are not certified. Intentionally repeated uploads remain separate requests. There is no automatic recovery, cleanup scan, or retry. Phase 18B inspection, claim lifecycle, frontend, and RAG behavior remain unchanged.

Offline fault injection: `python -B -m pytest backend/tests/test_upload_safety.py backend/tests/test_documents.py -q -p no:cacheprovider`. Tests use fake MongoDB collections and temporary PDFs, including delayed commit, cancellation, and repeated cancellation during file saving. Live Atlas failover has not been tested.

## Phase 18C-B, Part 1 — processing cancellation and generation safety

Processing propagates cancellation unchanged. Cancellation checkpoints surround synchronous extraction and successive remote stages; PyMuPDF runs in the supervised child process; request cancellation does not prove native completion. Cancellation retains the claim, including conservative retention before the first processing write. It does not publish an ordinary failure, compensate chunks, or automatically unlock.

Processing tracks its stage before each mutation. An uncertain initial state write, chunk insertion, vector cleanup, activation, obsolete-chunk deletion, or unlock returns a sanitized service error without further compensation or release. Activation commitment determines the authoritative generation, including when its acknowledgement is lost; acknowledgement determines whether the caller can safely proceed. Activation is guarded by owner, KB, document, exact operation token, and the prior generation snapshot. A lost activation acknowledgement preserves potentially active data. No negative read is treated as evidence that a dispatched command cannot complete later.

Successful cleanup targets only the prior authoritative ObjectId generation captured under the claim, with owner/KB/document scope. It never uses `generation != current`; future generations and unrelated resources cannot match the captured target. Other orphan generations are deliberately left for later review. Invalid prior-generation metadata prevents destructive cleanup and retains the claim. Uncertain cleanup completion also retains the claim, even after successful activation.

A completed local extraction/validation failure, before chunk or vector dispatch, may publish the existing failed state and then release the exact token. Failure or cancellation during that publication/release stops further cleanup. Successful processing releases only after activation and the fixed-target cleanup finish. An uncertain unlock is not retried; it may already have committed.

The existing vector helper is unchanged. Processing conservatively treats any error from that boundary as uncertain, including errors the helper does not distinguish from pre-dispatch configuration failures. This can retain more claims than strictly necessary. Retained claims block processing, indexing, and deletion and remain visible through Phase 18B inspection; this is not recovery eligibility or a new unlock mechanism. Crashes, delayed writes, and missing acknowledgements can still leave retained claims and inactive chunks. No complete crash recovery, automatic retry, or live-service verification is claimed.

Offline tests: `backend/tests/test_processing_cancellation.py`, existing processing/vector tests, Phase 18C-A claim/upload tests, bulk/inspection tests, and the complete backend suite. Use the backend directory on `PYTHONPATH` when running individual test modules.

## Phase 18C-B, Part 2 — indexing cancellation and write uncertainty

Indexing tracks local validation, encoding, collection setup, prior-vector cleanup, target metadata, document-wide vector deletion, batch upserts, count verification, and final publication. A pending mutation is recorded before dispatch and cleared only after its acknowledged result passes the existing checks. The successful workflow, deterministic point IDs, vector validation, sequential batching, and count checks are unchanged. All indexing metadata writes now include owner, KB, document, exact token, and expected chunk generation.

Transport errors, timeouts, incomplete acknowledgements, and cancellation during document-affecting mutations retain the claim and existing uncertainty. They do not publish definitive failed indexing metadata, replay commands, or independently unlock. Count errors/mismatches also retain the claim: a count is not proof that a previously uncertain command cannot finish later. The unchanged vector helper conservatively makes some pre-dispatch/configuration failures indistinguishable from uncertain mutation; those may retain a claim unnecessarily. Collection setup only creates/validates shared infrastructure and precedes document-vector mutation; its failure can still use the safe failed-state path when all preceding document writes were acknowledged.

Completed local model/validation failures may preserve the existing failed-state behavior when no document mutation is outstanding. Failed-state publication and unlock remain atomic and token/generation scoped; if that write is uncertain, no second unlock is attempted. Cancellation propagates unchanged. Before mutation, exact-token release is attempted; after mutation starts, cancellation conservatively retains state. Encoding runs on the dedicated embedding thread, with checkpoints between stages and batches. A checkpoint cannot interrupt a native encode call mid-execution.

Final indexed publication and claim removal remain one MongoDB update. A lost acknowledgement returns sanitized service failure without retry or failed-state compensation. If publication actually committed, indexed metadata and its atomic unlock remain intact. No positive-verification/resume path is added in this part. Cancellation after committed publication still propagates without undoing it. Bulk re-index preserves ordered `service_unavailable` and `not_attempted` outcomes; cancellation never launches the next item. The batch outcome reports request execution failure, not proof that remote vector writes failed.

This is not full crash/failover recovery. Uncertainty can leave partial vectors and claims requiring review, and replacement remains non-zero-downtime. No negative read authorizes recovery, no automatic retries/unlocks are added, and real deployment failover/durability is not certified. No separate generation-specific vector deletion API was added. Phase 18B inspection, Phase 18C-A uploads/claims, Part 1 processing, public APIs, and RAG behavior are preserved.

Offline coverage: `backend/tests/test_indexing_cancellation.py`, embedding/vector/bulk regressions, processing cancellation, claim/upload safety, inspection, and the complete backend suite. Synthetic fakes independently control dispatch, commit, acknowledgement loss, and delayed completion; no live services or model downloads are needed.


## Phase 18C-B, Part 3A - deletion claim retention

Deletion validates the PDF reference before mutation, then invalidates vector metadata,
removes vectors, removes the PDF, removes scoped chunks, releases the KB reservation,
and deletes exact-token document metadata. Once mutation dispatch begins, any error or
cancellation retains the claim if the document still exists. No failed-state overwrite,
automatic retry, compensation, or independent unlock follows uncertain deletion.
Only failures before mutation dispatch can release the exact token safely.

A cancelled filesystem await is not proof its thread stopped. The claim remains even
if the worker later finishes; no duplicate deletion is launched. Cancellation propagates
and bulk deletion stops before subsequent items. Service failures preserve ordered
service_unavailable/not_attempted reports. Lost final metadata acknowledgement can mean
the document is already gone; no replay is attempted. Conservative retention also covers
completed local deletion failures after dispatch, because the whole lifecycle may be
incomplete. Read-only operation inspection reports retained claims as uncertain.

This does not provide crash recovery, automatic unlocking, or a transaction across
MongoDB, Qdrant and storage. Partial cleanup and released reservations may remain after
interruption, and deployment failover guarantees are not certified.

## Phase 18C-C, Part 1 - atomic operation metadata foundation

New claims preserve the scalar `_operation` ObjectId and atomically initialize a
bounded `_operation_meta` sibling: schema version 1, matching token, allowlisted
type (default `unknown`), UTC start/recording times, `claimed` stage, sequence 0,
null acknowledged stage/error category, and `in_progress` outcome. Diagnostic
fields are excluded from the business-state snapshot returned to claim callers.

The internal stage helper accepts only allowlisted stage/outcome/error values and
matches document, owner, optional expected KB, exact claim and metadata tokens,
schema version, and expected sequence. It advances the sequence without upsert.
Mismatch or uncertain acknowledgement returns a sanitized internal service error;
there is no replay, unlock, or inference of non-commit. Cancellation propagates.
Acquisition verification additionally checks the matching metadata token/version.
The release helper removes claim and diagnostics in one exact-token update.

Legacy claims remain protected, including missing/malformed metadata. New claims
replace stale diagnostics atomically. No lifecycle checkpoint calls or public
response changes were included in Part 1. Part 2B below extends atomic diagnostic
cleanup to inline indexing completion writes. Any legacy remaining diagnostics
are stale, not an active operation or completion log. Phase 18B inspection remains unchanged. No migration,
history collection, indexes, heartbeat, automatic recovery, or expiry is added.
Timestamps and recorded stages never prove worker liveness or authorize recovery.
These diagnostics do not improve the deployment's crash/failover guarantees.

## Phase 18C-C, Part 2A - processing diagnostic checkpoints

PDF processing explicitly acquires a `processing` claim. Five coarse checkpoints
record intent: `extraction`, `chunk_insertion`, `vector_cleanup`, `activation`,
and `obsolete_cleanup`. Each records the preceding acknowledged stage using the
existing token/schema/sequence guard and expected KB scope. Local sequence advances
only after an acknowledged diagnostic update. There are no per-page/chunk writes.

A diagnostic stage is intended work, not evidence that it was dispatched or
completed. An unconfirmed checkpoint stops subsequent stages and retains the
claim; cancellation propagates without failed-state compensation. Proven local
extraction failure still publishes the existing safe failure state and releases.
Successful activation and fixed-generation cleanup retain their existing semantics;
the final exact-token release removes claim and diagnostics together, without a
separate diagnostic completion write. Other lifecycle integrations remain deferred.

Required checkpoint writes add failure boundaries and may retain a claim even
before chunk insertion. This is deliberate under uncertain acknowledgement, not
proof of abandonment. Metadata is not a heartbeat, lease, or recovery authorization.

## Phase 18C-C, Part 2B - indexing diagnostic checkpoints

Embedding generation explicitly acquires an `indexing` claim. After validation and
the initial generating-state publication, seven coarse intent checkpoints are
recorded: `encoding`, `collection_setup`, `vector_cleanup`, `indexing_metadata`,
`vector_replacement`, `count_verification`, and `final_publication`. The first
encoding acknowledgement means the first batch completed; later worker-side
encoding interleaves with upserts under the single vector-replacement checkpoint.
There are no per-batch diagnostic writes. Count verification starts only after all
batches finish, and final publication starts only after both count checks pass.

Each checkpoint uses the existing exact-token, owner, KB, schema and sequence
guards. Local sequence advances only after acknowledgement. Intent does not prove
dispatch or completion; the last acknowledged stage records only completed work.
Uncertain diagnostic writes stop subsequent work and retain the claim without
failed-state publication, replay, or unlock. Cancellation propagates. Existing
Qdrant uncertainty and bulk stop/not-attempted behavior remain in effect.

Successful Indexed publication and justified local-failure publication remove
both claim and diagnostics in the same guarded MongoDB update. There is no separate
cleanup write after publication uncertainty. Legacy retained claims stay protected.
These required diagnostic writes add availability boundaries; retained claims may
need service review. Native worker encoding remains non-interruptible until it returns.
No heartbeat, lease, automatic recovery, expiration, or crash/failover guarantee is added.

## Phase 18C-C, Part 2C - deletion and vector-inspection diagnostics

Deletion acquires a `deletion` claim. For documents with recorded vectors, intent
checkpoints follow the existing cleanup order: `vector_invalidation`,
`vector_deletion`, `pdf_deletion`, `chunk_deletion`, `reservation_release`, then
`metadata_deletion`. Documents without recorded vectors skip the first two stages.
Each checkpoint records only the preceding acknowledged work, with exact-token,
owner, KB, schema and monotonically advancing sequence guards. No per-chunk or
per-vector diagnostics are written. Successful final deletion removes the document,
claim and diagnostics together; no completion write follows it.

The existing `/vector-status` path acquires a `vector_inspection` claim and records
one `inspection` intent checkpoint. It retains its existing count reads and response
shape, with no repairs or vector mutations. Safe completion atomically releases the
claim and diagnostics. An uncertain diagnostic write or interrupted/failed remote
count inspection conservatively retains the claim, so a transient read failure can
require service review. No extra finalization checkpoint or Qdrant calls are added.
The separate Phase 18B `/operation-status` read-only endpoint remains unchanged.

Diagnostic acknowledgement loss stops downstream work without replay or unlock.
Deletion retains its claim after destructive dispatch, including cancellation while
a filesystem worker may still run. Proven local path-validation failure before
diagnostic/destructive dispatch retains the existing safe release behavior.
Legacy claims stay protected. These fields are not worker liveness, heartbeats,
leases, or recovery authorization. No expiry, force unlock, or automatic recovery
is introduced; partial deletion and crash/failover limitations remain.
