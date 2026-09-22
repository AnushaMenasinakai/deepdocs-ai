"""Password hashing only. No login or password verification endpoint."""
from argon2 import PasswordHasher
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
