"""Offline login/JWT tests: no real configuration, Atlas connection, or writes."""
import json
import os
import secrets
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jwt
from argon2 import PasswordHasher
from bson import ObjectId
from pymongo.errors import ConnectionFailure, OperationFailure

from auth import create_access_token
from config import ConfigurationError, JWTSettings, load_jwt_settings
from database import get_database
from main import app


async def request(method, path, payload=None, authorization=None):
    messages = []
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-type", b"application/json")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "server": ("test", 80), "client": ("test", 123),
    }, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    response_body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(response_body), dict(start["headers"])


class AuthTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "offline-password"
        cls.stored_hash = PasswordHasher().hash(cls.password)

    async def asyncSetUp(self):
        self.settings = JWTSettings(secrets.token_urlsafe(48))
        self.settings_patch = patch("auth.load_jwt_settings", return_value=self.settings)
        self.settings_patch.start()
        self.collection = SimpleNamespace(find_one=AsyncMock())
        self.database = SimpleNamespace(get_collection=MagicMock(return_value=self.collection))
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_database] = lambda: self.database
        self.user = {
            "_id": ObjectId(), "name": "Example User", "email": "user@example.com",
            "password_hash": self.stored_hash, "created_at": datetime.now(timezone.utc),
        }
        self.collection.find_one.return_value = self.user

    async def asyncTearDown(self):
        self.settings_patch.stop()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)

    async def login(self, **changes):
        payload = {"email": " USER@Example.com ", "password": self.password}
        return await request("POST", "/api/auth/login", {**payload, **changes})

    def signed(self, changes=None, remove=(), key=None, algorithm="HS256"):
        now = int(time.time())
        claims = {"sub": str(self.user["_id"]), "iat": now, "exp": now + 1800}
        claims.update(changes or {})
        for field in remove:
            claims.pop(field, None)
        return jwt.encode(claims, key or self.settings.secret_key, algorithm=algorithm)

    async def assert_unauthorized(self, authorization):
        status, body, headers = await request("GET", "/api/auth/me", authorization=authorization)
        self.assertEqual(status, 401)
        self.assertEqual(headers[b"www-authenticate"], b"Bearer")
        self.assertEqual(body, {"detail": "Invalid authentication credentials"})
        self.assertNotIn(self.settings.secret_key, json.dumps(body))

    async def test_login_normalization_minimal_token_and_no_secrets(self):
        status, body, headers = await self.login()
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"access_token", "token_type"})
        self.assertEqual(body["token_type"], "bearer")
        self.assertEqual(len(body["access_token"].split(".")), 3)
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.collection.find_one.assert_awaited_once_with(
            {"email": "user@example.com"}, {"_id": 1, "password_hash": 1},
        )
        claims = jwt.decode(body["access_token"], self.settings.secret_key, algorithms=["HS256"])
        self.assertEqual(set(claims), {"sub", "iat", "exp"})
        self.assertEqual(claims["sub"], str(self.user["_id"]))
        self.assertEqual(claims["exp"] - claims["iat"], 30 * 60)
        for secret in [self.password, self.stored_hash, self.settings.secret_key]:
            self.assertNotIn(secret, json.dumps(body))
            self.assertNotIn(secret, json.dumps(claims))

    async def test_wrong_password_missing_account_and_corrupt_hash_same_401(self):
        results = []
        results.append(await self.login(password="incorrect-password"))
        self.collection.find_one.return_value = None
        results.append(await self.login())
        self.collection.find_one.return_value = {**self.user, "password_hash": "corrupt-hash"}
        results.append(await self.login())
        self.collection.find_one.return_value = {**self.user, "password_hash": None}
        results.append(await self.login())
        for status, body, headers in results:
            self.assertEqual(status, 401)
            self.assertEqual(body, {"detail": "Invalid email or password"})
            self.assertEqual(headers[b"www-authenticate"], b"Bearer")

    async def test_login_does_not_trim_password(self):
        password = "  offline-password  "
        self.collection.find_one.return_value = {
            **self.user, "password_hash": PasswordHasher().hash(password),
        }
        self.assertEqual((await self.login(password=password))[0], 200)
        self.assertEqual((await self.login(password=password.strip()))[0], 401)

    async def test_validation_422_without_echoed_values(self):
        for changes in [
            {"email": "invalid-address"}, {"password": "pw-123"},
            {"password": "z" * 129}, {"password": None}, {"password": 12345678},
            {"password_hash": "untrusted-value"},
        ]:
            with self.subTest(fields=list(changes)):
                status, body, _ = await self.login(**changes)
                self.assertEqual(status, 422)
                for error in body["detail"]:
                    self.assertNotIn("input", error)
                self.assertNotIn(self.password, json.dumps(body))
                self.assertNotIn("untrusted-value", json.dumps(body))
        self.collection.find_one.assert_not_awaited()

    async def test_login_database_failures_are_503(self):
        for failure in [ConnectionFailure("private-driver-detail"), OperationFailure("private-driver-detail")]:
            self.collection.find_one.side_effect = failure
            status, body, _ = await self.login()
            self.assertEqual(status, 503)
            self.assertNotIn("private-driver-detail", json.dumps(body))

    async def test_valid_me_has_only_public_fields(self):
        token = create_access_token(self.user["_id"], self.settings)
        status, body, _ = await request("GET", "/api/auth/me", authorization="Bearer " + token)
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"id", "name", "email", "created_at"})
        self.assertEqual(body["id"], str(self.user["_id"]))
        self.assertNotIn(self.stored_hash, json.dumps(body))
        self.collection.find_one.assert_awaited_once_with(
            {"_id": self.user["_id"]}, {"_id": 1, "name": 1, "email": 1, "created_at": 1},
        )

    async def test_missing_and_malformed_headers(self):
        for header in [None, "", "Basic token", "Bearer", "Bearer one two"]:
            with self.subTest(header=header):
                await self.assert_unauthorized(header)
        self.collection.find_one.assert_not_awaited()

    async def test_invalid_tokens_and_required_claims(self):
        now = int(time.time())
        tokens = [
            "not-a-jwt", self.signed(key=secrets.token_urlsafe(48)),
            self.signed({"iat": now - 120, "exp": now - 1}),
            self.signed(remove=("sub",)), self.signed(remove=("iat",)),
            self.signed(remove=("exp",)), self.signed({"sub": "not-an-object-id"}),
            self.signed({"sub": 123}), self.signed({"sub": ""}),
            self.signed({"iat": now + 100, "exp": now + 200}),
            self.signed({"iat": str(now)}), self.signed({"exp": str(now + 100)}),
            self.signed({"iat": True}), self.signed({"iat": now, "exp": now}),
            self.signed(algorithm="HS384"),
        ]
        for index, token in enumerate(tokens):
            with self.subTest(case=index):
                await self.assert_unauthorized("Bearer " + token)
        self.collection.find_one.assert_not_awaited()

    async def test_deleted_user_is_401(self):
        self.collection.find_one.return_value = None
        await self.assert_unauthorized("Bearer " + self.signed())

    async def test_me_database_failure_is_safe_503(self):
        self.collection.find_one.side_effect = ConnectionFailure("private-driver-detail")
        status, body, _ = await request("GET", "/api/auth/me", authorization="Bearer " + self.signed())
        self.assertEqual(status, 503)
        self.assertNotIn("private-driver-detail", json.dumps(body))

    async def test_missing_jwt_configuration_is_safe_503(self):
        with patch("auth.load_jwt_settings", side_effect=ConfigurationError("private-setting")):
            status, body, _ = await self.login()
            self.assertEqual(status, 503)
            self.assertNotIn("private-setting", json.dumps(body))
        self.collection.find_one.assert_not_awaited()


