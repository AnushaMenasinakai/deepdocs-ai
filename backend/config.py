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
