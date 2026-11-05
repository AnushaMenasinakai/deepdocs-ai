# syntax=docker/dockerfile:1.7
# Build context: frontend/ (see DOCKER.md). Digests verified via Docker Hub.
FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS builder
WORKDIR /app
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html vite.config.js ./
COPY config/ ./config/
COPY src/ ./src/
COPY public/ ./public/
# Public browser configuration, never credentials. Changing this requires a rebuild.
ARG VITE_API_URL=http://127.0.0.1:8000
RUN VITE_API_URL="${VITE_API_URL}" npm run build

FROM nginx/nginx-unprivileged:1.30.6-alpine-slim@sha256:016193c18ef1ce19dc2229d3305f69af1b05e0bc0e8fe6ee724a58bd305f3855 AS runtime
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /app/dist/ /usr/share/nginx/html/
USER 101:101
EXPOSE 8080
STOPSIGNAL SIGQUIT
# Static configuration needs none of the base image's runtime template scripts.
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
