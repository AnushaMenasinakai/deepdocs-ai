# DeepDocs AI

AI-Powered Document Intelligence & Advanced RAG Platform.

Phase 1A implements only a React page that checks a FastAPI health endpoint. No AI or document-processing features are implemented.

Stack: React, JavaScript, Vite, Python 3.13.15 (64-bit), FastAPI, Uvicorn.

## Setup

Run all commands from `E:\deepdocs-ai` in PowerShell. Use the existing system Python; do not install another runtime.

```powershell
python --version
python -m pip --version
& 'C:\Users\anush\AppData\Local\Programs\Python\Python313\python.exe' -m venv backend/.venv
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

`backend/main.py` is the only application Python file: it creates FastAPI, configures CORS, and defines the health endpoint. Dependencies are listed in `backend/requirements.txt`. The virtual environment is excluded from Git.

`npm.cmd` works in PowerShell without changing script execution policy.
