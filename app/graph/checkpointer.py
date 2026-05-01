"""AsyncPostgresSaver factory.

LangGraph's Postgres checkpointer manages schema itself (`checkpoints`,
`checkpoint_writes`, `checkpoint_blobs`). Call `setup()` once — it's idempotent.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import get_settings


@asynccontextmanager
async def open_checkpointer():
    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(settings.pg_async_dsn) as saver:
        await saver.setup()
        yield saver


# For use inside FastAPI lifespan where we need a single long-lived saver.
_saver_cm = None
_saver: AsyncPostgresSaver | None = None


async def start_checkpointer() -> AsyncPostgresSaver:
    global _saver_cm, _saver
    if _saver is None:
        settings = get_settings()
        _saver_cm = AsyncPostgresSaver.from_conn_string(settings.pg_async_dsn)
        _saver = await _saver_cm.__aenter__()
        await _saver.setup()
    return _saver


async def stop_checkpointer() -> None:
    global _saver_cm, _saver
    if _saver_cm is not None:
        await _saver_cm.__aexit__(None, None, None)
    _saver_cm = None
    _saver = None


def current_checkpointer() -> AsyncPostgresSaver:
    if _saver is None:
        raise RuntimeError("checkpointer not started — call start_checkpointer() first")
    return _saver
