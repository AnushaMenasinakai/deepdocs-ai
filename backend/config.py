"""Small, explicit environment configuration; no connection at import time."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ENV_FILE = Path(__file__).resolve().parent / ".env"


class ConfigurationError(ValueError):
    """A safe, public configuration message that never includes input values."""


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = field(repr=False)
    mongodb_db_name: str = "deepdocs_ai"


def load_settings() -> Settings:
    # Process environment wins. Disable interpolation so secret values stay literal.
    try:
        values = {**dotenv_values(ENV_FILE, interpolate=False), **os.environ}
    except (OSError, UnicodeError):
        raise ConfigurationError("Unable to read backend/.env configuration.") from None

    uri = (values.get("MONGODB_URI") or "").strip()
    if not uri:
        raise ConfigurationError(
            "MONGODB_URI is required. Set it in the environment or backend/.env."
        )
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ConfigurationError("MONGODB_URI must be a MongoDB connection string.")

    name = (values.get("MONGODB_DB_NAME", "deepdocs_ai") or "").strip()
    if not name or any(char in name for char in '/\\. "$*<>:|?\x00'):
        raise ConfigurationError("MONGODB_DB_NAME must be a valid, non-empty database name.")
    return Settings(mongodb_uri=uri, mongodb_db_name=name)


@dataclass(frozen=True)
class JWTSettings:
    secret_key: str = field(repr=False)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30


def load_jwt_settings() -> JWTSettings:
    # Kept separate so missing JWT configuration does not disable registration/health.
    try:
        values = {**dotenv_values(ENV_FILE, interpolate=False), **os.environ}
    except (OSError, UnicodeError):
        raise ConfigurationError("Unable to read authentication configuration.") from None
    secret = values.get("JWT_SECRET_KEY") or ""
    if (
        len(secret.encode("utf-8")) < 32
        or not secret.strip()
        or secret.startswith("<")
    ):
        raise ConfigurationError("JWT_SECRET_KEY requires a strong random secret of at least 32 bytes.")
    algorithm = values.get("JWT_ALGORITHM", "HS256")
    if algorithm != "HS256":
        raise ConfigurationError("JWT_ALGORITHM must be HS256.")
    try:
        minutes = int(values.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    except (ValueError, TypeError):
        raise ConfigurationError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer from 1 to 1440.") from None
    if not 1 <= minutes <= 1440:
        raise ConfigurationError("JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer from 1 to 1440.")
    return JWTSettings(secret, algorithm, minutes)
