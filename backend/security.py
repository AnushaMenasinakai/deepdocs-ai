"""Argon2id password hashing and verification, isolated from HTTP routes."""
from functools import lru_cache
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type
from starlette.concurrency import run_in_threadpool

# Argon2id: 64 MiB, three iterations, four lanes, fresh library-generated salt.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=4,
    hash_len=32, salt_len=16, type=Type.ID,
)


async def hash_password(password: str) -> str:
    # CPU/memory-intensive work must not block FastAPI's event loop.
    return await run_in_threadpool(_hasher.hash, password)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    # A missing account still performs password verification, reducing timing differences.
    return _hasher.hash(secrets.token_urlsafe(32))


async def verify_password(password: str, password_hash: str | None) -> bool:
    def verify():
        candidate = password_hash if isinstance(password_hash, str) else _dummy_hash()
        try:
            valid = _hasher.verify(candidate, password)
            return valid and isinstance(password_hash, str)
        except (VerificationError, InvalidHashError, UnicodeError):
            return False

    return await run_in_threadpool(verify)