class OpenAPISecurityTests(unittest.TestCase):
    def test_bearer_security_only_protects_me(self):
        schema = app.openapi()
        schemes = schema["components"]["securitySchemes"]
        self.assertEqual(schemes["HTTPBearer"], {
            "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        })
        self.assertEqual(
            schema["paths"]["/api/auth/me"]["get"]["security"],
            [{"HTTPBearer": []}],
        )
        self.assertFalse(schema.get("security"))
        for path in ("/api/auth/login", "/api/auth/register"):
            self.assertFalse(schema["paths"][path]["post"].get("security"))

class JWTConfigurationTests(unittest.TestCase):
    def test_defaults_precedence_and_hidden_secret(self):
        secret = secrets.token_urlsafe(48)
        with patch("config.dotenv_values", return_value={"JWT_SECRET_KEY": "placeholder"}):
            with patch.dict(os.environ, {"JWT_SECRET_KEY": secret}, clear=True):
                settings = load_jwt_settings()
        self.assertEqual(settings.secret_key, secret)
        self.assertEqual(settings.algorithm, "HS256")
        self.assertEqual(settings.access_token_expire_minutes, 30)
        self.assertNotIn(secret, repr(settings))

    def test_invalid_configuration_never_echoes_values(self):
        secret = secrets.token_urlsafe(48)
        cases = [
            {"JWT_SECRET_KEY": ""}, {"JWT_SECRET_KEY": "tiny"},
            {"JWT_SECRET_KEY": "<replace-with-a-strong-random-secret>"},
            {"JWT_ALGORITHM": "none"}, {"JWT_ALGORITHM": "HS512"},
            {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "private-invalid-value"},
            {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "0"},
            {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "1441"},
        ]
        for values in cases:
            with self.subTest(fields=list(values)):
                with patch("config.dotenv_values", return_value={}):
                    with patch.dict(os.environ, {"JWT_SECRET_KEY": secret, **values}, clear=True):
                        with self.assertRaises(ConfigurationError) as error:
                            load_jwt_settings()
                self.assertNotIn(secret, str(error.exception))
                self.assertNotIn("private-invalid-value", str(error.exception))

    def test_configured_lifetime_and_subject(self):
        settings = JWTSettings(secrets.token_urlsafe(48), access_token_expire_minutes=7)
        subject = ObjectId()
        token = create_access_token(subject, settings)
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        self.assertEqual(claims["sub"], str(subject))
        self.assertEqual(claims["exp"] - claims["iat"], 420)


if __name__ == "__main__":
    unittest.main()
