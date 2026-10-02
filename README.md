# DeepDocs AI

AI-Powered Document Intelligence & Advanced RAG Platform.

Phase 1 provides the dashboard, knowledge-base and document empty states, and Ask DeepDocs preview. Phase 2A adds backend MongoDB configuration and connectivity only. No AI or document-processing features are implemented.

Stack: React, JavaScript, Vite, Python 3.13.15 (64-bit), FastAPI, Uvicorn.

## Setup

Run all commands from `E:\deepdocs-ai` in PowerShell. Use the existing system Python; do not install another runtime.

```powershell
python --version
python -m pip --version
# Preserve the existing backend/.venv; do not recreate it.
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd --prefix frontend ci
```

## Run

Backend, in one terminal:

```powershell
.\backend\.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
```

Frontend, in another terminal:

```powershell
npm.cmd --prefix frontend run dev
```

Open http://127.0.0.1:5173. The page calls http://127.0.0.1:8000/api/health directly and displays the connection status. CORS permits the local Vite origins on port 5173. Refresh the page after restarting the backend.

Expected health response:

```json
{"status":"ok","message":"DeepDocs AI API is running"}
```

Production build:

```powershell
npm.cmd --prefix frontend run build
```

Build output is in `frontend/dist`. Deployment configuration is outside this phase; the API URL currently targets the local backend.

`backend/main.py` creates FastAPI, configures CORS, and defines health endpoints. `backend/config.py` loads settings and `backend/database.py` manages the MongoDB lifecycle and reusable database dependency. Dependencies are listed in `backend/requirements.txt`. The virtual environment is excluded from Git.

`npm.cmd` works in PowerShell without changing script execution policy.

## MongoDB configuration (Phase 2A)

Install the updated requirements into the existing virtual environment using the setup command above, in a terminal that can execute the project Python. Do not recreate the environment.

Create a local, untracked `backend/.env` based on `backend/.env.example`, and privately set `MONGODB_URI` to your MongoDB Atlas connection string. No working URI is supplied by this project. `MONGODB_DB_NAME` defaults to `deepdocs_ai`; the example sets that name explicitly. Use an Atlas database account with the permissions needed for that database and allow your machine's network address in Atlas. Never put credentials in tracked files.

Configuration reads `backend/.env` relative to the module, independent of the shell's working directory. Process environment variables override file values. Empty or missing `MONGODB_URI` raises a configuration error; an explicitly blank database name is invalid. Restart the backend after changing configuration. URI values are excluded from the settings representation, and driver errors are not returned or logged.

One official PyMongo `AsyncMongoClient` is created per FastAPI application lifespan (per worker), with TLS certificate and hostname verification enabled. It selects the configured database and attempts a bounded ping on startup. Requests reuse this client, which is awaited closed in lifespan cleanup. Health checks have a five-second deadline; connection, socket and server-selection timeouts are also configured. No collections, documents, or indexes are created. Selecting a database and pinging it do not persist a database in Atlas.

`GET /api/health` remains a liveness check and preserves its original 200 response, independently of MongoDB readiness. Configuration errors are logged as safe messages, while the application stays available. A database health request returns:

- **200:** `{"status":"ok","message":"MongoDB is reachable"}`
- **503, missing URI:** `{"detail":"MONGODB_URI is required. Set it in the environment or backend/.env."}`
- **503, connection failure:** `{"detail":"MongoDB is unavailable. Check configuration and Atlas connectivity."}`

The separate endpoint is `GET /api/health/database`. Each call pings the selected database; a failed startup connection can recover through the same client without restarting. Invalid configuration requires fixing the settings and restarting. Ping verifies reachability, not collection read/write permissions. Future backend routes can obtain the shared database through `Depends(get_database)` from `database.py`.

After starting the backend using the existing command above, verify locally:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/health/database
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

The unittest suite uses mocked clients and does not connect to Atlas. It covers missing settings, secret-safe errors, unchanged liveness, shared-client reuse, shutdown, and connection recovery.

During Phase 2A implementation, Codex could not execute the existing Python due to sandbox access restrictions. Dependency installation, imports, HTTP/runtime tests, and live MongoDB connectivity were **not verified**. No `backend/.env` or process `MONGODB_URI` was available. The tests must be run outside that restricted environment before runtime verification can be considered complete.

