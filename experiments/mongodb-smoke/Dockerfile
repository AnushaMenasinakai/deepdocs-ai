FROM python:3.13.15-slim-bookworm@sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26
RUN pip install --no-cache-dir --only-binary=:all: pymongo==4.18.1 dnspython==2.8.0
WORKDIR /test
COPY backend/document_storage.py backend/storage_contract.py backend/storage_schema.py ./
COPY experiments/mongodb-smoke/smoke.py ./
ENV PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["python", "smoke.py"]
