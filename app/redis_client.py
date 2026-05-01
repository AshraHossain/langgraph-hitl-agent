"""Thin wrapper around redis-py for interrupt/resume pub/sub.

Redis isn't strictly required for LangGraph's resume (the Postgres checkpointer
handles durability) but pub/sub gives us a fanout channel for UIs / webhooks
to learn the moment a thread hits an approval gate or finishes.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from app.config import get_settings

_client: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        settings = get_settings()
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _chan(thread_id: str) -> str:
    return f"hitl:thread:{thread_id}"


async def publish_event(thread_id: str, event: dict[str, Any]) -> None:
    r = await get_redis()
    await r.publish(_chan(thread_id), json.dumps(event))


async def subscribe(thread_id: str):
    """Async generator yielding events for a thread. Caller must close."""
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_chan(thread_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(_chan(thread_id))
        await pubsub.aclose()
