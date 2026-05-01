"""Async Postgres connection pool + migration runner + audit repo."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import asyncpg

from app.config import get_settings
from app.utils.logger import configure_logging, get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=settings.pg_async_dsn,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_migrations() -> None:
    """Apply all .sql files in ./migrations in lexicographic order."""
    settings = get_settings()
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              filename TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in applied:
                continue
            log.info("applying_migration", file=path.name)
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(filename) VALUES ($1)", path.name
                )
    log.info("migrations_complete", dsn_host=settings.postgres_host)


# ---------- Audit helpers ----------

async def insert_audit_event(
    thread_id: str,
    event_type: str,
    *,
    node: str | None = None,
    requirement: str | None = None,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_event(thread_id, event_type, node, requirement, actor, payload)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb)
            """,
            thread_id,
            event_type,
            node,
            requirement,
            actor,
            json.dumps(payload or {}),
        )


async def fetch_audit_events(thread_id: str) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, thread_id, ts, event_type, node, requirement, actor, payload
            FROM audit_event
            WHERE thread_id = $1
            ORDER BY ts ASC, id ASC
            """,
            thread_id,
        )
        return [
            {
                "id": r["id"],
                "thread_id": r["thread_id"],
                "ts": r["ts"].isoformat(),
                "event_type": r["event_type"],
                "node": r["node"],
                "requirement": r["requirement"],
                "actor": r["actor"],
                "payload": r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}"),
            }
            for r in rows
        ]


async def upsert_thread(thread_id: str, title: str, content: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO rfp_thread(thread_id, title, content)
            VALUES ($1,$2,$3)
            ON CONFLICT (thread_id) DO UPDATE
              SET updated_at = now()
            """,
            thread_id,
            title,
            content,
        )


async def set_thread_status(thread_id: str, status: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE rfp_thread SET status=$1, updated_at=now() WHERE thread_id=$2",
            status,
            thread_id,
        )


# ---------- CLI: `python -m app.database migrate` ----------

def _main() -> None:
    configure_logging()
    if len(sys.argv) < 2:
        print("usage: python -m app.database migrate")
        sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "migrate":
        asyncio.run(_migrate_and_close())
    else:
        print(f"unknown command: {cmd}")
        sys.exit(2)


async def _migrate_and_close() -> None:
    try:
        await run_migrations()
    finally:
        await close_pool()


if __name__ == "__main__":
    _main()
