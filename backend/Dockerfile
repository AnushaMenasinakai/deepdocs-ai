# syntax=docker/dockerfile:1.7
# Build from the repository root: docker build -t deepdocs-backend:docker-a backend
# Linux amd64 CPU image; other architectures require separate verification.
FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HOME=/home/app/.cache/huggingface \
    XDG_CACHE_HOME=/home/app/.cache

WORKDIR /app/backend
COPY requirements.txt docker/requirements-cpu.txt ./
RUN test "$(dpkg --print-architecture)" = amd64 \
    && python -m pip install --index-url https://pypi.org/simple --only-binary=:all: -r requirements-cpu.txt -r requirements.txt \
    && python -m pip check

# Only source modules are copied. Local secrets, PDFs and caches are excluded.
COPY *.py ./
COPY docker/package_model.py /tmp/package_model.py
RUN python /tmp/package_model.py && rm /tmp/package_model.py

ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p storage/documents /home/app/.cache/huggingface \
    && chown app:app storage/documents /home/app/.cache /home/app/.cache/huggingface \
    && chmod -R a+rX,a-w .cache/embeddings \
    && chmod 0750 storage/documents
USER 10001:10001

# Build fails if the real provider cannot load the pinned cache offline as app.
RUN --network=none python -c "import main, pymupdf, argon2, torch; from config import EmbeddingSettings; from embeddings import load_model, model_dimension; assert torch.version.cuda is None; model = load_model(EmbeddingSettings.model_name); assert model_dimension(model) == 384; assert model.encode(['Offline packaging verification'], normalize_embeddings=True).shape == (1, 384)"

EXPOSE 8000
# No health probe here: existing liveness is not complete service readiness.
# Persistent PDF storage must be attached in Docker-B before real uploads.
# Direct access: do not let forwarded headers rewrite authentication limiter identity.
# A future reverse proxy requires an explicit, verified trusted-proxy policy.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
