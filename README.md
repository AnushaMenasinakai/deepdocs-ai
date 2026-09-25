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
