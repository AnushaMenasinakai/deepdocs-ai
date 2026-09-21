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