## Registration API (Phase 2B)

Install the updated requirements into the existing virtual environment from a terminal where project Python is accessible. Start the backend with the existing Uvicorn command. No frontend registration UI or login/token behavior is included.

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

The existing local frontend CORS origins now permit POST as well as GET. Frontend source is unchanged.

Offline verification from the repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
```

The suite patches configuration and MongoDB clients, never loads the real `backend/.env`, and never modifies Atlas. Registration tests cover input validation, normalization, safe responses, duplicate-key handling (including simulated concurrency), unavailable database/index, lifecycle index setup, and real local Argon2id hashing. Mocked concurrency is not a substitute for manual Atlas index verification.

Phase 2A Atlas connectivity was manually verified outside Codex. For Phase 2B, Python execution in Codex still returns access denied; dependency installation, imports, tests, and actual registration/index creation remain pending external runtime verification. No Atlas connection or write was attempted during implementation.

## Login and current user (Phase 2C)

Set `JWT_SECRET_KEY` privately in the runtime environment or local `backend/.env` to a cryptographically random secret of at least 32 bytes. The example contains a rejected placeholder, not a usable key. `JWT_ALGORITHM` defaults to and permits only `HS256`; `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` defaults to 30 (allowed range 1–1440). Environment variables override file settings. JWT configuration is validated on authentication use; invalid configuration returns a safe 503 and does not disable registration or health checks.

- `POST /api/auth/login`: JSON `email` and `password`, with the same normalization/validation as registration. Returns `access_token` and `token_type: "bearer"`. Unknown accounts, wrong passwords, and corrupt stored hashes share a generic 401; invalid request structure/format returns 422.
- `GET /api/auth/me`: requires `Authorization: Bearer <access_token>`. Returns only `id`, `name`, `email`, and `created_at`. Invalid, expired, or deleted-user tokens return 401 with `WWW-Authenticate: Bearer`; database failures return safe 503 responses.

Tokens contain only `sub` (MongoDB user ID), `iat`, and `exp`. They are signed, not encrypted. No refresh tokens, frontend authentication, or logout flow is included. Install the new dependency and run offline tests locally:

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

Startup creates the `knowledge_bases_owner_updated` index on owner_id ascending, updated_at descending, and _id descending. Failure is logged without driver details and retried on restart; this performance index is not required for ownership enforcement. No documents, uploads, vectors, or cascade cleanup are implemented.

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

Offline tests use temporary directories under ignored .verification/ (created by the suite when absent) and mocked MongoDB; they do not connect to Atlas. No PDF extraction, page processing, chunking, embeddings, search, RAG, or document frontend is implemented.

## Document library UI (Phase 3D)

The protected Documents page now lists the selected Knowledge Base's PDFs and supports single-file upload and confirmed deletion. It automatically selects the first available Knowledge Base and links to Knowledge Bases when none exist. The UI checks file extension, available MIME type, non-empty content, and the 10 MiB limit; backend validation remains authoritative. Status "Uploaded" confirms storage only. PDF processing, extraction, chunking, embeddings, Qdrant, and RAG are not implemented.

## PDF processing pipeline (Phase 4)

Processing is available through Swagger/API only:

- POST /api/documents/{document_id}/process processes an owned stored PDF and returns document metadata including page_count, chunk_count, processed_at, and a safe processing_error.
- GET /api/documents/{document_id}/chunks returns the owned document's active chunk set in chunk_index order.

Both endpoints require Bearer authentication. Cross-user resources return 404. Invalid IDs return 422; concurrent process/delete operations return 409; parser/no-text failures return 422; unavailable storage/database services return safe 503 responses.

Install pymupdf==1.28.2 using backend/requirements.txt in the existing environment. PyMuPDF extracts text one page at a time in sorted reading order; human-facing page numbers begin at 1. Empty pages count toward page_count but create no chunks. Image-only/no-text and password-protected or unreadable PDFs fail safely. No OCR is implemented.

Normalization only standardizes newlines and horizontal whitespace, reduces excess blank lines, and trims whitespace. It preserves line/list structure, punctuation, and numbers; it does not join ambiguous wrapped lines or rewrite text. Chunks stay within one page, prefer paragraph then sentence then whitespace boundaries, and overlap at whole-word boundaries. Defaults: PDF_CHUNK_TARGET=1000 and PDF_CHUNK_OVERLAP=150 characters. Small tails can extend a chunk up to target + overlap; an indivisible long word may exceed the target rather than being cut. Indices begin at 0 across the document. Character counts describe cleaned chunk text, not byte offsets in the original PDF.

The document_chunks collection stores ObjectId document/Knowledge Base/owner references, source filename, text, chunk order, page_start/page_end, character_count, and UTC created_at. A private generation identifier separates staged and active data. Neither owner IDs nor generation/storage internals are exposed by the chunk API. A compound index supports owner + document + generation + chunk order.

Lifecycle: uploaded → processing → processed, or failed. Reprocessing preserves the last successful generation and its counts/timestamp until all replacement chunks have been inserted and a single metadata update activates the replacement. Failed extraction/insertion marks the attempt failed and preserves the old active chunks; failed-stage chunks are cleaned up where possible. Inspection returns only the active generation, including the last successful one after a failed reprocessing attempt. Deleting a document removes all its owned chunk generations and its PDF. Knowledge Base deletion still requires deleting documents first.

No cross-collection transaction or background queue is used. An uncertain activation acknowledgement preserves generations and the operation lock for manual reconciliation; metadata may remain processing until reviewed. Cleanup failures can leave inactive generations, which inspection never returns. Process termination can leave a stale operation lock. Reconcile only when no operation is running; do not blindly delete the active generation. A failed deletion can leave retryable metadata with a missing PDF or chunks and a safe failure message.

This intentionally synchronous local pipeline runs native PyMuPDF extraction on the event-loop thread, not concurrently in worker threads; other requests on that worker may wait during extraction. Configurable limits are PDF_MAX_PAGES=1000, PDF_MAX_TEXT_CHARACTERS=5000000, and PDF_MAX_CHUNKS=20000. They bound accepted output but are not a hard memory/time sandbox for native PDF decompression.

The frontend only displays uploaded/processing/processed/failed statuses; processing actions and chunk inspection remain API-only. No embeddings, embedding models, Qdrant, vector/search pipeline, Gemini, retrieval, RAG, or citation UI is implemented.


## Embeddings foundation (Phase 5)

POST /api/documents/{document_id}/embeddings requires Bearer authentication and ownership. It accepts no model/path/body parameters. The document must be processed with a nonempty, complete active chunk generation. It returns document_id, chunk_count, embedding_model, embedding_dimension, and status (generated), never full vectors or internal ownership/storage fields. Missing/cross-user documents return 404, malformed IDs return 422, unprocessed/empty documents and concurrent operations return 409, and configuration/model/vector/database failures return sanitized 503 responses.

The dependency is sentence-transformers==6.1.0. A pip binary-only dry run resolved it together with the existing backend requirements on Windows Python 3.13.15, including compatible PyTorch wheels; this verifies dependency resolution, not real inference. Install the updated backend/requirements.txt locally before live verification. Unit tests use a fake model and never download model weights or contact Atlas.

The default model is sentence-transformers/all-MiniLM-L6-v2 (384 dimensions). EMBEDDING_MODEL_NAME configures a server-controlled Hugging Face repository ID; EMBEDDING_BATCH_SIZE defaults to 32 (allowed 1–128). Dimensionality is read from the model rather than hardcoded. The model is lazily imported/loaded on the first embedding request, reused per process, CPU-only, with remote custom code disabled. Changing the configured model replaces the single cached model. Downloads go to ignored backend/.cache/embeddings; first use needs network access and sufficient memory/disk space. Normal startup does not import Sentence Transformers or load weights.

Embedding chunk texts in chunk_index order preserves their existing document/page provenance for later storage. Each bounded batch produces normalized dense vectors; every vector must be nonempty, have the model's exact dimension, and contain only finite real numbers (no strings, booleans, NaN, or infinity). Batch output counts must match inputs. Validated vectors are discarded after each batch: no full vectors are stored in MongoDB, returned by the API, or stored in Qdrant. The model's tokenizer length limit still applies (the default MiniLM model truncates inputs beyond 256 word pieces); character-based Phase 4 chunks do not guarantee a token limit. Stored text and page provenance remain unchanged.

A single documents.embedding object records the outcome for all chunks of its chunk_generation: status, model, dimension, chunk_count, embedded_at (UTC), and a safe error. Missing metadata means not_generated. Successful metadata means vectors were generated and validated in memory, not that durable vectors are available. States are not_generated, generating, generated, and failed. No per-chunk writes or extra MongoDB records/indexes are needed: a chunk inherits success only when its generation matches that complete document-level result. Repeated calls regenerate and replace this metadata; they never duplicate chunks. Starting Phase 4 reprocessing resets embedding metadata, including when that reprocessing later fails.

Embedding generation shares the existing atomic document-operation claim with processing/deletion. All vectors must validate before one guarded document update publishes success and releases the lock. Failures record a safe failed state when the database permits; no partial batch is published. Document deletion naturally removes this metadata with the document and still removes owned chunks/PDFs. The Knowledge Base deletion policy is unchanged.

This synchronous local verification pipeline may block its worker while loading/encoding. It has no queue, cancellation worker, or automatic lock recovery. A crash or persistent database failure may leave generating plus an operation lock; reconcile only when no operation is running. If the database applied the final success update but its acknowledgement was lost, the client can receive 503 while valid generated metadata exists; retrying is safe. No vectors survive either outcome. Real model download/inference and Atlas metadata persistence still require manual live verification.

There is no embeddings UI, Qdrant integration, vector/search/retrieval pipeline, reranking, Gemini, RAG, or question answering. Qdrant integration belongs to Phase 6, which has not started.


## Qdrant vector persistence (Phase 6)

Phase 6 extends the Phase 5 endpoint; it replaces the earlier in-memory-only outcome. Install backend/requirements.txt (adds qdrant-client==1.19.1). A binary-only pip dry run verified compatibility with Python 3.13.15, sentence-transformers==6.1.0, and the other pinned backend dependencies. The async official client is created lazily, reused per application process, and closed at shutdown. No Qdrant connection or model download is required at normal startup.

Set QDRANT_URL and, for Cloud, QDRANT_API_KEY privately in backend/.env or the runtime environment. QDRANT_COLLECTION_NAME defaults to deepdocs_chunks. HTTPS Cloud URLs and HTTP localhost/127.0.0.1/IPv6 loopback are supported; embedded credentials, query parameters, fragments, and URL paths are rejected. Real configuration is never returned/logged. Restart the backend after changing Qdrant configuration. The example contains no usable credential.

The collection uses one unnamed dense vector, cosine distance, and the dimension supplied by the embedding provider (384 for all-MiniLM-L6-v2). Missing collections are created; existing dimensions/distance/named-vector mismatches return safe 503 without deleting or recreating the collection. Keyword payload indexes cover owner_id, knowledge_base_id, document_id, and chunk_generation for Cloud-compatible filtered cleanup/counting.

Each point has a deterministic UUIDv5 derived from owner, Knowledge Base, document, chunk generation, and chunk ID. Payload fields are owner_id, knowledge_base_id, document_id, chunk_id, chunk_generation (all strings), chunk_index, source_filename, page_start, page_end, and text. Text is deliberately included for future retrieval without one MongoDB lookup per candidate. No paths, credentials, authentication data, or full environment configuration enters payloads. MongoDB remains authoritative for application/chunk lifecycle; Qdrant stores vectors and retrieval payloads, not application ownership policy.

POST /api/documents/{document_id}/embeddings requires JWT/ownership and a successfully processed, nonempty active chunk set. It claims the existing operation lock, marks indexing in MongoDB, generates/validates ordered batches, checks collection compatibility, removes previous owned document points, upserts deterministic IDs with wait=True, and checks exact total/current-generation point counts before publishing success. The response preserves status=generated and adds vector_status=indexed, vector_store=qdrant, and collection_name. Full vectors never enter MongoDB or API responses.

The documents.vector_index object tracks not_generated/indexing/indexed/failed/stale, model, dimension, active chunk generation, count, collection, and indexed_at. A private endpoint fingerprint prevents accidental cleanup against a different Qdrant deployment. Repeated indexing replaces owned document points rather than accumulating generations. Changing collection on the same endpoint cleans the old recorded collection first; moving endpoints requires restoring the old configuration for cleanup before migration. The lightweight Phase 5 embedding metadata remains, but success is now published only after persistence verification.

Reprocessing marks vector state stale immediately. After replacement chunks have been built/staged, it deletes all old document points before activating the new chunk generation. Cleanup failure returns 503, preserves the last active chunk set, records failed processing/stale vector state, and keeps the cleanup location for retry. Successful reprocessing leaves no old points and resets vector state to not_generated. Failed extraction can retain old physical vectors, but they are explicitly stale and must never be treated as current.

Document deletion marks vector state stale and confirms scoped Qdrant deletion before removing the PDF, chunks, reservation, and MongoDB document through the existing retryable deletion flow. If Qdrant is unavailable, documents with recorded vector state remain in MongoDB with their files/chunks for retry; documents never sent to Qdrant can still be deleted. Every count/delete filter includes authenticated owner, document, and Knowledge Base. The Knowledge Base nonempty conflict rule is unchanged.

GET /api/health/qdrant is a lightweight connectivity check returning status=ok or a sanitized 503; it does not load the embedding model or promise collection compatibility. GET /api/documents/{document_id}/vector-status requires JWT/ownership, uses the document-operation lock, and returns document_id, status, collection_name, expected_chunk_count, stored_vector_count, and synchronized. Synchronization requires indexed metadata for the active generation plus matching current and total counts. It returns neither payload text nor vectors and performs no similarity search.

There is no distributed transaction across MongoDB, Qdrant, and files. Partial/uncertain upserts can leave points with failed/indexing MongoDB state; retry, reprocess, or deletion cleans them using the retained location. A lost final MongoDB acknowledgement can return 503 despite valid indexed state. Crashes, persistent MongoDB failures, or delayed remote operations require reconciliation while no operations are running; do not blindly clear operation locks. Exact counts and completed write acknowledgements are verification, not a distributed atomicity guarantee or a point-by-point content audit. Future retrieval must enforce ownership and MongoDB active-generation/indexed-state eligibility rather than treating every raw Qdrant point as usable. The chosen retry strategy does not preserve continuous availability of an older vector set.

Tests fake the Qdrant client and embedding provider; an additional official-client in-memory test checks IDs/upsert/count/delete without a server. Real Cloud credentials/connectivity, real-model indexing into Cloud, reprocessing cleanup, and deletion still require manual live verification. No frontend changes, semantic/vector search endpoints, query embeddings, Gemini, RAG, chat, or Phase 7 are implemented.


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

This phase uses the maintained official google-genai==2.25.0 SDK, not google-generativeai. A binary-only pip dry run resolved the SDK alongside every existing requirement on Python 3.13.15. Install backend/requirements.txt locally. Set GEMINI_API_KEY privately in backend/.env or the runtime environment; GEMINI_MODEL defaults to gemini-3.1-flash-lite, a currently stable, low-latency/cost-effective model suited to short text-grounded answers. No fallback model is used. Model documentation: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite . The SDK/client is loaded only after usable context exists, reused per process, and closed at shutdown. Restart to apply changed Gemini settings.

The RAG service calls the existing Phase 7 retrieval service with server-controlled top_k=5. Owner/Knowledge Base filters, active-generation checks, authoritative chunk validation, and existing query embedding validation are unchanged. No processing or indexing occurs. Queries, query vectors, prompts, and answers are not persisted by DeepDocs and are not application-logged. Only the question and selected document reference data are sent to the configured Gemini service; runtime credentials are never part of the prompt. Google's service-side data handling is separate from application persistence.

RAG_MIN_RELEVANCE_SCORE defaults to 0.50 (configurable finite range 0–1). This is an intentionally conservative, initial positive cosine-similarity heuristic for the existing normalized MiniLM retrieval, not a calibrated probability or a conclusion drawn from one live sample. It may reject answerable questions and admit irrelevant ones; Phase 11 must evaluate recall, unsupported-answer rates, and domain/language behavior before calibration. Phase 7 search remains threshold-free. Only hits meeting this guard can enter the prompt; no qualifying hits means deterministic insufficient_context without creating/calling Gemini, even when its key is absent.

RAG_MAX_CONTEXT_CHARS defaults to 12000 (allowed 1000–30000) and bounds the complete serialized reference-data array including provenance overhead. A deterministic context builder preserves relevance order, deduplicates chunks, and includes complete chunks that fit; oversized chunks are skipped, never rewritten or sliced. JSON encoding preserves Unicode/source text, and malformed records are skipped. Document/chunk IDs, filename, and page range are retained internally. Phase 9 exposes only the document ID, filename, and page range for included context sources. The separate question is bounded to 1000 characters in addition to this context budget.

The fixed system_instruction requires answers only from retrieved context: no outside knowledge, invented facts, unsupported claims, claimed actions, or document-injected instructions. A JSON user message explicitly separates USER_QUESTION and RETRIEVED_DOCUMENT_CONTEXT; document strings cannot replace the system_instruction field. All document/question content is untrusted data, including fake role markers or requests to reveal keys. There are no tools, function calls, external grounding, agents, or streaming. This is defense in depth, not proof that a generative model will always obey; malicious-content tests verify structural separation, not immunity to every prompt injection.

Gemini is requested to return structured supported/answer JSON. A completed, unblocked single candidate is required. Empty, malformed, tool-call, truncated, blocked, oversized, invalid-Unicode/control-character, and known-key/internal-metadata responses are rejected safely. Common absolute filesystem-path patterns and prompt-envelope echoes are also rejected; these guards are not a general-purpose sensitive-data classifier. Maximum output is 1024 tokens and the validated answer is capped at 4000 characters. Calls have a 30-second deadline, a single SDK attempt, and no fallback. Provider/network/key/quota/model/response errors map to sanitized 503, never raw SDK exceptions.

Successful public response: status=answered, answer (plain text), retrieved_chunk_count (number of complete chunks supplied to Gemini). If no usable context exists, status=insufficient_context, retrieved_chunk_count=0, and answer is always: I couldn't find enough relevant information in this Knowledge Base to answer that question. Gemini can also declare the supplied context insufficient; that returns the same fixed message with the number of chunks it considered, not a provider-written refusal. Relevance alone is never treated as proof of answer support.

Current limitations: generation is instructed to be grounded but there is no independent entailment verifier; fluency, schema validity, or the model's supported flag cannot prove factual support. Evaluate live answers against PDFs, especially on relevant-but-unanswerable and malicious-content questions. Retrieval/answering is not an atomic cross-service snapshot, and a document may change after context retrieval. Existing model token truncation and synchronous embedding limits still apply. Very long complete chunks can exhaust the budget and cause abstention.

Automated verification uses fake Gemini, fake inference/Qdrant, synthetic PDFs, and the existing offline regressions. Live Google API/model access and answer grounding still require manual Swagger verification. Answers are generated under a context-only instruction; no final/inline citations, citation UI, chat/conversation history, persistent answer history, hybrid search, BM25, or reranking is implemented. Phase 9 adds context-source references and the Ask DeepDocs UI below; these are not sentence-level citations.


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

The browser validates response shape and public page metadata before presentation. Backend/provider exception details are never displayed. Only the question is sent by the form; clients cannot control ownership, retrieval filters, model, prompt, threshold, or context budget. Existing secrets remain backend-only. Questions/answers are not persisted and no conversational memory, chat history, streaming, hybrid search, BM25, or reranking is added. Phase 10 is not implemented.

Offline UI verification: with the frontend running on port 5176, run `node frontend/tests/ask.browser.cjs`. Supply `PLAYWRIGHT_MODULE` for an existing Playwright installation if it is not locally available, and optionally `FRONTEND_TEST_URL`. The suite uses Edge headless, mocks all APIs, and writes ignored screenshots under `.verification`; it does not contact Atlas, Qdrant, or Gemini. Live answer/source correctness still requires comparing the real UI's answers and page references against the uploaded PDF.
