"""Offline configuration and real-app ASGI CORS checks; no lifespan or DB calls."""
import asyncio
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from config import ConfigurationError, load_cors_origins

LOCAL = ["http://127.0.0.1:5173", "http://localhost:5173"]


def configured(value=None, app=False):
    values = {} if value is None else {"CORS_FRONTEND_ORIGINS": value}
    with patch("config.dotenv_values", return_value=values), patch.dict(os.environ, {}, clear=True):
        return runpy.run_path(str(BACKEND / "main.py"))["app"] if app else load_cors_origins()


def request(app, origin=None, preflight=False, method="POST"):
    async def run():
        messages = []
        headers = [] if origin is None else [(b"origin", origin.encode())]
        if preflight:
            headers += [(b"access-control-request-method", method.encode()),
                        (b"access-control-request-headers", b"authorization,content-type")]

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                   "method": "OPTIONS" if preflight else "GET", "scheme": "http",
                   "path": "/api/health", "raw_path": b"/api/health", "query_string": b"",
                   "root_path": "", "headers": headers, "server": ("test", 80),
                   "client": ("test", 123)}, receive, send)
        start = next(m for m in messages if m["type"] == "http.response.start")
        return start["status"], dict(start["headers"])
    return asyncio.run(run())


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_production_origins_keep_local_only(value):
    assert configured(value) == LOCAL


@pytest.mark.parametrize("origin", LOCAL)
@pytest.mark.parametrize("preflight", [False, True])
def test_existing_local_origins(origin, preflight):
    status, headers = request(configured(app=True), origin, preflight)
    assert status == 200
    assert headers[b"access-control-allow-origin"] == origin.encode()
    assert b"access-control-allow-credentials" not in headers


@pytest.mark.parametrize("value", ["https://app.example.com", "https://app.example.com, https://preview.example.com:8443"])
def test_configured_origins_simple_and_preflight(value):
    app = configured(value, app=True)
    for origin in value.split(","):
        origin = origin.strip()
        for preflight in [False, True]:
            status, headers = request(app, origin, preflight)
            assert status == 200
            assert headers[b"access-control-allow-origin"] == origin.encode()
            assert b"Origin" in headers[b"vary"]
            assert b"access-control-allow-credentials" not in headers
            if preflight:
                assert headers[b"access-control-allow-methods"] == b"GET, POST, PATCH, DELETE"
                assert headers[b"access-control-allow-headers"] == b"authorization,content-type"


@pytest.mark.parametrize("origin", ["https://evil.example.com", "https://app.example.com.evil.test", "http://app.example.com", "https://app.example.com:444", "null"])
def test_disallowed_origin_not_reflected(origin):
    app = configured("https://app.example.com", app=True)
    for preflight in [False, True]:
        status, headers = request(app, origin, preflight)
        assert status == (400 if preflight else 200)
        assert b"access-control-allow-origin" not in headers
        assert b"access-control-allow-credentials" not in headers


def test_no_origin_and_unapproved_method():
    app = configured(app=True)
    status, headers = request(app)
    assert status == 200 and b"access-control-allow-origin" not in headers
    status, _ = request(app, LOCAL[0], True, "PUT")
    assert status == 400


@pytest.mark.parametrize("value", ["*", "https://*.example.com", "null", "example.com", "ftp://example.com",
    "https://example.com/", "https://example.com/path", "https://example.com?", "https://example.com?q=x",
    "https://example.com#", "https://user:private@example.com", "https://example.com:0", "https://example.com:65536",
    "https://example.com:", "https://example.com:abc", "https://bad_host", "https://-bad.example",
    "https://example..com", "https://example.com.", "https://999.1.1.1", "https://[::zzz]",
    "https://exa\nmple.com", "https://example.com\\evil", ",", "https://good.example,", ",https://good.example"])
def test_malformed_origins_reject_entire_configuration(value):
    with pytest.raises(ConfigurationError) as error:
        configured(value)
    assert str(error.value).startswith("CORS_FRONTEND_ORIGINS must contain")
    assert "private" not in str(error.value)


def test_invalid_configuration_prevents_app_construction():
    with pytest.raises(ConfigurationError):
        configured("https://good.example,*", app=True)


def test_duplicates_and_browser_normalization():
    assert configured("https://APP.example:443, https://app.example,http://localhost:5173,http://example.com:80,http://[::1]:8080") == [
        *LOCAL, "https://app.example", "http://example.com", "http://[::1]:8080"]


def test_environment_precedence_and_no_dotenv_interpolation():
    with patch("config.dotenv_values", return_value={"CORS_FRONTEND_ORIGINS": "https://file.example"}) as dotenv:
        with patch.dict(os.environ, {"CORS_FRONTEND_ORIGINS": "https://env.example"}, clear=True):
            assert load_cors_origins() == [*LOCAL, "https://env.example"]
        dotenv.assert_called_with(BACKEND / ".env", interpolate=False)


def test_unreadable_configuration_fails_safely():
    with patch("config.dotenv_values", side_effect=OSError("private-path")):
        with pytest.raises(ConfigurationError) as error:
            load_cors_origins()
    assert "private-path" not in str(error.value)


@pytest.mark.parametrize("origin", [LOCAL[0], "https://evil.example.com", None])
def test_admission_retry_header_exposure_preserves_origin_policy(origin):
    from fastapi.middleware.cors import CORSMiddleware
    from expensive_admission import capacity_unavailable
    from httpx import ASGITransport, AsyncClient

    app = configured(app=True)
    cors = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert cors.kwargs["expose_headers"] == ["Retry-After"]
    assert cors.kwargs["allow_origins"] == LOCAL
    assert cors.kwargs["allow_methods"] == ["GET", "POST", "PATCH", "DELETE"]
    assert cors.kwargs["allow_headers"] == ["*"]
    assert not cors.kwargs.get("allow_credentials", False)

    @app.get("/test-admission")
    async def saturated():
        raise capacity_unavailable()

    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {} if origin is None else {"Origin": origin}
            busy = await client.get("/test-admission", headers=headers)
            assert busy.status_code == 503
            assert busy.json() == {"detail": "Service is busy. Please try again later."}
            assert busy.headers["retry-after"] == "1"
            assert busy.headers["cache-control"] == "no-store"
            if origin in LOCAL:
                assert busy.headers["access-control-allow-origin"] == origin
                assert busy.headers["access-control-expose-headers"] == "Retry-After"
            else:
                assert "access-control-allow-origin" not in busy.headers
            assert "access-control-allow-credentials" not in busy.headers
            # CORS adds exposure metadata without changing ordinary or auth responses.
            for path in ["/api/health", "/api/auth/me"]:
                plain = await client.get(path)
                cross = await client.get(path, headers=headers)
                assert cross.status_code == plain.status_code == (200 if path.endswith("health") else 401)
                assert cross.json() == plain.json()
                if origin in LOCAL:
                    assert cross.headers["access-control-expose-headers"] == "Retry-After"
    asyncio.run(run())
