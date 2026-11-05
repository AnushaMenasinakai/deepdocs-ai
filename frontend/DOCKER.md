# Frontend container

The image builds the existing Vite application and serves its static output with
unprivileged NGINX on port 8080. No backend proxy or development server is included.
Both pinned base-image indexes include Linux AMD64, matching the backend.

From the repository root (commands are for a later approved build/run phase):

```powershell
docker build --platform linux/amd64 --tag deepdocs-frontend:local ./frontend
docker run --rm --name deepdocs-frontend-local --publish 127.0.0.1:5173:8080 deepdocs-frontend:local
```

The default build argument is `VITE_API_URL=http://127.0.0.1:8000`. This is the API
origin seen by the **browser**, not by NGINX. The backend must allow the frontend
origin `http://127.0.0.1:5173` through its existing CORS configuration. Docker service
names such as `backend` are not browser-facing addresses. Do not append `/api`.

For another deployment, rebuild with its public HTTPS API origin:

```powershell
docker build --platform linux/amd64 --build-arg VITE_API_URL=https://api.example.com --tag deepdocs-frontend:deployment ./frontend
```

Vite embeds this value in public JavaScript. Container runtime environment variables
cannot change it. Never put passwords, tokens or other secrets into Vite variables
or build arguments. Environment files are excluded from the build context.

## Compose startup

For a later approved local run, from the repository root:

```powershell
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose stop
```

Compose builds both services and publishes the frontend at
`http://127.0.0.1:5173` and backend at `http://127.0.0.1:8000`. Its frontend build
argument explicitly uses that loopback API origin. A different deployment must
override that build argument and rebuild; the standalone build example above does
not change Compose configuration. Preserve the backend's explicit
`CORS_FRONTEND_ORIGINS` entry for `http://127.0.0.1:5173`.

Both services join the default Compose network. Browser API traffic uses published
host ports, not that network's service names. MongoDB, Qdrant and Gemini stay external.
The backend configuration file is mounted read-only (default `backend/.env`, or a
separate file selected with `BACKEND_ENV_FILE`); it is not a build input. Startup
uses the configured services and can initialize database indexes, so later isolated
validation must use a dedicated test configuration and separate Compose project.
Do not reuse or delete existing PDF volumes for testing.

The frontend has a 128 MiB runtime memory limit, half a CPU, a 64-process limit,
read-only root filesystem and a 64 MiB writable `/tmp`. These limits do not bound
the Node image build and require runtime validation. It drops Linux capabilities
and prevents privilege escalation. It retries failed process exits at most three
times and allows 30 seconds for graceful shutdown. An unhealthy probe alone does
not restart a container. The backend retains its five-minute shutdown grace period.

Frontend liveness fetches local `index.html` with the image's BusyBox wget. Backend
liveness uses `/api/health`; neither probe checks external services. Frontend startup
does not wait for Gemini or backend readiness. `docker compose stop` preserves PDF
storage; do not use `down --volumes` on data you intend to retain.

The runtime contains built assets, not the builder's Node dependencies. It runs as
UID/GID 101:101, with its PID and temporary files under `/tmp`; that directory must
remain writable (provide a suitably sized tmpfs if using a read-only root filesystem).
NGINX receives SIGQUIT for graceful shutdown. SPA routes fall back to `index.html`;
missing `/assets/` files return 404. HTML requires revalidation, while hashed assets
receive a long cache lifetime. No TLS termination is configured in this local image.

Published metadata used for the pins:

- [Node 24.21.0 bookworm-slim](https://hub.docker.com/v2/repositories/library/node/tags/24.21.0-bookworm-slim)
- [NGINX-maintained unprivileged 1.30.6 alpine-slim](https://hub.docker.com/v2/repositories/nginx/nginx-unprivileged/tags/1.30.6-alpine-slim)

Container build, `nginx -t`, browser deep-link checks and runtime permission checks
remain required during container validation. No container was built for this phase.
